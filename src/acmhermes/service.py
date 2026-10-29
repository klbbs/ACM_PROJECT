"""Session-authenticated optional API; per-user Jobs supplies isolation and budgets."""
import asyncio
import json
import re
import shutil

from fastapi import Header, HTTPException
from acmweb.catalog import recommendation_candidates, refresh_recommendation_sessions
from acmweb.codex_bridge import AIError
from acmweb.difficulty_estimates import cf_reference, estimate, overlaps
from acmweb.training_topics import TOPICS, matches_topic
from .models import Choice, Draw, SolutionReport, candidate_choice

MAX_RUN_SECONDS = 600


class Hermes:
    def __init__(self, app):
        self.app = app
        self.db, self.ai = app.state.database, app.state.ai
        self.jobs, self.accounts = app.state.jobs, app.state.account_config
        # An upgrade still honors the previous promise that private screenshots
        # do not survive process restarts. Never create new artifact directories.
        legacy_directory = app.state.settings.data_dir/'hermes-artifacts'
        if legacy_directory.is_dir() and not legacy_directory.is_symlink():
            for child in legacy_directory.iterdir():
                if re.fullmatch(r'[a-f0-9]{32}', child.name):
                    shutil.rmtree(child, ignore_errors=True)

    def pool(self, request, preferred_ids=None):
        low, high = request.score_range
        reference = cf_reference(self.db)
        topic = TOPICS.get(request.topic or '')
        methods = self.app.state.topic_feasibility.approved_map(topic['id']) if topic else {}
        def eligible(p):
            return overlaps(estimate(p, reference), low, high) and (
                not request.topic or ((matches_topic(p,topic) or p['id'] in methods) if topic else
                    request.topic.casefold() in {str(t).casefold() for t in p.get('tags', [])}))
        rows = recommendation_candidates(self.db, self.accounts, low, high, limit=60, predicate=eligible,
                                         preferred_ids=preferred_ids)
        return [{**row,'score_evidence':estimate(row,reference),
                 **({'topic_method_evidence':methods[row['id']]} if row['id'] in methods else {})} for row in rows]

    async def run(self, payload):
        async def pipeline():
            request = Draw.model_validate(payload['request'])
            topic = TOPICS.get(request.topic or '')
            topic_context = {'id':topic['id'],'title':topic['title']} if topic else request.topic
            selection = self.ai.selection()
            if not selection['ready'] or (selection['channel'],selection['model']) != (payload['provider'],payload['model']):
                raise AIError('当前默认 AI 配置已变化或不可用，请重新发起 Hermes 请求')
            fingerprint = self.ai.configuration_fingerprint()
            def guard():
                if self.ai.configuration_fingerprint() != fingerprint:
                    raise AIError('Hermes 生成期间 AI 配置已变化，未交付混用模型的结果；请重新发起')
            await refresh_recommendation_sessions(self.accounts)
            guard()
            pool = self.pool(request)
            if not pool:
                raise AIError('没有符合分数区间、题型、日期和已登录平台条件的未完成题；请同步平台题库并检测登录，不会放宽条件或编造题目')
            chosen = await self.ai.complete('hermes_selection',selection['channel'],selection['model'],
                {'candidates':pool,'score_range':list(request.score_range),'topic':topic_context},candidate_choice(p['id'] for p in pool))
            guard()
            choice = Choice.model_validate(chosen.data)
            candidate = next((p for p in pool if p['id'] == choice.problem_id),None)
            if not candidate:
                raise AIError('AI 选择了候选列表之外的题目，未生成题面或报告')
            reports = self.app.state.selection_reports
            statement, statement_hash = await reports._read(choice.problem_id)
            guard()
            response = await self.ai.complete('hermes_solution_report',selection['channel'],selection['model'],
                {'problem_id':choice.problem_id,'statement':statement,'score_range':list(request.score_range),
                 'topic':topic_context,'difficulty_evidence':candidate['score_evidence'],
                 'topic_method_evidence':candidate.get('topic_method_evidence')},SolutionReport)
            guard()
            report = SolutionReport.model_validate(response.data).model_dump()
            if report['problem_id'] != choice.problem_id or not report['suitable']:
                raise AIError('完整题面阅读后未确认符合要求，未交付该题；不会自动付费换题')
            if re.search(r'```|#include|\bint\s+main\s*\(|public\s+static\s+void\s+main',json.dumps(report)):
                raise AIError('报告包含完整程序，未交付；本接口只返回解题分析报告')
            # Revalidate eligibility and the precise full-statement fingerprint
            # after all paid work; never silently replace a changed problem.
            await refresh_recommendation_sessions(self.accounts)
            guard()
            if choice.problem_id not in {p['id'] for p in self.pool(request,[choice.problem_id])}:
                raise AIError('题目资格已变化（登录、日期、已完成或对战），未交付结果')
            if reports._cached(choice.problem_id)[1] != statement_hash:
                raise AIError('生成期间题面已变化，未交付不匹配的题目说明或报告')
            explanation = report.pop('problem_explanation')
            # Samples come from this exact official statement, not from model
            # paraphrasing. The explanation is part of the private AI report,
            # not a separate full-page translation or a shared translation.
            explanation.update(problem_id=choice.problem_id, language='zh-CN',
                original_url=statement['url'], constraints=list(report['constraints']),
                samples=[dict(sample) for sample in statement['samples']])
            return {'format':'text','problem':candidate,'selection_reason':choice.reason,
                    'problem_explanation':explanation,'report':report,'report_provenance':response.provenance,
                    'notice':'中文题目说明及完整解题报告由 AI 静态阅读生成，可能有误，请结合原题核对；报告含剧透，未运行或提交代码，不计签到。'}
        return await asyncio.wait_for(pipeline(),MAX_RUN_SECONDS)

    def job(self,jid):
        with self.db.connect() as c:
            row=c.execute("SELECT * FROM web_jobs WHERE id=? AND kind='hermes_draw'",(jid,)).fetchone()
        if not row:
            raise HTTPException(404,'找不到本人的 Hermes 任务')
        value=dict(row)
        value['result']=json.loads(value['result']) if value['result'] else None
        result=value['result']
        if result and 'screenshots' in result:
            for key in ('screenshots','artifact_id','expires_at','artifacts_expired','translation_provenance','image_links'):
                result.pop(key,None)
            result.update(format='legacy_report', problem_explanation=None,
                notice='此历史任务仅保留原解题报告，尚未生成新版中文题目说明；请结合原题阅读。截图功能已移除，不会自动重新调用 AI。')
        return value


def register(app):
    service=Hermes(app)
    app.state.hermes=service
    app.state.jobs.run_hermes=service.run

    @app.get('/api/hermes/capabilities')
    async def capabilities():
        return {'enabled':True,'output_format':'text',
                'score_range':{'minimum':800,'maximum':3500,'reference':'CF rating；其他平台使用已注明来源的区间估计，未知分数排除'},
                'topics':[{'id':t['id'],'title':t['title']} for t in TOPICS.values()],
                'auth':'使用本人 ACM Lab 会话；多人模式的写请求需 X-ACM-Request 和 CSRF 校验',
                'spoiler':'本接口明确生成完整解题报告，调用即请求获取该题分析；不会自动交题或运行代码'}

    @app.post('/api/hermes/draw')
    async def draw(payload:Draw,idempotency_key:str=Header(min_length=32,max_length=32,pattern=r'^[a-f0-9]{32}$')):
        selection=service.ai.selection()
        if not selection['ready']:
            raise HTTPException(400,'请先在连接与设置保存可用的默认 AI 模型')
        return service.jobs.start('hermes_draw',{'request':payload.model_dump(),'provider':selection['channel'],'model':selection['model']},request_key=idempotency_key)

    @app.get('/api/hermes/draws/{jid}')
    def status(jid:str):
        return service.job(jid)

    @app.get('/api/hermes/requests/{request_key}')
    def request_status(request_key:str):
        with service.db.connect() as c:
            row=c.execute("SELECT job_id FROM web_job_requests WHERE request_key=? AND kind='hermes_draw'",(request_key,)).fetchone()
        if not row:
            raise HTTPException(404,'找不到本人的 Hermes 请求')
        return service.job(row['job_id'])

    @app.get('/api/hermes/draws/{jid}/screenshots/{index}')
    def screenshot(jid:str,index:int):
        service.job(jid)  # Authenticate ownership before explaining deprecation.
        raise HTTPException(410,'截图功能已移除，请读取本任务的中文题目说明和解题报告')

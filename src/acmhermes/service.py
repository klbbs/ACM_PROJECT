"""Session-authenticated optional API; per-user Jobs supplies isolation and budgets."""
import asyncio
import json
import re
import shutil
import html
import time
import uuid
from typing import Literal

from fastapi import Header, HTTPException, Query
from fastapi.responses import FileResponse
from acmweb.catalog import recommendation_candidates, refresh_recommendation_sessions
from acmweb.codex_bridge import AIError
from acmweb.difficulty_estimates import cf_reference, estimate, overlaps
from acmweb.training_topics import TOPICS, matches_topic
from .models import Choice, ControlRequest, Draw, SolutionReport, candidate_choice
from .control import TaskControl

MAX_RUN_SECONDS = 600
IMAGE_TTL = 1800


class Hermes:
    def __init__(self, app):
        self.app = app
        self.db, self.ai = app.state.database, app.state.ai
        self.jobs, self.accounts = app.state.jobs, app.state.account_config
        self.control = TaskControl(self.jobs)
        self.jobs.hermes_control = self.control
        self.image_renderer = None
        self.directory = app.state.settings.data_dir/'hermes-artifacts'
        self.timers = {}
        # Private screenshots do not survive process restarts. Text requests
        # never create artifact directories.
        legacy_directory = app.state.settings.data_dir/'hermes-artifacts'
        if legacy_directory.is_dir() and not legacy_directory.is_symlink():
            for child in legacy_directory.iterdir():
                if re.fullmatch(r'[a-f0-9]{32}', child.name):
                    shutil.rmtree(child, ignore_errors=True)

    def renderer(self):
        # Only the image endpoint loads optional renderer dependencies.
        if self.image_renderer is None:
            from .render import ScreenshotRenderer
            self.image_renderer = ScreenshotRenderer()
        return self.image_renderer

    def expire(self,rid):
        timer=self.timers.pop(rid,None)
        if timer:
            timer.cancel()
        shutil.rmtree(self.directory/rid,ignore_errors=True)

    def close(self):
        for rid in list(self.timers):
            self.expire(rid)

    async def sync_history(self):
        from .history import sync_history
        return await sync_history(self.db,self.accounts)

    @staticmethod
    def image_statement(explanation):
        # Render the same AI question explanation as the text endpoint, not the
        # full solution report and not another paid full-page translation.
        parts=[]
        for title,key in (('题目要你做什么','description'),('输入','input_format'),('输出','output_format')):
            parts.append('<h2>'+title+'</h2><p>'+html.escape(explanation[key]).replace('\n','<br>')+'</p>')
        for title,key in (('约束','constraints'),('注意事项','notes')):
            if explanation[key]:
                parts.append('<h2>'+title+'</h2><ul>'+''.join('<li>'+html.escape(s)+'</li>' for s in explanation[key])+'</ul>')
        parts.append('<p>原题：'+html.escape(explanation['original_url'])+'</p>')
        return {'title':explanation['title'],'body_html':''.join(parts),'samples':explanation['samples']}

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

    async def run(self, payload, *, job_id=None):
        artifacts=[]
        async def pipeline():
            self.control.stage(job_id,'validating')
            request = Draw.model_validate(payload['request'])
            output_format=payload.get('output_format','text')
            if output_format not in {'text','image'}:
                raise AIError('Hermes 输出方式无效')
            topic = TOPICS.get(request.topic or '')
            topic_context = {'id':topic['id'],'title':topic['title']} if topic else request.topic
            selection = self.ai.selection()
            if not selection['ready'] or (selection['channel'],selection['model']) != (payload['provider'],payload['model']):
                raise AIError('当前默认 AI 配置已变化或不可用，请重新发起 Hermes 请求')
            fingerprint = self.ai.configuration_fingerprint()
            account_revision = self.accounts.revision()
            synced_bindings = None
            def guard():
                self.control.check(job_id)
                if self.ai.configuration_fingerprint() != fingerprint:
                    raise AIError('Hermes 生成期间 AI 配置已变化，未交付混用模型的结果；请重新发起')
                if self.accounts.revision() != account_revision:
                    raise AIError('Hermes 生成期间平台配置已变化，未继续抽题或交付旧账号结果；请重新发起')
                if synced_bindings is not None:
                    from .history import bindings
                    if not set(bindings(self.accounts)).issubset(synced_bindings):
                        raise AIError('平台登录状态在历史同步后已变化，新验证账号尚未同步提交；请重新抽题，不会使用未同步平台选题')
            if output_format=='image':
                self.control.stage(job_id,'preparing_image')
                await self.renderer().preflight()
                guard()
            self.control.stage(job_id,'checking_sessions')
            await refresh_recommendation_sessions(self.accounts)
            guard()
            self.control.stage(job_id,'syncing_submissions')
            history=await self.sync_history()
            synced_bindings={(row['platform'],row['handle']) for row in history['accounts'] if row.get('complete')}
            guard()
            # A long initial full-history read may outlive login evidence.
            await refresh_recommendation_sessions(self.accounts)
            guard()
            pool = self.pool(request)
            if not pool:
                raise AIError('没有符合分数区间、题型、日期和已登录平台条件的未完成题；请同步平台题库并检测登录，不会放宽条件或编造题目')
            self.control.stage(job_id,'selecting')
            chosen = await self.ai.complete('hermes_selection',selection['channel'],selection['model'],
                {'candidates':pool,'score_range':list(request.score_range),'topic':topic_context},candidate_choice(p['id'] for p in pool))
            guard()
            choice = Choice.model_validate(chosen.data)
            candidate = next((p for p in pool if p['id'] == choice.problem_id),None)
            if not candidate:
                raise AIError('AI 选择了候选列表之外的题目，未生成题面或报告')
            reports = self.app.state.selection_reports
            self.control.stage(job_id,'reading_statement')
            statement, statement_hash = await reports._read(choice.problem_id)
            guard()
            self.control.stage(job_id,'generating_report')
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
            self.control.stage(job_id,'revalidating')
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
            result={'format':output_format,'problem':candidate,'selection_reason':choice.reason,
                    'problem_explanation':explanation,'report':report,'report_provenance':response.provenance,
                    'history_sync':history,
                    'notice':'中文题目说明及完整解题报告由 AI 静态阅读生成，可能有误，请结合原题核对；报告含剧透，未运行或提交代码，不计签到。'}
            if output_format=='image':
                self.control.stage(job_id,'rendering_image')
                rid=uuid.uuid4().hex
                artifacts.append(rid)
                self.directory.mkdir(parents=True,mode=0o700,exist_ok=True)
                try:
                    files,_=await self.renderer().render(self.image_statement(explanation),self.directory/rid)
                    guard()
                    self.timers[rid]=asyncio.get_running_loop().call_later(IMAGE_TTL,self.expire,rid)
                    result.update(artifact_id=rid,expires_at=int(time.time())+IMAGE_TTL,screenshots=files)
                except BaseException:
                    self.expire(rid)
                    raise
            guard()
            return result
        try:
            return await asyncio.wait_for(pipeline(),MAX_RUN_SECONDS)
        except BaseException:
            for rid in artifacts:
                self.expire(rid)
            raise

    def job(self,jid):
        value=self.control.row(jid)
        value['result']=json.loads(value['result']) if value['result'] else None
        result=value['result']
        if result and result.get('format')=='image':
            result['screenshots']=[{'index':i,'url':f'/api/hermes/draws/{jid}/screenshots/{i}'} for i,_ in enumerate(result['screenshots'])]
            result['artifacts_expired']=time.time()>=result['expires_at']
        elif result and 'screenshots' in result:
            for key in ('screenshots','artifact_id','expires_at','artifacts_expired','translation_provenance','image_links'):
                result.pop(key,None)
            result.update(format='legacy_report', problem_explanation=None,
                notice='此历史任务仅保留原解题报告，尚未生成新版中文题目说明；请结合原题阅读。历史截图已清理，不会自动重新调用 AI。')
        return value


def register(app):
    service=Hermes(app)
    app.state.hermes=service
    app.state.jobs.run_hermes=service.run

    @app.get('/api/hermes/capabilities')
    async def capabilities():
        return {'enabled':True,'output_format':'text','output_formats':['text','image'],
                'endpoints':{'text':'/api/hermes/draw','image':'/api/hermes/draw/image',
                    'cancel':'/api/hermes/draws/{job_id}/cancel','cancel_request':'/api/hermes/requests/{request_key}/cancel',
                    'stop_all':'/api/hermes/stop','list':'/api/hermes/draws','readiness':'/api/hermes/readiness','candidates':'/api/hermes/candidates'},
                'score_range':{'minimum':800,'maximum':3500,'reference':'CF rating；其他平台使用已注明来源的区间估计，未知分数排除'},
                'topics':[{'id':t['id'],'title':t['title']} for t in TOPICS.values()],
                'auth':'使用本人 ACM Lab 会话；多人模式的写请求需 X-ACM-Request 和 CSRF 校验',
                'spoiler':'本接口明确生成完整解题报告，调用即请求获取该题分析；不会自动交题或运行代码'}

    def enqueue(payload,key,output_format):
        selection=service.ai.selection()
        if not selection['ready']:
            raise HTTPException(400,'请先在连接与设置保存可用的默认 AI 模型')
        data={'request':payload.model_dump(),'provider':selection['channel'],'model':selection['model']}
        # Keep the old text request fingerprint compatible with existing keys.
        if output_format=='image':
            data['output_format']='image'
        value=service.jobs.start('hermes_draw',data,request_key=key)
        service.control.track(value['id'])
        return value

    @app.post('/api/hermes/draw')
    async def draw(payload:Draw,idempotency_key:str=Header(min_length=32,max_length=32,pattern=r'^[a-f0-9]{32}$')):
        return enqueue(payload,idempotency_key,'text')

    @app.post('/api/hermes/draw/image')
    async def draw_image(payload:Draw,idempotency_key:str=Header(min_length=32,max_length=32,pattern=r'^[a-f0-9]{32}$')):
        return enqueue(payload,idempotency_key,'image')

    @app.get('/api/hermes/draws')
    def listing(status:Literal['queued','running','completed','failed','interrupted']|None=None,
                limit:int=Query(default=20,ge=1,le=100),offset:int=Query(default=0,ge=0,le=10000)):
        return service.control.listing(status,limit,offset)

    @app.get('/api/hermes/readiness')
    def readiness():
        try:
            selection=service.ai.selection()
            ready=selection['ready']
            reason='' if ready else '请在连接与设置保存可用的默认 AI'
        except AIError as error:
            ready,reason=False,str(error)
        return {'enabled':True,'ai_ready':ready,'reason':reason,'active_jobs':service.control.active_ids(),
                'image_requires_optional_dependencies':True,'notice':'只读配置状态，不调用模型或检测平台登录'}

    @app.get('/api/hermes/candidates')
    def preview(minimum:int=Query(ge=800,le=3500),maximum:int=Query(ge=800,le=3500),topic:str|None=Query(default=None,min_length=1,max_length=80)):
        if minimum>maximum or topic is not None and not topic.strip():
            raise HTTPException(422,'请提供有效的分数区间和题型')
        items=service.pool(Draw(score_range=(minimum,maximum),topic=topic))
        return {'items':items,'count':len(items),'limit':60,'cache_only':True,
                'notice':'仅预览当前缓存与登录证据，非全库总数；不调用 AI、不检测登录、不锁定候选'}

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

    @app.post('/api/hermes/draws/{jid}/cancel')
    async def cancel(jid:str,payload:ControlRequest):
        return service.control.cancel(jid)

    @app.post('/api/hermes/requests/{request_key}/cancel')
    async def cancel_request(request_key:str,payload:ControlRequest):
        job=request_status(request_key)
        return service.control.cancel(job['id'])

    @app.post('/api/hermes/stop')
    async def stop(payload:ControlRequest):
        return service.control.stop_all()

    @app.get('/api/hermes/draws/{jid}/screenshots/{index}')
    def screenshot(jid:str,index:int):
        result=service.job(jid)['result']
        if not result or result.get('format')!='image':
            raise HTTPException(410,'此任务没有图片输出；文字说明和解题报告仍可读取')
        if index<0 or index>=len(result['screenshots']):
            raise HTTPException(404,'图片不存在')
        if result['artifacts_expired']:
            service.expire(result['artifact_id'])
            raise HTTPException(410,'题目图片已过期，文字说明和报告仍可读取')
        path=service.directory/result['artifact_id']/f'{index}.png'
        if not path.is_file():
            raise HTTPException(410,'图片已清理或服务已重启，文字说明和报告仍可读取')
        return FileResponse(path,media_type='image/png',filename=f'problem-{index+1}.png')

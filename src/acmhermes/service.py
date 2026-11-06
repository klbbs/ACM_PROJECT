"""Session-authenticated optional API; per-user Jobs supplies isolation and budgets."""
import asyncio
import json
import re
import secrets
import shutil
import html
import time
import uuid
from typing import Literal

from fastapi import Header, HTTPException, Query
from fastapi.responses import FileResponse
from acmweb.catalog import recommendation_candidates, refresh_recommendation_sessions, verified_platforms, _canonical
from acmweb.runtime_logs import emit_event
from acmweb.codex_bridge import AIError
from acmweb.difficulty_estimates import cf_reference, estimate, overlaps
from acmweb.training_topics import TOPICS
from .topics import MAX_QUERY_LENGTH, parse_topics, method_evidence, matches_topics, context as topic_context_for
from .models import Choice, ControlRequest, Draw, SolutionReport, candidate_choice
from .control import TaskControl
from .difficulty import score_candidate, selection_range

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

    async def ensure_catalog(self):
        from .catalog import ensure_catalog
        return await ensure_catalog(self.db,self.accounts)

    async def public_references(self, problem_id, statement):
        """Anonymous best-effort materials; failure never replaces the statement."""
        if not isinstance(statement, dict) or statement.get('id') != problem_id:
            return []
        from .references import gather, _url
        from acmweb.problem_sources import atcoder_contest
        try:
            platform, key = problem_id.split(':', 1)
            if platform == 'codeforces':
                contest = key.split('-')[0]
                base = 'https://codeforces.com'
                paths = {'editorial': r'/blog/entry/[1-9]\d{0,9}',
                    'accepted_public_code': rf'/(?:contest|gym)/{re.escape(contest)}/submission/[1-9]\d{{0,11}}'}
            elif platform == 'atcoder':
                contest = atcoder_contest(key)
                base = 'https://atcoder.jp'
                paths = {'editorial': rf'/contests/{re.escape(contest)}/editorial/[1-9]\d{{0,9}}',
                    'accepted_public_code': rf'/contests/{re.escape(contest)}/submissions/[1-9]\d{{0,11}}'}
            else:
                return []
            values = await gather(problem_id, statement)
            safe, remaining = [], 8000
            for value in values[:2] if isinstance(values, list) else []:
                if (not isinstance(value, dict) or value.get('problem_id') != problem_id or
                        value.get('kind') not in {'editorial', 'accepted_public_code'} or
                        any(not isinstance(value.get(key), str) or not value[key].strip()
                            for key in ('title', 'url', 'excerpt')) or
                        _url(base, value['url'], paths[value['kind']]) != value['url']):
                    continue
                if remaining <= 0:
                    break
                safe.append({key: value[key][:limit] for key, limit in
                    (('problem_id', 120), ('kind', 30), ('title', 200), ('url', 500), ('excerpt', 8000))})
                safe[-1]['excerpt'] = safe[-1]['excerpt'][:remaining]
                remaining -= len(safe[-1]['excerpt'])
                safe[-1]['limitations'] = [item[:300] for item in value.get('limitations', [])[:4] if isinstance(item, str)]
            return safe
        except Exception:
            # asyncio.CancelledError derives from BaseException: user stop and
            # the parent job deadline must continue propagating.
            return []

    async def refresh_evidence(self, request):
        from .selection_evidence import refresh_evidence
        return await refresh_evidence(self.db, self.accounts, self.pool(request, evidence_only=True),
                                      score_range=request.score_range)

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

    def pool(self, request, preferred_ids=None, *, evidence_only=False, qualified_contests=None, allowed_platforms=None):
        low, high = request.score_range
        lower, upper = selection_range(low, high)
        reference = cf_reference(self.db)
        topics = parse_topics(request.topic)
        methods = method_evidence(self, topics)
        excluded=self.control.memory.drawn_ids() | set(request.exclude_ids)
        scores = {}
        def score(p):
            if p['id'] not in scores:
                scores[p['id']] = (score_candidate(p, low, high) if p.get('platform') == 'codeforces'
                                   else estimate(p, reference))
            return scores[p['id']]
        def difficulty(p):
            if evidence_only:
                # Discovery may inspect a different official rating; only the
                # final pool must satisfy the requested composite estimate.
                return p.get('platform') == 'codeforces' and type(p.get('difficulty')) is int
            return score(p).get('eligible', overlaps(score(p), lower, upper))
        def eligible(p):
            if allowed_platforms is not None and p.get('platform') not in allowed_platforms:
                return False
            if (qualified_contests is not None and p.get('platform') == 'codeforces'
                    and p.get('contest_id') not in qualified_contests
                    and score(p).get('empirical_rating') is None):
                return False
            return _canonical(p['platform'],p['id']) not in excluded and matches_topics(p, topics, methods)
        if evidence_only:
            # A nominal-first sort followed by LIMIT would hide every other
            # rating in a large CF catalog. Reserve a separate discovery slice.
            rows = recommendation_candidates(self.db, self.accounts, low, high, limit=40, predicate=eligible,
                difficulty_predicate=lambda p: difficulty(p) and low <= p['difficulty'] <= high)
            rows += recommendation_candidates(self.db, self.accounts, low, high, limit=20, predicate=eligible,
                difficulty_predicate=lambda p: difficulty(p) and not low <= p['difficulty'] <= high)
        else:
            rows = recommendation_candidates(self.db, self.accounts, low, high, limit=60, predicate=eligible,
                                             preferred_ids=preferred_ids, difficulty_predicate=difficulty)
        return [{**row,'score_evidence':score(row),
                 **({'topic_method_evidence': (next(iter(methods[row['id']].values())) if len(methods[row['id']])==1
                     else {'by_topic':methods[row['id']]})} if row['id'] in methods else {})} for row in rows]

    @staticmethod
    def platform_weights(pool, recent_platforms=()):
        return {p:max(1, round(1000 / (1 + recent_platforms.count(p)) *
                              (.5 if recent_platforms and recent_platforms[0] == p else 1)))
                for p in sorted({row['platform'] for row in pool})}

    @staticmethod
    def choose(pool, recent_platforms=()):
        # Integer OS randomness, positive weight for every eligible candidate.
        # Gym bonus affects probability only, never its estimated difficulty.
        platforms = sorted({row['platform'] for row in pool})
        if len(platforms) > 1:
            by_platform = Hermes.platform_weights(pool, recent_platforms)
            weights = [by_platform[p] for p in platforms]
            position = secrets.randbelow(sum(weights))
            for platform, weight in zip(platforms, weights):
                if position < weight:
                    candidate, _ = Hermes.choose([row for row in pool if row['platform'] == platform])
                    return candidate, 'system_platform_balanced_random_without_replacement'
                position -= weight
        weights = [max(1, round(1000 * (1 + row['score_evidence'].get('priority_score', 0)))) for row in pool]
        if len(set(weights)) == 1:
            return secrets.choice(pool), 'system_random_without_replacement'
        position = secrets.randbelow(sum(weights))
        for row, weight in zip(pool, weights):
            if position < weight:
                return row, 'system_weighted_random_without_replacement'
            position -= weight
        raise AssertionError('invalid random weight')

    def recent_platforms(self):
        with self.db.connect() as db:
            rows=db.execute("SELECT result FROM web_jobs WHERE kind='hermes_draw' AND status='completed' AND result IS NOT NULL ORDER BY updated_at DESC,id DESC LIMIT 8").fetchall()
        result=[]
        for row in rows:
            try:
                platform=json.loads(row[0]).get('problem',{}).get('platform')
            except (ValueError,TypeError,AttributeError):
                continue
            if platform in {'codeforces','atcoder','luogu','nowcoder'}:
                result.append(platform)
        return result

    def diagnostics(self, request, allowed_platforms=None):
        from .pool_diagnostics import diagnose
        return diagnose(self,request,allowed_platforms)

    async def run(self, payload, *, job_id=None):
        artifacts=[]
        started=time.monotonic()
        async def pipeline():
            self.control.stage(job_id,'validating')
            request = Draw.model_validate(payload['request'])
            score_window = list(selection_range(*request.score_range))
            output_format=payload.get('output_format','text')
            if output_format not in {'text','image'}:
                raise AIError('Hermes 输出方式无效')
            requested_topics = parse_topics(request.topic)
            topic_context = topic_context_for(requested_topics)
            selection = self.ai.selection()
            if not selection['ready'] or (selection['channel'],selection['model']) != (payload['provider'],payload['model']):
                raise AIError('当前默认 AI 配置已变化或不可用，请重新发起 Hermes 请求')
            fingerprint = self.ai.configuration_fingerprint()
            account_revision = self.accounts.revision()
            synced_bindings = None
            known_bindings = None
            allowed_platforms = None
            fallbacks = []
            def note(platform, step, reason):
                fallbacks.append({'platform':platform,'stage':step,'reason':reason})
                emit_event(getattr(self.app.state,'event_log',None),'hermes.platform_skipped',
                    level='WARNING',job_id=job_id,platform=platform,failure_step=step,reason=reason)
            def guard():
                self.control.check(job_id)
                if self.ai.configuration_fingerprint() != fingerprint:
                    raise AIError('Hermes 生成期间 AI 配置已变化，未交付混用模型的结果；请重新发起')
                if self.accounts.revision() != account_revision:
                    raise AIError('Hermes 生成期间平台配置已变化，未继续抽题或交付旧账号结果；请重新发起')
                if synced_bindings is not None:
                    from .history import bindings
                    if not set(bindings(self.accounts)).issubset(known_bindings or synced_bindings):
                        raise AIError('平台登录状态在历史同步后已变化，新验证账号尚未同步提交；请重新抽题，不会使用未同步平台选题')
            async def renew_sessions(checkpoint):
                # Recheck at the exact boundaries that consume login evidence.
                # Catalog/standings work may outlive a five-minute check; never
                # stretch an expired timestamp or accept a negative result.
                summary = await refresh_recommendation_sessions(self.accounts, minimum_validity=60)
                verified = len(verified_platforms(self.accounts))
                emit_event(getattr(self.app.state, 'event_log', None), 'hermes.sessions_checked',
                    level='INFO' if verified else 'WARNING', job_id=job_id,
                    session_stage=checkpoint, verified_count=verified,
                    **({key: summary[key] for key in ('checked','busy','errors','skipped_fresh','skipped_cooldown')
                        if key in summary} if isinstance(summary, dict) else {}))
                guard()
                if not verified:
                    error = AIError('没有已验证登录的平台；本次登录检测尚未确认有效身份，可能是临时网络失败、平台限制或会话已过期。'
                                    '凭据未被删除；请等待检测冷却后重试，或到连接与设置查看检测结果')
                    error.reason = 'hermes_session_unavailable'
                    raise error
            if output_format=='image':
                self.control.stage(job_id,'preparing_image')
                await self.renderer().preflight()
                guard()
            self.control.stage(job_id,'checking_sessions')
            await renew_sessions('before_history')
            guard()
            self.control.stage(job_id,'syncing_submissions')
            history=await self.sync_history()
            synced_bindings={(row['platform'],row['handle']) for row in history['accounts'] if row.get('complete')}
            known_bindings={(row['platform'],row['handle']) for row in history['accounts']}
            failed_history={row['platform'] for row in history['accounts'] if not row.get('complete')}
            allowed_platforms={platform for platform,_ in synced_bindings}-failed_history
            for platform in sorted(failed_history):
                note(platform,'history','hermes_history_unavailable')
            guard()
            # A long initial full-history read may outlive login evidence.
            await renew_sessions('after_history')
            guard()
            self.control.stage(job_id,'syncing_catalog')
            if 'codeforces' in allowed_platforms:
                try:
                    catalog=await self.ensure_catalog()
                except AIError:
                    guard()
                    allowed_platforms.discard('codeforces')
                    catalog={'platform':'codeforces','status':'unavailable','reason':'hermes_catalog_unavailable'}
                    note('codeforces','catalog','hermes_catalog_unavailable')
            else:
                catalog={'platform':'codeforces','status':'not_required'}
            guard()
            await renew_sessions('before_evidence')
            self.control.stage(job_id,'checking_difficulty')
            contest_evidence = (await self.refresh_evidence(request) if 'codeforces' in allowed_platforms
                                else {'status':'not_required'})
            guard()
            await renew_sessions('before_selection')
            # Statistics improve known ratings; a finite standings sample must
            # not erase every otherwise valid CF task in favour of another OJ.
            pool = self.pool(request, allowed_platforms=allowed_platforms)
            if not pool:
                diagnostics=self.diagnostics(request,allowed_platforms)
                emit_event(getattr(self.app.state,'event_log',None),'hermes.pool_empty',level='WARNING',
                    job_id=job_id,candidate_count=diagnostics['candidate_count'],
                    cached_count=sum(row['cached_count'] for row in diagnostics['platforms']))
                lower, upper = selection_range(*request.score_range)
                topic_title = request.topic or '不限专题'
                suffix = '；故障平台已跳过' if fallbacks else ''
                raise AIError(f'当前可用平台中没有符合推测分数 {lower}～{upper}、{topic_title}、发布日期及排重条件的未完成且未推荐题{suffix}；已排除客户端记录，候选预览可查看各平台缓存与筛选原因')
            candidate_counts={platform:sum(p['platform']==platform for p in pool) for platform in sorted(allowed_platforms)}
            recent=self.recent_platforms()
            platform_weights=self.platform_weights(pool,recent)
            reports = self.app.state.selection_reports
            report_attempts = 0
            while pool:
                self.control.stage(job_id,'selecting')
                candidate, strategy = self.choose(pool, recent)
                platform=candidate['platform']
                self.control.stage(job_id,'reading_statement')
                try:
                    statement, statement_hash = await reports._read(candidate['id'])
                except AIError:
                    guard()
                    note(platform,'statement','hermes_statement_unavailable')
                    pool=[p for p in pool if p['platform']!=platform]
                    continue
                guard()
                # Public references overlap the existing selection call; never
                # start a separate paid AI summary or leave reads after a stop.
                reference_task = asyncio.create_task(self.public_references(candidate['id'], statement))
                try:
                    self.control.stage(job_id,'selecting')
                    chosen = await self.ai.complete('hermes_selection',selection['channel'],selection['model'],
                        {'candidates':[candidate],'score_range':score_window,'requested_score_range':list(request.score_range),'topic':topic_context},candidate_choice([candidate['id']]))
                    guard()
                    choice = Choice.model_validate(chosen.data)
                    if choice.problem_id != candidate['id']:
                        raise AIError('AI 返回的题号与本次随机抽取不一致，未生成题面或报告')
                    references = await reference_task
                    guard()
                finally:
                    if not reference_task.done():
                        reference_task.cancel()
                    try:
                        await reference_task
                    except (asyncio.CancelledError, Exception):
                        pass
                self.control.stage(job_id,'generating_report')
                # Provider failures are not OJ outages: do not blindly charge
                # again against the same broken model by changing the OJ.
                response = await self.ai.complete('hermes_solution_report',selection['channel'],selection['model'],
                    {'problem_id':choice.problem_id,'statement':statement,'score_range':score_window,'requested_score_range':list(request.score_range),
                     'topic':topic_context,'difficulty_evidence':candidate['score_evidence'],
                     'topic_method_evidence':candidate.get('topic_method_evidence'),
                     'brief_statistics':candidate.get('brief_statistics'),
                     'public_references':references},SolutionReport)
                guard()
                report_attempts += 1
                report = SolutionReport.model_validate(response.data).model_dump()
                if report['problem_id'] != choice.problem_id:
                    raise AIError('报告题号与本次抽取不一致，未交付结果')
                if not report['suitable']:
                    note(platform,'report','hermes_report_unsuitable')
                    pool=[p for p in pool if p['platform']!=platform]
                    if report_attempts >= 2 or time.monotonic()-started >= MAX_RUN_SECONDS-60:
                        break
                    continue
                if re.search(r'```|#include|\bint\s+main\s*\(|public\s+static\s+void\s+main',json.dumps(report)):
                    raise AIError('报告包含完整程序，未交付；本接口只返回解题分析报告')
                break
            else:
                raise AIError('已尝试本轮其他平台，均未取得可用的完整题面或合适报告；请稍后重试')
            if not report['suitable']:
                raise AIError('已跨平台尝试，仍未确认适用题目；达到报告次数或总时限预算，已停止调用模型')
            # Revalidate eligibility and the precise full-statement fingerprint
            # after all paid work; never silently replace a changed problem.
            self.control.stage(job_id,'revalidating')
            await renew_sessions('before_delivery')
            guard()
            if choice.problem_id not in {p['id'] for p in self.pool(request,[choice.problem_id], allowed_platforms=allowed_platforms)}:
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
                    'report_references':[{key:reference[key] for key in ('problem_id','kind','title','url','limitations') if key in reference} for reference in references],
                    'history_sync':history,'catalog_sync':catalog,'contest_evidence_sync':contest_evidence,
                    'selection_strategy':strategy, 'requested_score_range':list(request.score_range),'effective_score_range':score_window,
                    'platform_candidate_counts':candidate_counts,
                    'platform_weights':platform_weights,'platform_fallbacks':fallbacks,'report_attempts':report_attempts,
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
        value['delivery']=self.control.deliveries.status(jid)
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
    from .delivery import register as register_delivery, PROTOCOL, ACK_TIMEOUT
    register_delivery(app, service)

    @app.get('/api/hermes/capabilities')
    async def capabilities():
        return {'enabled':True,'output_format':'text','output_formats':['text','image'],
                'endpoints':{'text':'/api/hermes/draw','image':'/api/hermes/draw/image',
                    'cancel':'/api/hermes/draws/{job_id}/cancel','cancel_request':'/api/hermes/requests/{request_key}/cancel',
                    'stop_all':'/api/hermes/stop','list':'/api/hermes/draws','readiness':'/api/hermes/readiness','candidates':'/api/hermes/candidates',
                    'problem_memory':'/api/hermes/problem-memory',
                    'delivery':'/api/hermes/draws/{job_id}/delivery',
                    'delivery_stream':'/api/hermes/draws/{job_id}/delivery/stream',
                    'delivery_ack':'/api/hermes/draws/{job_id}/delivery/ack'},
                'delivery_protocol':{'name':PROTOCOL,'ack_timeout_seconds':ACK_TIMEOUT,
                    'semantics':'at_least_once','ack_after':'durable_consumer_commit',
                    'notice':'结果重发使用同一消息，不重新抽题；调用方必须开启交付流并在本地去重、落盘后确认。'},
                'selection_strategy':'system_platform_balanced_random_without_replacement','exclude_ids_limit':10000,
                'platform_policy':{'base_weight':'equal_per_eligible_platform','recent_window':8,
                    'max_report_attempts':2,'notice':'先抽平台再抽题，近期重复平台降权；故障平台本轮跳过，不放宽已做题和登录要求。'},
                'difficulty_policy':{'empirical_weight':0.55,'official_weight':0.45,'gym_priority_bonus':0.12,
                    'single_target_tolerance':100,'rating_precision':'weighted_value_2_decimals',
                    'minimum_statistics_population':100,'notice':'统计充分时按综合估计筛选；Gym 加分只影响概率，未知难度排除，现场数据不是官方 rating。'},
                'score_range':{'minimum':800,'maximum':3500,'reference':'CF 综合训练估计；统计不足时保留官方 rating，其他平台用注明来源的估计，未知分数排除'},
                'topics':[{'id':t['id'],'title':t['title']} for t in TOPICS.values()],
                'topic_query':{'parameter':'topic','separators':[',','，','&','|'],'default_operator':'AND','precedence':['& / comma','|'],'parentheses':False,'maximum_categories':8,
                    'example':'dp,博弈论','notice':'支持专题 ID、名称、精确别名及 CF 标签，自动去重；空项不允许。'},
                'auth':'使用本人 ACM Lab 会话；多人模式的写请求需 X-ACM-Request 和 CSRF 校验',
                'spoiler':'本接口明确生成完整解题报告，调用即请求获取该题分析；不会自动交题或运行代码'}

    def enqueue(payload,key,output_format):
        selection=service.ai.selection()
        if not selection['ready']:
            raise HTTPException(400,'请先在连接与设置保存可用的默认 AI 模型')
        request=payload.model_dump()
        if not request['exclude_ids']:
            request.pop('exclude_ids')  # Keep existing idempotency fingerprints.
        data={'request':request,'provider':selection['channel'],'model':selection['model']}
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
    def preview(minimum:int=Query(ge=800,le=3500),maximum:int=Query(ge=800,le=3500),topic:str|None=Query(default=None,min_length=1,max_length=MAX_QUERY_LENGTH)):
        if minimum>maximum or topic is not None and not topic.strip():
            raise HTTPException(422,'请提供有效的分数区间和题型')
        try:
            request=Draw(score_range=(minimum,maximum),topic=topic)
        except ValueError:
            raise HTTPException(422,'题型最多 8 类，以 &/逗号取交集、| 取并集，不支持括号或空项') from None
        items=service.pool(request)
        return {'items':items,'count':len(items),'limit':60,'cache_only':True,
                'diagnostics':service.diagnostics(request),'topic_query':topic_context_for(parse_topics(topic)),
                'requested_score_range':[minimum,maximum],'effective_score_range':list(selection_range(minimum,maximum)),
                'platform_candidate_counts':{p:sum(row['platform']==p for row in items) for p in verified_platforms(service.accounts)},
                'platform_weights':service.platform_weights(items,service.recent_platforms()),
                'notice':'仅预览当前缓存与登录证据，非全库总数；不调用 AI、不检测登录、不锁定候选'}

    @app.get('/api/hermes/problem-memory')
    def memory(after:str|None=Query(default=None,max_length=200),limit:int=Query(default=500,ge=1,le=1000),
               revision:str|None=Query(default=None,min_length=64,max_length=64,pattern=r'^[a-f0-9]{64}$')):
        return service.control.memory.read(after,limit,revision)

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

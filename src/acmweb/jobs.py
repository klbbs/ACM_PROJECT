import asyncio
import json
import hashlib
import logging
import time
import threading
import uuid
from argparse import Namespace
from fastapi import HTTPException

from . import analytics
from .catalog import refresh_catalog, recommendation_candidates, recommendation_status, refresh_recommendation_sessions
from .codex_bridge import AIError
from .runtime_logs import emit_event, known_failure_reason
from .selection_report_links import prepare_selection_reports
from ojcrawler.config import load
from ojcrawler.cli import crawl

log=logging.getLogger(__name__)


class Jobs:
    def __init__(self,database,settings,ai,accounts=None):
        self.db,self.settings,self.ai=database,settings,ai
        self.tasks={}
        self.accounts=accounts
        self.guard=None
        self.event_log=None
        self.selection_reports=None
        with self.db.connect() as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS web_job_requests (
                request_key TEXT PRIMARY KEY, kind TEXT NOT NULL, fingerprint TEXT NOT NULL,
                job_id TEXT NOT NULL)''')

    def list(self):
        with self.db.connect() as db:
            rows=db.execute("SELECT * FROM web_jobs ORDER BY created_at DESC LIMIT 30").fetchall()
        return [{**dict(r),"result":json.loads(r["result"]) if r["result"] else None} for r in rows]

    def start(self,kind,payload=None,*,request_key=None):
        fingerprint = (hashlib.sha256(json.dumps(payload or {}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                       if request_key is not None else None)
        def replay(connection):
            if request_key is None:
                return None
            previous = connection.execute('SELECT * FROM web_job_requests WHERE request_key=?', (request_key,)).fetchone()
            if previous:
                if previous['kind'] != kind or previous['fingerprint'] != fingerprint:
                    raise HTTPException(409, '此请求标识已用于另一份生成配置，请重新发起')
                job = connection.execute('SELECT id,status FROM web_jobs WHERE id=?', (previous['job_id'],)).fetchone()
                if not job:
                    raise HTTPException(409, '原生成任务已归档，请先查看已保存题单，不会自动再次生成')
                return dict(job)
            return None
        with self.db.connect() as connection:
            previous = replay(connection)
        if previous:
            return previous
        if self.guard:
            self.guard()
        if sum(not task.done() for _,task in self.tasks.values()) >= 2:
            raise AIError('当前用户任务已满，请等待完成')
        if any(k==kind and not task.done() for k,task in self.tasks.values()):
            raise AIError("同类任务正在执行，请等待完成")
        job_id=uuid.uuid4().hex
        now=int(time.time())
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = replay(db)
            if previous:
                return previous
            db.execute("INSERT INTO web_jobs VALUES(?,?,?,?,?,?,?)",(job_id,kind,"queued",now,now,None,None))
            if request_key is not None:
                db.execute('INSERT INTO web_job_requests VALUES(?,?,?,?)', (request_key,kind,fingerprint,job_id))
        emit_event(self.event_log, 'job.queued', job_id=job_id, kind=kind, job_status='queued')
        task=asyncio.create_task(self._run(job_id,kind,payload or {}))
        self.tasks[job_id]=(kind,task)
        task.add_done_callback(lambda _:self.tasks.pop(job_id,None))
        return {"id":job_id,"status":"queued"}

    def update(self,job_id,status,result=None,error=None):
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT kind,status FROM web_jobs WHERE id=?', (job_id,)).fetchone()
            control = getattr(self, 'hermes_control', None)
            if previous and previous['kind'] == 'hermes_draw' and control:
                status,result,error = control.filter_update(db,job_id,status,result,error)
            db.execute("UPDATE web_jobs SET status=?,updated_at=?,result=?,error=? WHERE id=?",
                       (status,int(time.time()),json.dumps(result,ensure_ascii=False) if result is not None else None,error,job_id))
        if previous and previous['status'] != status and status in {'completed', 'partial', 'failed', 'interrupted'}:
            level = 'ERROR' if status == 'failed' else ('WARNING' if status in {'partial', 'interrupted'} else 'INFO')
            emit_event(self.event_log, 'job.' + status, level=level,
                       job_id=job_id, kind=previous['kind'], job_status=status,
                       **({'reason': reason} if (reason := known_failure_reason(error)) else {}))

    async def _run(self,job_id,kind,payload):
        self.update(job_id,"running")
        try:
            if kind=="crawl":
                if payload.get('gym_url') or payload.get('day'):
                    result = await asyncio.to_thread(self.crawl, gym_url=payload.get('gym_url', ''), day=payload.get('day', ''))
                else:
                    result=await asyncio.to_thread(self.crawl, payload['platform']) if payload.get('platform') else await asyncio.to_thread(self.crawl)
                self.log_gym_results(job_id, kind, result)
                self.update(job_id,"completed" if result["complete"] else "partial",result,
                    error='部分官方提交未能读取，请查看同步详情；当前榜单可能缺少未同步题目' if not result['complete'] else None)
            elif kind == 'specialist_generate':
                result = await self.run_specialist_generate(job_id, payload)
                self.update(job_id, 'completed', {'stage':'completed', 'plan':result,
                    'requested_count':len(result['items']), 'actual_count':len(result['items'])})
            elif kind == 'topic_method_check':
                result = await self.run_topic_method_check(payload)
                self.update(job_id, 'partial' if result.get('incomplete') else 'completed', result)
            elif kind == 'specialist_catalog':
                from .catalog import refresh_specialist_candidates
                from .training_topics import TOPICS
                topic = TOPICS.get(payload.get('topic_id'))
                if topic is None:
                    raise AIError('请选择支持的训练专题')
                progress_lock = threading.Lock()
                progress_open = True
                def publish_specialist(progress):
                    with progress_lock:
                        if progress_open:
                            self.update(job_id, 'running', {'topic_id': topic['id'], **progress})
                try:
                    rows = await asyncio.to_thread(refresh_specialist_candidates,
                        self.db, self.accounts, topic, on_progress=publish_specialist)
                finally:
                    with progress_lock:
                        progress_open = False
                active = [row for row in rows if row['status'] != 'skipped']
                if not active or all(row['status'] == 'failed' for row in active):
                    status = 'failed'
                elif all(row['status'] in {'updated', 'cached'} for row in active):
                    status = 'completed'
                else:
                    status = 'partial'
                self.update(job_id, status, {'topic_id': topic['id'], 'platforms': rows,
                    'count': sum(row.get('count', 0) for row in rows), 'complete': status == 'completed',
                    'completed_platforms': len(rows), 'total_platforms': len(rows)},
                    error='本次专题题库补充未完成，请查看分平台原因；已有缓存与题单已保留' if status == 'failed' else None)
            elif kind=="catalog":
                # A cancelled to_thread keeps running until its current request returns.
                # Close progress delivery before persisting "interrupted", otherwise a
                # late source callback could resurrect a stopped job as "running".
                progress_lock = threading.Lock()
                progress_open = True
                def publish_progress(progress):
                    with progress_lock:
                        if progress_open: self.update(job_id,"running",progress)
                try:
                    result=await asyncio.to_thread(refresh_catalog,self.db,on_progress=publish_progress)
                finally:
                    with progress_lock: progress_open = False
                catalog_status = "completed" if result["complete"] else ("partial" if result["count"] else "failed")
                # Personal history is independent of public catalog metadata.
                # Its failure must never discard successful catalog data.
                if self.accounts and self.accounts.public()['configured']:
                    self.update(job_id, "running", {**result, "submission_sync": {"status": "running"}})
                    try:
                        history = await asyncio.to_thread(self.crawl, since_today=True)
                        self.log_gym_results(job_id, kind, history)
                        result['submission_sync'] = {"status": "completed" if history['complete'] else "partial",
                            "accounts": history['accounts'], "message": "今日官方提交已同步，已验证的平台身份将用于当日榜单" if history['complete'] else "部分平台提交未能同步，请在连接与设置检查账号后重试"}
                    except Exception:
                        result['submission_sync'] = {"status": "failed", "message": "今日提交同步失败；题库结果已保留，请单独重试同步我的提交记录"}
                else:
                    result['submission_sync'] = {"status": "skipped", "message": "尚未配置平台账号，仅同步公开题库；请先连接平台再同步我的提交记录"}
                status = "partial" if catalog_status == "completed" and result['submission_sync']['status'] in {'partial', 'failed'} else catalog_status
                self.update(job_id,status,result,error="所有平台题库同步失败；已保留原缓存，请查看分平台状态" if catalog_status == "failed" else None)
            elif kind=="ai_test":
                result=await self.ai.test_connection()
                self.update(job_id,"completed",result)
            elif kind == 'hermes_draw':
                result = await self.run_hermes(payload,job_id=job_id)
                self.update(job_id, 'completed', result)
            elif kind.startswith("wb_"):
                result=await self.run_workbench(kind,payload)
                self.update(job_id,"completed",result)
            elif kind=="translation":
                runner=getattr(self,"run_translation",None)
                if runner is None:
                    raise AIError("翻译服务尚未初始化")
                # The route validates the request before queuing it. Validate
                # again at the async boundary so a persisted/replayed job
                # cannot bypass the same consent and engine constraints.
                from .translations import Translate
                request=Translate.model_validate({key: value for key, value in payload.items() if key != "problem_id"})
                result=await runner(payload["problem_id"],request)
                self.update(job_id,"completed",result)
            elif kind in {"analysis","recommend"}:
                context=await asyncio.to_thread(analytics.overview,self.db,payload.get("days",30),"" if kind=="recommend" else payload.get("platform",""))
                if kind=="analysis" and not context["stats"]["submissions"]:
                    raise AIError("所选时间窗口没有提交数据，请先采集或扩大时间范围")
                from .workbench import training_evidence
                context["workbench_evidence"]=training_evidence(self.db)
                evidence = getattr(self, 'daily_evidence', None)
                context['actual_time_evidence'] = evidence() if evidence else []
                selected=[]
                if kind=="recommend":
                    await refresh_recommendation_sessions(self.accounts)
                    selected=recommendation_candidates(self.db,self.accounts,payload.get("minimum",800),payload.get("maximum",1600),payload.get("tags"),60,published_after=payload.get("published_after"))
                    context["candidate_constraints"]=recommendation_status(self.db,self.accounts,payload.get("published_after"),minimum=payload.get("minimum",800),maximum=payload.get("maximum",1600),tags=payload.get("tags"))
                    if not selected:
                        raise AIError("没有满足条件的未做题，请先同步题库或调整筛选条件")
                report=await self.ai.generate(payload["provider"],payload["model"],context,selected,payload.get("goal",""),task=kind)
                if kind == "recommend":
                    ids = [p["id"] for p in report.get("selected_problems", [])]
                    current = recommendation_candidates(self.db,self.accounts,payload.get("minimum",800),payload.get("maximum",1600),payload.get("tags"),max(1,len(ids)),published_after=payload.get("published_after"),preferred_ids=ids)
                    if not set(ids) <= {p["id"] for p in current}:
                        raise AIError("候选资格已变化（登录、日期、提交或对战），未保存推荐，请重试")
                    if self.selection_reports is None:
                        raise AIError('选题阅读报告服务尚未初始化，请稍后重试')
                    # Metadata is only a shortlist. Final recommendations must
                    # first be confirmed against the actual official statement.
                    if report.get('selected_problems'):
                        report['selected_problems'] = await prepare_selection_reports(
                            self.selection_reports, report['selected_problems'],
                            payload['provider'], payload['model'],
                            context={'mode_title': 'AI 推荐训练'})
                    await refresh_recommendation_sessions(self.accounts)
                    ids = [p["id"] for p in report.get("selected_problems", [])]
                    current = recommendation_candidates(self.db,self.accounts,payload.get("minimum",800),payload.get("maximum",1600),payload.get("tags"),max(1,len(ids)),published_after=payload.get("published_after"),preferred_ids=ids)
                    if not set(ids) <= {p["id"] for p in current}:
                        raise AIError("候选资格已变化（登录、日期、提交或对战），未保存推荐，请重试")
                report_id=uuid.uuid4().hex
                report["data_window"]={"days":context["days"],"platform":"" if kind=="recommend" else payload.get("platform", ""),"submissions":context["stats"]["submissions"]}
                with self.db.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    if kind == "recommend":
                        battles = db.execute("SELECT value FROM web_settings WHERE key='battle_problem_ids'").fetchone()
                        if battles and set(ids) & set(json.loads(battles[0])):
                            raise AIError("推荐题目刚被分配为对战题，未保存推荐，请重试")
                    db.execute("INSERT INTO ai_reports VALUES(?,?,?,?,?,?)",(report_id,int(time.time()),kind,payload["provider"],payload["model"],json.dumps(report,ensure_ascii=False)))
                self.update(job_id,"completed",{"report_id":report_id})
            else:
                raise AIError("未知任务")
        except asyncio.CancelledError:
            self.update(job_id,"interrupted",error="服务停止，任务中断")
            raise
        except Exception as exc:
            # Never persist arbitrary exception messages, which may contain secrets.
            message=str(exc) if isinstance(exc,AIError) else f"任务失败 ({type(exc).__name__})，请检查服务端环境与网络"
            self.update(job_id,"failed",error=message)

    def log_gym_results(self, job_id, kind, result):
        rows = result.get('gym_sync', []) if isinstance(result, dict) else []
        if not isinstance(rows, list):
            return
        reasons = {'access_restricted', 'time_unverified', 'login_or_permission',
                   'query_limit', 'unreadable', 'scope_deferred'}
        for item in rows:
            if not isinstance(item, dict):
                continue
            complete = item.get('complete') is True
            code = item.get('error_code')
            emit_event(self.event_log, 'gym.sync_checked', level='INFO' if complete else 'WARNING',
                job_id=job_id, kind=kind, platform='codeforces',
                job_status='completed' if complete else 'partial', actual_count=item.get('count', 0),
                warning_count=item.get('deferred_count', 0),
                **({} if complete else {'reason': 'gym_' + code if isinstance(code, str) and code in reasons else 'gym_sync_incomplete'}))

    def crawl(self, platform=None, *, since_today=False, gym_url='', day=''):
        config=load(self.accounts.effective_path if self.accounts else self.settings.config_path)
        config.database=self.settings.database
        config.output_dir=self.settings.data_dir/"runs"
        since = None
        if since_today or day:
            from .gym_submissions import start_of_day
            since = start_of_day(day)
        code=crawl(config,Namespace(platform=[platform] if platform else None,since=since,full=False,max_pages=None))
        with self.db.connect() as db:
            row=db.execute("SELECT data FROM runs ORDER BY collected_at DESC,id DESC LIMIT 1").fetchone()
        if not row:
            raise AIError("采集未产生任务记录")
        run=json.loads(row[0])
        accounts = [{k:a.get(k) for k in ("platform","account","complete","error","warnings")} for a in run["accounts"]]
        gym = []
        if self.accounts and platform in {None, 'codeforces'}:
            from .gym_submissions import sync_gym
            gym = sync_gym(self.db, self.accounts, self.settings, url=gym_url, day=day)
        return {"complete":code==0 and all(a['complete'] for a in gym),"run_id":run["run_id"],
                "accounts":accounts + gym, "gym_sync":gym}

    async def close(self):
        tasks=[task for _,task in self.tasks.values()]
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)

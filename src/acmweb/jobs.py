import asyncio
import json
import logging
import time
import threading
import uuid
from argparse import Namespace

from . import analytics
from .catalog import refresh_catalog, recommendation_candidates, recommendation_status, refresh_recommendation_sessions
from .codex_bridge import AIError
from ojcrawler.config import load
from ojcrawler.cli import crawl

log=logging.getLogger(__name__)


class Jobs:
    def __init__(self,database,settings,ai,accounts=None):
        self.db,self.settings,self.ai=database,settings,ai
        self.tasks={}
        self.accounts=accounts
        self.guard=None

    def list(self):
        with self.db.connect() as db:
            rows=db.execute("SELECT * FROM web_jobs ORDER BY created_at DESC LIMIT 30").fetchall()
        return [{**dict(r),"result":json.loads(r["result"]) if r["result"] else None} for r in rows]

    def start(self,kind,payload=None):
        if self.guard:
            self.guard()
        if sum(not task.done() for _,task in self.tasks.values()) >= 2:
            raise AIError('当前用户任务已满，请等待完成')
        if any(k==kind and not task.done() for k,task in self.tasks.values()):
            raise AIError("同类任务正在执行，请等待完成")
        job_id=uuid.uuid4().hex
        now=int(time.time())
        with self.db.connect() as db:
            db.execute("INSERT INTO web_jobs VALUES(?,?,?,?,?,?,?)",(job_id,kind,"queued",now,now,None,None))
        task=asyncio.create_task(self._run(job_id,kind,payload or {}))
        self.tasks[job_id]=(kind,task)
        task.add_done_callback(lambda _:self.tasks.pop(job_id,None))
        return {"id":job_id,"status":"queued"}

    def update(self,job_id,status,result=None,error=None):
        with self.db.connect() as db:
            db.execute("UPDATE web_jobs SET status=?,updated_at=?,result=?,error=? WHERE id=?",
                       (status,int(time.time()),json.dumps(result,ensure_ascii=False) if result is not None else None,error,job_id))

    async def _run(self,job_id,kind,payload):
        self.update(job_id,"running")
        try:
            if kind=="crawl":
                result=await asyncio.to_thread(self.crawl, payload['platform']) if payload.get('platform') else await asyncio.to_thread(self.crawl)
                self.update(job_id,"completed" if result["complete"] else "partial",result)
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
                status = "completed" if result["complete"] else ("partial" if result["count"] else "failed")
                self.update(job_id,status,result,error="所有平台题库同步失败；已保留原缓存，请查看分平台状态" if status == "failed" else None)
            elif kind=="ai_test":
                result=await self.ai.test_connection()
                self.update(job_id,"completed",result)
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

    def crawl(self, platform=None):
        config=load(self.accounts.effective_path if self.accounts else self.settings.config_path)
        config.database=self.settings.database
        config.output_dir=self.settings.data_dir/"runs"
        code=crawl(config,Namespace(platform=[platform] if platform else None,since=None,full=False,max_pages=None))
        with self.db.connect() as db:
            row=db.execute("SELECT data FROM runs ORDER BY collected_at DESC,id DESC LIMIT 1").fetchone()
        if not row:
            raise AIError("采集未产生任务记录")
        run=json.loads(row[0])
        return {"complete":code==0,"run_id":run["run_id"],"accounts":[{k:a.get(k) for k in ("platform","account","complete","error","warnings")} for a in run["accounts"]]}

    async def close(self):
        tasks=[task for _,task in self.tasks.values()]
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)

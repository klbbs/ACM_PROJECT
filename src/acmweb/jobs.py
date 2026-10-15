import asyncio
import json
import logging
import time
import uuid
from argparse import Namespace

from . import analytics
from .catalog import refresh_catalog, candidates
from .codex_bridge import AIError
from ojcrawler.config import load
from ojcrawler.cli import crawl

log=logging.getLogger(__name__)


class Jobs:
    def __init__(self,database,settings,ai,accounts=None):
        self.db,self.settings,self.ai=database,settings,ai
        self.tasks={}
        self.accounts=accounts

    def list(self):
        with self.db.connect() as db:
            rows=db.execute("SELECT * FROM web_jobs ORDER BY created_at DESC LIMIT 30").fetchall()
        return [{**dict(r),"result":json.loads(r["result"]) if r["result"] else None} for r in rows]

    def start(self,kind,payload=None):
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
                result=await asyncio.to_thread(self.crawl)
                self.update(job_id,"completed" if result["complete"] else "partial",result)
            elif kind=="catalog":
                result=await asyncio.to_thread(refresh_catalog,self.db)
                self.update(job_id,"completed",result)
            elif kind in {"analysis","recommend"}:
                context=await asyncio.to_thread(analytics.overview,self.db,payload.get("days",30),payload.get("platform",""))
                if not context["stats"]["submissions"]:
                    raise AIError("所选时间窗口没有提交数据，请先采集或扩大时间范围")
                selected=[]
                if kind=="recommend":
                    selected=candidates(self.db,payload.get("minimum",800),payload.get("maximum",1600),payload.get("tags"),60,payload.get("min_contest",0))
                    if not selected:
                        raise AIError("没有满足条件的未做题，请先同步题库或调整筛选条件")
                report=await self.ai.generate(payload["provider"],payload["model"],context,selected,payload.get("goal",""))
                report_id=uuid.uuid4().hex
                report["data_window"]={"days":context["days"],"platform":payload.get("platform", ""),"submissions":context["stats"]["submissions"]}
                with self.db.connect() as db:
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

    def crawl(self):
        config=load(self.accounts.effective_path if self.accounts else self.settings.config_path)
        config.database=self.settings.database
        config.output_dir=self.settings.data_dir/"runs"
        code=crawl(config,Namespace(platform=None,since=None,full=False,max_pages=None))
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

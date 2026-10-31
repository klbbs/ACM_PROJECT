"""Durable, per-workspace cancellation fences for Hermes jobs only."""
import asyncio
import time
from fastapi import HTTPException
from acmweb.runtime_logs import emit_event
from acmweb.codex_bridge import AIError
from .memory import DrawMemory

TERMINAL = {'completed','failed','interrupted','partial'}
STAGES = {'queued':'等待处理','validating':'检查配置','preparing_image':'检查图片组件',
          'checking_sessions':'检查平台登录','syncing_submissions':'同步历史提交并排除已做题',
          'syncing_catalog':'同步 Codeforces 题库',
          'selecting':'AI 选题','reading_statement':'读取完整题面',
          'generating_report':'AI 阅读并生成说明与报告','revalidating':'核对题目资格',
          'rendering_image':'生成题目图片','completed':'已完成','failed':'未完成','interrupted':'已中断'}
STOP_MESSAGE = '用户已急停 Hermes 抽题流程，结果未交付；已发出的上游请求只能尽力中断，无法保证撤销或免除费用'
DDL = '''CREATE TABLE IF NOT EXISTS hermes_task_control (
 job_id TEXT PRIMARY KEY, stage TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
 requested_at INTEGER, stopped_at INTEGER
);'''
JOIN = '''SELECT j.*,c.stage,c.cancel_requested,c.requested_at,c.stopped_at
 FROM web_jobs j LEFT JOIN hermes_task_control c ON c.job_id=j.id'''


class TaskControl:
    def __init__(self, jobs):
        self.jobs,self.db=jobs,jobs.db
        with self.db.connect() as c:
            c.executescript(DDL)
            # Database startup already marks old active jobs interrupted. Their
            # previous process no longer has a local coroutine to wait for.
            c.execute("""UPDATE hermes_task_control SET stopped_at=? WHERE cancel_requested=1
                AND stopped_at IS NULL AND job_id IN(SELECT id FROM web_jobs WHERE status='interrupted')""",(int(time.time()),))
        self.memory=DrawMemory(self.db)

    @staticmethod
    def decorate(row):
        value=dict(row)
        stage=value.pop('stage',None) or value['status']
        requested=bool(value.pop('cancel_requested',False))
        requested_at=value.pop('requested_at',None)
        stopped_at=value.pop('stopped_at',None)
        value['control']={'stage':stage,'stage_title':STAGES.get(stage,'状态未知'),
            'cancel_requested':requested,'requested_at':requested_at,'stopped_at':stopped_at,
            'stop_state':('stopped' if stopped_at is not None else 'stopping') if requested else 'not_requested',
            'upstream_cancellation':'best_effort' if requested else 'not_requested'}
        return value

    def row(self,jid):
        with self.db.connect() as c:
            row=c.execute(JOIN+" WHERE j.id=? AND j.kind='hermes_draw'",(jid,)).fetchone()
        if not row:
            raise HTTPException(404,'找不到本人的 Hermes 任务')
        return self.decorate(row)

    def track(self,jid):
        with self.db.connect() as c:
            c.execute("INSERT OR IGNORE INTO hermes_task_control(job_id,stage) SELECT id,status FROM web_jobs WHERE id=? AND kind='hermes_draw'",(jid,))

    def check(self,jid):
        if jid is None:
            return
        with self.db.connect() as c:
            row=c.execute('SELECT cancel_requested FROM hermes_task_control WHERE job_id=?',(jid,)).fetchone()
        if row and row[0]:
            raise asyncio.CancelledError()

    def stage(self,jid,stage):
        if jid is None:
            return
        if stage not in STAGES:
            raise ValueError('unknown Hermes stage')
        self.check(jid)
        with self.db.connect() as c:
            c.execute('''UPDATE hermes_task_control SET stage=? WHERE job_id=? AND cancel_requested=0
                AND EXISTS(SELECT 1 FROM web_jobs WHERE id=? AND status IN ('queued','running'))''',(stage,jid,jid))
        self.check(jid)

    def filter_update(self,c,jid,status,result,error):
        """Called inside Jobs.update's write transaction: late success cannot win."""
        control=c.execute('SELECT cancel_requested FROM hermes_task_control WHERE job_id=?',(jid,)).fetchone()
        if control and control[0]:
            return 'interrupted',None,STOP_MESSAGE
        if status == 'completed':
            try:
                self.memory.record(c,jid,result)
            except AIError as exc:
                status,result,error='failed',None,str(exc)
        if status in TERMINAL:
            c.execute('UPDATE hermes_task_control SET stage=? WHERE job_id=?',(status,jid))
        return status,result,error

    def stopped(self,jid):
        with self.db.connect() as c:
            c.execute('UPDATE hermes_task_control SET stopped_at=COALESCE(stopped_at,?) WHERE job_id=? AND cancel_requested=1',(int(time.time()),jid))

    def cancel(self,jid):
        newly_requested=False
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute(JOIN+" WHERE j.id=? AND j.kind='hermes_draw'",(jid,)).fetchone()
            if not row:
                raise HTTPException(404,'找不到本人的 Hermes 任务')
            if row['status'] not in TERMINAL and not row['cancel_requested']:
                newly_requested=True
                c.execute('''INSERT INTO hermes_task_control(job_id,stage,cancel_requested,requested_at)
                    VALUES(?,?,1,?) ON CONFLICT(job_id) DO UPDATE SET cancel_requested=1,requested_at=excluded.requested_at''',
                    (jid,row['stage'] or row['status'],int(time.time())))
                c.execute("UPDATE web_jobs SET status='interrupted',updated_at=?,result=NULL,error=? WHERE id=?",
                          (int(time.time()),STOP_MESSAGE,jid))
        if newly_requested:
            emit_event(self.jobs.event_log,'job.interrupted','WARNING',job_id=jid,kind='hermes_draw',job_status='interrupted')
            entry=self.jobs.tasks.get(jid)
            if entry and not entry[1].done():
                # Repeated cancel requests do not interrupt cancellation cleanup.
                entry[1].add_done_callback(lambda _task:self.stopped(jid))
                entry[1].cancel()
            else:
                self.stopped(jid)
        value=self.row(jid)
        return {key:value[key] for key in ('id','status','control')}

    def listing(self,status=None,limit=20,offset=0):
        where=" WHERE j.kind='hermes_draw'"+(' AND j.status=?' if status else '')
        args=(status,) if status else ()
        with self.db.connect() as c:
            total=c.execute('SELECT COUNT(*) FROM web_jobs j'+where,args).fetchone()[0]
            rows=c.execute(JOIN+where+' ORDER BY j.created_at DESC,j.id DESC LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
        return {'total':total,'items':[{k:v for k,v in self.decorate(row).items() if k not in {'result','error'}} for row in rows]}

    def active_ids(self):
        with self.db.connect() as c:
            return [r[0] for r in c.execute('''SELECT j.id FROM web_jobs j
                LEFT JOIN hermes_task_control c ON c.job_id=j.id WHERE j.kind='hermes_draw'
                AND (j.status IN ('queued','running') OR (c.cancel_requested=1 AND c.stopped_at IS NULL))''')]

    def stop_all(self):
        # Snapshot only this user's Hermes jobs. Never touch a submit/crawl job
        # or create a persistent block on future explicit draw requests.
        items=[self.cancel(jid) for jid in self.active_ids()]
        return {'requested_count':len(items),'items':items,'scope':'current_user_hermes_jobs'}

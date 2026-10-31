"""Private, durable problem-ID memory for a user's Hermes integration.

Delivery is recorded in the same transaction that completes a job. History
exports contain only canonical IDs and evidence flags, never source or tokens.
"""
import hashlib
import json
import time
import uuid

from fastapi import HTTPException

from acmweb.catalog import _attempt_exclusions, _canonical
from acmweb.codex_bridge import AIError
from acmweb.problem_sources import normalize


DDL = """CREATE TABLE IF NOT EXISTS hermes_drawn_problems (
    problem_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS hermes_drawn_job ON hermes_drawn_problems(job_id);"""
SCOPE_KEY = 'hermes_history_scope'
DUPLICATE_MESSAGE = '此题已由 Hermes 抽取并交付，本次结果未交付；请重新抽取其他题目'


def _pid(value, platform=None):
    if not isinstance(value, str):
        return None
    try:
        return normalize(_canonical(platform, value) if platform else value)
    except (AIError, ValueError, TypeError, AttributeError):
        return None


def _result_pid(result):
    if not isinstance(result, dict) or not isinstance(result.get('problem'), dict):
        return None
    problem = result['problem']
    return _pid(problem.get('id'), problem.get('platform'))


class DrawMemory:
    def __init__(self, database):
        self.db = database
        with self.db.connect() as c:
            c.executescript(DDL)
            c.execute('BEGIN IMMEDIATE')
            c.execute('INSERT OR IGNORE INTO web_settings(key,value) VALUES(?,?)',
                      (SCOPE_KEY, json.dumps(uuid.uuid4().hex)))
            # Completed text/image and older handoff reports all used problem.id.
            # Interrupted/failed jobs, partial images and editor drafts are not
            # delivered problems. Retain the earliest completion if old releases
            # already delivered a duplicate.
            for row in c.execute("SELECT id,created_at,result FROM web_jobs WHERE kind='hermes_draw' AND status='completed' AND result IS NOT NULL ORDER BY created_at,id").fetchall():
                try:
                    pid = _result_pid(json.loads(row['result']))
                except (ValueError, TypeError):
                    continue
                if pid:
                    c.execute('INSERT OR IGNORE INTO hermes_drawn_problems VALUES(?,?,?)',
                              (pid, row['id'], row['created_at']))

    def drawn_ids(self):
        with self.db.connect() as c:
            return {row[0] for row in c.execute('SELECT problem_id FROM hermes_drawn_problems')}

    def record(self, connection, jid, result):
        """Caller holds the completion write transaction; duplicates roll back.

        Reapplying the exact completed job is safe. A different job must never
        complete with a previously delivered problem, even across processes.
        """
        pid = _result_pid(result)
        if not pid:
            raise AIError('Hermes 结果缺少可核验题号，未交付题目')
        previous = connection.execute('SELECT problem_id FROM hermes_drawn_problems WHERE job_id=?', (jid,)).fetchone()
        if previous and previous[0] != pid:
            raise AIError('此 Hermes 任务已交付另一题目，不能修改已交付题号')
        connection.execute('INSERT OR IGNORE INTO hermes_drawn_problems VALUES(?,?,?)',
                           (pid, jid, int(time.time())))
        owner = connection.execute('SELECT job_id FROM hermes_drawn_problems WHERE problem_id=?', (pid,)).fetchone()
        if owner[0] != jid:
            raise AIError(DUPLICATE_MESSAGE)
        return True

    def read(self, after=None, limit=500, revision=None):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise HTTPException(422, '每页题号数量须在 1 到 1000 之间')
        if after is not None and (not isinstance(after, str) or len(after) > 200):
            raise HTTPException(422, '题号分页游标格式不正确')
        items = {}

        def mark(pid, **flags):
            if pid is None:
                return
            item = items.setdefault(pid, {'problem_id': pid, 'attempted': False,
                'solved': False, 'drawn': False, 'skipped': False})
            for key, value in flags.items():
                item[key] |= value

        with self.db.connect() as c:
            # One explicit read transaction supplies a consistent revision and
            # page even while submission sync or another process is writing.
            c.execute('BEGIN')
            scope = json.loads(c.execute('SELECT value FROM web_settings WHERE key=?', (SCOPE_KEY,)).fetchone()[0])
            now = int(time.time())
            for row in c.execute('SELECT platform,problem_id,verdict,submitted_at FROM submissions'):
                solved = row['verdict'] == 'AC' and 0 < row['submitted_at'] <= now
                mark(_pid(row['problem_id'], row['platform']), attempted=True, solved=solved)
            for row in c.execute('SELECT DISTINCT problem_id FROM wb_attempts'):
                mark(_pid(row['problem_id']), attempted=True)
            # This helper requires verified remote judgement, a submission ID,
            # matching platform/account and timestamp. A draft marked AC does
            # not become an officially solved problem in the export.
            for pid in _attempt_exclusions(c, allow_attempted=True):
                mark(_pid(pid), attempted=True, solved=True)
            for row in c.execute("SELECT platform,problem_id,status FROM practice_feedback WHERE status IN ('done','skip')"):
                mark(_pid(row['problem_id'], row['platform']), attempted=row['status'] == 'done', skipped=row['status'] == 'skip')
            for row in c.execute('SELECT problem_id FROM hermes_drawn_problems'):
                mark(_pid(row['problem_id']), drawn=True)
        values = [items[pid] for pid in sorted(items)]
        fingerprint = hashlib.sha256(json.dumps({'scope': scope, 'items': values},
            sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if revision is not None and revision != fingerprint:
            raise HTTPException(409, '题目历史已变化，请从第一页重新同步，不能混用旧快照')
        remaining = [item for item in values if after is None or item['problem_id'] > after]
        page = remaining[:limit]
        return {'scope': scope, 'revision': fingerprint, 'items': page,
                'next_after': page[-1]['problem_id'] if len(remaining) > limit else None}

"""Shared, schema-limited AI solutions; no personal jobs, accounts or drafts.

Only the server generation pipeline writes here. Reading never calls AI or OJ.
The shared path is inherited alongside translations by authenticated workspaces.
"""
from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
import time
import uuid

from fastapi import HTTPException, Query

from .problem_sources import normalize
from .codex_bridge import AIError

DDL = '''CREATE TABLE IF NOT EXISTS shared_problem_reports (
 id TEXT PRIMARY KEY, problem_id TEXT NOT NULL, statement_hash TEXT NOT NULL,
 created_at INTEGER NOT NULL, model TEXT NOT NULL, provider TEXT NOT NULL,
 topic_query TEXT NOT NULL, digest TEXT NOT NULL, data TEXT NOT NULL,
 UNIQUE(problem_id,statement_hash,model,digest)
);
CREATE INDEX IF NOT EXISTS shared_problem_reports_problem
 ON shared_problem_reports(problem_id,statement_hash,created_at DESC);'''
NOTICE = '本站共享的 AI 解题报告，可能有误并含剧透；非官方题解，未运行或提交代码。'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))


class ProblemReports:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(DDL)
        path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, pid, statement_hash, report, *, provider, model, topic_query):
        from acmhermes.models import SolutionReport
        pid = normalize(pid)
        if not re.fullmatch('[a-f0-9]{64}', statement_hash):
            raise ValueError('invalid statement fingerprint')
        report = SolutionReport.model_validate(report).model_dump()
        if report['problem_id'] != pid:
            raise ValueError('report problem mismatch')
        # Never store original AI transport/provenance, endpoints, user IDs,
        # request payloads, reference code, drafts or account configuration.
        if provider not in {'api', 'codex'} or not isinstance(model, str) or not 1 <= len(model.strip()) <= 150:
            raise ValueError('invalid model metadata')
        encoded, query = _json(report), _json(topic_query)
        if len(encoded.encode()) > 256 * 1024 or len(query.encode()) > 12000:
            raise ValueError('report exceeds public storage bound')
        if re.search(r'```|#include|\bint\s+main\s*\(|public\s+static\s+void\s+main', encoded):
            raise ValueError('complete program is not a report')
        digest = hashlib.sha256((encoded + query).encode()).hexdigest()
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO shared_problem_reports VALUES(?,?,?,?,?,?,?,?,?)',
                (uuid.uuid4().hex, pid, statement_hash, int(time.time()), model.strip(), provider, query, digest, encoded))
            return db.execute('SELECT id FROM shared_problem_reports WHERE problem_id=? AND statement_hash=? AND model=? AND digest=?',
                (pid, statement_hash, model.strip(), digest)).fetchone()['id']

    def list(self, pid, statement_hash):
        with self.connect() as db:
            rows = db.execute('SELECT id,problem_id,created_at,model,provider,data FROM shared_problem_reports '
                'WHERE problem_id=? AND statement_hash=? ORDER BY created_at DESC,id DESC LIMIT 50', (pid, statement_hash)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            report = json.loads(item.pop('data'))
            item['difficulty_assessment'] = {key:report['difficulty_assessment'][key] for key in ('rating','confidence')}
            item['suitable'] = report['suitable']
            result.append(item)
        return result

    def get(self, rid, pid, statement_hash):
        if not re.fullmatch('[a-f0-9]{32}', rid):
            raise HTTPException(404, '共享题解报告不存在')
        with self.connect() as db:
            row = db.execute('SELECT * FROM shared_problem_reports WHERE id=? AND problem_id=?', (rid, pid)).fetchone()
        if row is None:
            raise HTTPException(404, '此题没有对应共享报告')
        if row['statement_hash'] != statement_hash:
            raise HTTPException(409, '原题题面已更新，此报告对应旧题面，未展示')
        return {key: row[key] for key in ('id', 'problem_id', 'created_at', 'model', 'provider')} | {
            'report': json.loads(row['data']), 'notice': NOTICE}

    def reusable(self, pid, statement_hash, topic_query, minimum, maximum):
        from acmhermes.rating_review import decision
        with self.connect() as db:
            rows = db.execute('SELECT id,data,model,provider FROM shared_problem_reports '
                'WHERE problem_id=? AND statement_hash=? AND topic_query=? ORDER BY created_at DESC,id DESC LIMIT 10',
                (pid, statement_hash, _json(topic_query))).fetchall()
        for row in rows:
            report = json.loads(row['data'])
            if report['suitable'] and decision(report['difficulty_assessment'], minimum, maximum) == 'accepted':
                return {'id': row['id'], 'report': report, 'model': row['model'], 'provider': row['provider']}
        return None


def register(app, store, statements):
    def current(problem_id):
        try:
            pid = normalize(problem_id)
            _, fingerprint = statements._cached(pid)
            return pid, fingerprint
        except AIError:
            raise HTTPException(409, '请先打开并获取本题的官方题面，再读取共享报告') from None

    @app.get('/api/problem-reports')
    def listing(problem_id: str = Query(min_length=1, max_length=200)):
        pid, fingerprint = current(problem_id)
        return {'problem_id': pid, 'items': store.list(pid, fingerprint), 'notice': NOTICE}

    @app.get('/api/problem-reports/{report_id}')
    def reading(report_id: str, problem_id: str = Query(min_length=1, max_length=200)):
        pid, fingerprint = current(problem_id)
        return store.get(report_id, pid, fingerprint)

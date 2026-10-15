import json
import sqlite3
import time
from contextlib import contextmanager

from ojcrawler.storage import Store


class Database:
    def __init__(self, path):
        self.path = path
        Store(path).close()
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS web_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS web_jobs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, result TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS ai_reports (id TEXT PRIMARY KEY, created_at INTEGER NOT NULL,
                kind TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS problem_catalog (platform TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY(platform,id));
            CREATE TABLE IF NOT EXISTS practice_feedback (platform TEXT NOT NULL, problem_id TEXT NOT NULL,
                status TEXT NOT NULL, note TEXT NOT NULL, updated_at INTEGER NOT NULL,
                PRIMARY KEY(platform,problem_id));
            ''')
            # A restart must not leave interrupted jobs forever "running".
            db.execute("UPDATE web_jobs SET status='interrupted',error='服务重启，任务未完成',updated_at=? WHERE status IN ('queued','running')", (int(time.time()),))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM web_settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connect() as db:
            db.execute("INSERT INTO web_settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key,json.dumps(value, ensure_ascii=False)))

    def report(self, report_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM ai_reports WHERE id=?", (report_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["data"] = json.loads(result["data"])
        return result

    def reports(self):
        with self.connect() as db:
            rows = db.execute("SELECT id,created_at,kind,provider,model FROM ai_reports ORDER BY created_at DESC LIMIT 50").fetchall()
        return [dict(row) for row in rows]

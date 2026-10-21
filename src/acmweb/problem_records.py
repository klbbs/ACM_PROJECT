"""Personal problem bookmarks and notes, stored only in the owning user's database."""
import json
import time

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .codex_bridge import AIError
from .problem_sources import normalize, source_url

MAX_NOTE_LENGTH = 20000
DDL = '''
CREATE TABLE IF NOT EXISTS personal_problem_records (
    problem_id TEXT PRIMARY KEY,
    favorite INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS personal_problem_records_updated
ON personal_problem_records(updated_at DESC, problem_id);
'''


class UpdateRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0, strict=True)
    favorite: bool | None = Field(default=None, strict=True)
    note: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)

    @model_validator(mode='after')
    def has_change(self):
        if self.favorite is None and self.note is None:
            raise ValueError('请提供收藏状态或备注')
        return self


class ProblemRecords:
    def __init__(self, db):
        self.db = db
        with db.connect() as conn:
            conn.executescript(DDL)

    @staticmethod
    def _pid(value):
        try:
            return normalize(value)
        except AIError as exc:
            raise HTTPException(400, str(exc)) from exc

    @staticmethod
    def _metadata(conn, pid):
        row = conn.execute('SELECT data FROM wb_problems WHERE id=?', (pid,)).fetchone()
        if row is None:
            row = conn.execute('SELECT data FROM problem_catalog WHERE id=?', (pid,)).fetchone()
        try:
            data = json.loads(row['data']) if row else {}
            title = data.get('title') or data.get('name') or pid
            title = title[:300] if isinstance(title, str) else pid
        except (ValueError, TypeError, AttributeError):
            title = pid
        return {'title': title, 'url': source_url(pid), 'platform': pid.split(':', 1)[0]}

    def _view(self, conn, pid, row):
        return {'problem_id': pid, 'favorite': bool(row['favorite']) if row else False,
                'note': row['note'] if row else '', 'revision': row['revision'] if row else 0,
                'updated_at': row['updated_at'] if row else None, **self._metadata(conn, pid)}

    def get(self, problem_id):
        pid = self._pid(problem_id)
        with self.db.connect() as conn:
            row = conn.execute('SELECT * FROM personal_problem_records WHERE problem_id=?', (pid,)).fetchone()
            return self._view(conn, pid, row)

    def update(self, problem_id, payload):
        pid = self._pid(problem_id)
        with self.db.connect() as conn:
            # Serialize the read/check/write across requests and independent worker processes.
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM personal_problem_records WHERE problem_id=?', (pid,)).fetchone()
            revision = row['revision'] if row else 0
            if revision != payload.revision:
                raise HTTPException(409, '这道题的记录已在其他页面更新，请重新载入后再保存；当前输入仍保留在页面中')
            favorite = payload.favorite if payload.favorite is not None else bool(row['favorite']) if row else False
            note = payload.note if payload.note is not None else row['note'] if row else ''
            conn.execute('''INSERT INTO personal_problem_records VALUES(?,?,?,?,?)
                ON CONFLICT(problem_id) DO UPDATE SET favorite=excluded.favorite,note=excluded.note,
                revision=excluded.revision,updated_at=excluded.updated_at''',
                (pid, int(favorite), note, revision + 1, int(time.time())))
            saved = conn.execute('SELECT * FROM personal_problem_records WHERE problem_id=?', (pid,)).fetchone()
            return self._view(conn, pid, saved)

    def list(self, search='', favorites_only=False, limit=50, offset=0):
        # Resolve metadata locally. Neither listing nor editing fetches remote statements.
        with self.db.connect() as conn:
            rows = conn.execute('''SELECT * FROM personal_problem_records
                WHERE (favorite=1 OR note!='') AND (?=0 OR favorite=1)
                ORDER BY updated_at DESC,problem_id''', (int(favorites_only),)).fetchall()
            items = [self._view(conn, row['problem_id'], row) for row in rows]
        term = search.strip().casefold()
        if term:
            items = [item for item in items if any(term in item[key].casefold() for key in ('problem_id', 'title', 'note'))]
        return {'items': items[offset:offset + limit], 'total': len(items), 'limit': limit, 'offset': offset}


def register(app, service):
    @app.get('/api/problem-records')
    def list_records(search: str = Query('', max_length=200), favorites_only: bool = False,
                     limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        return service.list(search, favorites_only, limit, offset)

    @app.get('/api/problem-records/{problem_id}')
    def get_record(problem_id: str):
        return service.get(problem_id)

    @app.put('/api/problem-records/{problem_id}')
    def update_record(problem_id: str, payload: UpdateRecord):
        return service.update(problem_id, payload)

"""Private inventory for completed Hermes preparation and bounded replenishment."""
import asyncio
import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass

from acmweb.catalog import _selection_exclusions
from acmweb.codex_bridge import AIError
from acmweb.runtime_logs import known_failure_reason

from .models import Draw
from .topics import context as topic_context, parse_topics

MAX_TARGET = 20
MAX_SEGMENTS = 64
REFILL_LEASE_SECONDS = 90
REFILL_HEARTBEAT_SECONDS = 20
RETRY_DELAYS = (20, 60)
MAX_FAILURES = len(RETRY_DELAYS) + 1
POLL_SECONDS = 20

log = logging.getLogger(__name__)

DDL = '''
CREATE TABLE IF NOT EXISTS hermes_prepared_segments (
    id TEXT PRIMARY KEY, request TEXT NOT NULL, target INTEGER NOT NULL,
    last_attempt REAL NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0, failure_reason TEXT
);
CREATE TABLE IF NOT EXISTS hermes_prepared_entries (
    problem_id TEXT PRIMARY KEY, segment_id TEXT NOT NULL, context TEXT NOT NULL,
    statement_hash TEXT NOT NULL, result TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS hermes_prepared_context ON hermes_prepared_entries(context);
CREATE INDEX IF NOT EXISTS hermes_prepared_segment ON hermes_prepared_entries(segment_id);
CREATE TABLE IF NOT EXISTS hermes_prepared_refill (
    id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL, segment_id TEXT NOT NULL,
    context TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TRIGGER IF NOT EXISTS hermes_prepared_consume AFTER INSERT ON hermes_drawn_problems
BEGIN
    DELETE FROM hermes_prepared_entries WHERE problem_id=NEW.problem_id;
END;
'''


@dataclass(frozen=True)
class PreparedEntry:
    problem_id: str
    statement_hash: str
    result: dict


def _segment_id(request):
    return json.dumps([request.score_range, topic_context(parse_topics(request.topic))],
                      ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _entry(row):
    return PreparedEntry(row['problem_id'], row['statement_hash'], json.loads(row['result']))


class PreparedPool:
    def __init__(self, database, reviews, memory, *, statement_fingerprint=None):
        self.db, self.reviews, self.memory = database, reviews, memory
        self.statement_fingerprint = statement_fingerprint
        with self.db.connect() as c:
            c.executescript(DDL)

    def _excluded(self, connection):
        return self.memory.recommended_ids(connection) | _selection_exclusions(connection)

    def _statement_matches(self, connection, problem_id, statement_hash):
        if self.statement_fingerprint is None:
            return True
        try:
            return self.statement_fingerprint(problem_id, connection) == statement_hash
        except (AIError, ValueError, KeyError, TypeError):
            return False

    def configure(self, request, target):
        """Set the stocking target for this exact score range and topic."""
        if type(target) is not int or not 0 <= target <= MAX_TARGET:
            raise ValueError(f'Hermes 每段库存数量须为 0～{MAX_TARGET} 的整数')
        key = _segment_id(request)
        request_json = Draw(score_range=request.score_range, topic=request.topic).model_dump_json()
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if not target:
                c.execute('DELETE FROM hermes_prepared_segments WHERE id=?', (key,))
                c.execute('DELETE FROM hermes_prepared_entries WHERE segment_id=?', (key,))
                return
            current = c.execute('SELECT 1 FROM hermes_prepared_segments WHERE id=?', (key,)).fetchone()
            count = c.execute('SELECT count(*) FROM hermes_prepared_segments').fetchone()[0]
            if not current and count >= MAX_SEGMENTS:
                raise ValueError(f'Hermes 最多配置 {MAX_SEGMENTS} 个库存分段')
            c.execute('''INSERT INTO hermes_prepared_segments(id,request,target) VALUES(?,?,?)
                ON CONFLICT(id) DO UPDATE SET request=excluded.request, target=excluded.target,
                    retry_at=0, failures=0, failure_reason=NULL''',
                      (key, request_json, target))
            c.execute('''DELETE FROM hermes_prepared_entries WHERE segment_id=? AND problem_id NOT IN
                (SELECT problem_id FROM hermes_prepared_entries WHERE segment_id=? ORDER BY created_at LIMIT ?)''',
                      (key, key, target))

    def segments(self):
        with self.db.connect() as c:
            rows = c.execute('SELECT * FROM hermes_prepared_segments ORDER BY last_attempt,id').fetchall()
        return [(Draw.model_validate_json(row['request']), row) for row in rows]

    def status(self, context_for):
        segments = [(request, segment, context_for(request)) for request, segment in self.segments()]
        if not segments:
            return []
        claimed = self.reviews.claimed()
        result = []
        with self.db.connect() as c:
            excluded = self._excluded(c)
            for request, segment, context in segments:
                rows = c.execute('''SELECT problem_id,statement_hash FROM hermes_prepared_entries
                    WHERE context=?''', (context,))
                stocked = {pid for pid, fingerprint in rows if pid not in excluded
                           and self._statement_matches(c, pid, fingerprint)}
                result.append({'score_range': list(request.score_range), 'topic': request.topic, 'target': segment['target'],
                               'stocked': len(stocked), 'available': len(stocked - claimed),
                               'failures': segment['failures'], 'paused': segment['failures'] >= MAX_FAILURES,
                               'retry_at': int(segment['retry_at']) if segment['retry_at'] > time.time() else None,
                               'failure_reason': segment['failure_reason']})
        return result

    def ids(self, owner=None):
        """Exclude stocked candidates from fresh generation, except an owner's reservation."""
        with self.db.connect() as c:
            return {row[0] for row in c.execute('''SELECT e.problem_id FROM hermes_prepared_entries e
                WHERE NOT EXISTS (SELECT 1 FROM hermes_candidate_claims cl WHERE cl.problem_id=e.problem_id
                    AND cl.job_id=? AND cl.expires_at>?)''', (owner, time.time()))}

    def entries(self, context, owner=None):
        claimed = self.reviews.claimed(owner)
        with self.db.connect() as c:
            excluded = claimed | self._excluded(c)
            return [_entry(row) for row in c.execute('''SELECT * FROM hermes_prepared_entries
                WHERE context=? ORDER BY created_at''', (context,)) if row['problem_id'] not in excluded
                and self._statement_matches(c, row['problem_id'], row['statement_hash'])]

    def reserve(self, context, owner, *, problem_id=None, excluded=(), seconds=1200):
        """Atomically reserve a ready entry through the regular candidate claim table."""
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            self.reviews.clean(c)
            excluded = set(excluded) | self._excluded(c)
            rows = c.execute('''SELECT * FROM hermes_prepared_entries
                WHERE context=? AND (? IS NULL OR problem_id=?) ORDER BY created_at''',
                             (context, problem_id, problem_id)).fetchall()
            for row in rows:
                pid = row['problem_id']
                if pid in excluded or not self._statement_matches(c, pid, row['statement_hash']):
                    continue
                c.execute('INSERT OR IGNORE INTO hermes_candidate_claims VALUES(?,?,?)',
                          (pid, owner, int(time.time()) + seconds + 60))
                claim = c.execute('SELECT job_id FROM hermes_candidate_claims WHERE problem_id=?', (pid,)).fetchone()
                if claim[0] == owner:
                    return _entry(row)
        return None

    def discard(self, problem_id, owner):
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            cursor = c.execute('''DELETE FROM hermes_prepared_entries WHERE problem_id=? AND EXISTS
                (SELECT 1 FROM hermes_candidate_claims WHERE problem_id=? AND job_id=? AND expires_at>?)''',
                               (problem_id, problem_id, owner, time.time()))
            return bool(cursor.rowcount)

    def begin_refill(self, request, context, owner):
        key = _segment_id(request)
        now = time.time()
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('''DELETE FROM hermes_candidate_claims WHERE job_id IN
                (SELECT owner FROM hermes_prepared_refill WHERE expires_at<=?)''', (now,))
            c.execute('DELETE FROM hermes_prepared_refill WHERE expires_at<=?', (now,))
            if c.execute('SELECT 1 FROM hermes_prepared_refill').fetchone():
                return False
            segment = c.execute('SELECT target,retry_at,failures FROM hermes_prepared_segments WHERE id=?', (key,)).fetchone()
            if not segment:
                return False
            c.execute('DELETE FROM hermes_prepared_entries WHERE segment_id=? AND context<>?', (key, context))
            excluded = self._excluded(c)
            rows = c.execute('SELECT problem_id,statement_hash FROM hermes_prepared_entries WHERE segment_id=?',
                             (key,)).fetchall()
            invalid = {pid for pid, fingerprint in rows if pid in excluded
                       or not self._statement_matches(c, pid, fingerprint)}
            c.executemany('DELETE FROM hermes_prepared_entries WHERE problem_id=?', [(pid,) for pid in invalid])
            if (segment['failures'] >= MAX_FAILURES or segment['retry_at'] > now
                    or len(rows) - len(invalid) >= segment['target']):
                return False
            c.execute('UPDATE hermes_prepared_segments SET last_attempt=? WHERE id=?', (now, key))
            c.execute('INSERT INTO hermes_prepared_refill VALUES(1,?,?,?,?)',
                      (owner, key, context, now + REFILL_LEASE_SECONDS))
            return True

    def stock(self, request, context, result, statement_hash, owner):
        key = _segment_id(request)
        pid = result['choice']['problem_id']
        result_json = json.dumps(result, ensure_ascii=False)
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('''SELECT 1 FROM hermes_prepared_refill
                WHERE owner=? AND segment_id=? AND context=? AND expires_at>?''',
                             (owner, key, context, time.time())).fetchone():
                return False
            self.reviews.clean(c)
            claim = c.execute('SELECT job_id FROM hermes_candidate_claims WHERE problem_id=?', (pid,)).fetchone()
            if not claim or claim[0] != owner or pid in self._excluded(c):
                return False
            if not self._statement_matches(c, pid, statement_hash):
                return False
            if c.execute('SELECT 1 FROM hermes_drawn_problems WHERE problem_id=?', (pid,)).fetchone():
                return False
            cursor = c.execute('''INSERT OR IGNORE INTO hermes_prepared_entries
                SELECT ?,?,?,?,?,? FROM hermes_prepared_segments s WHERE s.id=? AND s.target>
                    (SELECT count(*) FROM hermes_prepared_entries WHERE segment_id=s.id)''',
                               (pid, key, context, statement_hash, result_json, time.time(), key))
            return bool(cursor.rowcount)

    def renew_refill(self, owner):
        now = time.time()
        with self.db.connect() as c:
            cursor = c.execute('''UPDATE hermes_prepared_refill SET expires_at=?
                WHERE owner=? AND expires_at>?''', (now + REFILL_LEASE_SECONDS, owner, now))
            return bool(cursor.rowcount)

    def end_refill(self, request, owner, *, stocked=False, failure_reason=None):
        key = _segment_id(request)
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            deleted = c.execute('DELETE FROM hermes_prepared_refill WHERE owner=?', (owner,)).rowcount
            c.execute('DELETE FROM hermes_candidate_claims WHERE job_id=?', (owner,))
            if not deleted:
                return
            if stocked:
                c.execute('''UPDATE hermes_prepared_segments SET retry_at=0,failures=0,failure_reason=NULL
                    WHERE id=?''', (key,))
            elif failure_reason:
                row = c.execute('SELECT failures FROM hermes_prepared_segments WHERE id=?', (key,)).fetchone()
                if row:
                    failures = min(row['failures'] + 1, MAX_FAILURES)
                    retry_at = time.time() + RETRY_DELAYS[failures - 1] if failures < MAX_FAILURES else 0
                    c.execute('''UPDATE hermes_prepared_segments SET retry_at=?,failures=?,failure_reason=?
                        WHERE id=?''', (retry_at, failures, failure_reason, key))


class PreparedRefill:
    def __init__(self, pool, *, context_for, prepare, ready=lambda: True):
        self.pool, self.context_for, self.prepare, self.ready = pool, context_for, prepare, ready
        self._wake = asyncio.Event()
        self._task = None
        self._preparation = None
        self._preparing_segment = None
        self._preparation_finished = None
        self._preempted = False
        self._closing = False

    @property
    def active(self):
        return self._task is not None and not self._task.done()

    @property
    def running(self):
        return self._preparation is not None and not self._preparation.done()

    def start(self):
        if not self.active:
            self._task = asyncio.create_task(self._run())

    def wake(self):
        self._wake.set()

    async def close(self):
        task = self._task
        if task is not None:
            try:
                if not self._closing:
                    self._closing = True
                    if self._preparation:
                        await self.preempt()
                    else:
                        task.cancel()
                await task
            except asyncio.CancelledError:
                pass
            finally:
                if self._task is task:
                    self._task = None
                    self._closing = False

    async def cancel_segment(self, request):
        if self._preparing_segment == _segment_id(request):
            await self.preempt()

    async def preempt(self):
        if self._preparation is not None:
            self._cancel_preparation()
            await asyncio.shield(self._preparation_finished)

    def _cancel_preparation(self):
        if not self._preempted:
            self._preempted = True
            self._preparation.cancel()

    async def _renew(self, owner):
        while True:
            await asyncio.sleep(REFILL_HEARTBEAT_SECONDS)
            try:
                if self.pool.renew_refill(owner):
                    continue
            except sqlite3.Error:
                log.warning('Hermes 库存补充租约续期失败')
            self._cancel_preparation()
            return

    async def _fill_one(self):
        segments = self.pool.segments()
        if not segments or not self.ready():
            return False
        for request, _ in segments:
            context = self.context_for(request)
            owner = uuid.uuid4().hex
            if not context or not self.pool.begin_refill(request, context, owner):
                continue
            stocked = False
            failure_reason = None
            self._preparing_segment = _segment_id(request)
            self._preempted = False
            self._preparation_finished = asyncio.get_running_loop().create_future()
            self._preparation = asyncio.create_task(self.prepare(request, owner))
            heartbeat = asyncio.create_task(self._renew(owner))
            try:
                prepared = await self._preparation
                if prepared is not None and self.context_for(request) == context:
                    result, statement_hash = prepared
                    stocked = self.pool.stock(request, context, result, statement_hash, owner)
                    if not stocked:
                        failure_reason = 'hermes_prepared_candidate_unavailable'
                elif prepared is None:
                    failure_reason = 'hermes_candidates_exhausted'
            except asyncio.CancelledError:
                if self._closing:
                    raise
            except Exception as error:
                failure_reason = known_failure_reason(error) or 'hermes_generation_failed'
                log.warning('Hermes 库存补充失败')
            finally:
                heartbeat.cancel()
                self._preparing_segment = None
                self._preparation = None
                try:
                    self.pool.end_refill(request, owner, stocked=stocked, failure_reason=failure_reason)
                finally:
                    self._preparation_finished.set_result(None)
                    self._preparation_finished = None
            return True
        return False

    async def _run(self):
        while not self._closing:
            self._wake.clear()
            try:
                if await self._fill_one():
                    await asyncio.sleep(0)
                    continue
            except Exception:
                log.warning('Hermes 库存调度失败')
            try:
                await asyncio.wait_for(self._wake.wait(), POLL_SECONDS)
            except asyncio.TimeoutError:
                pass

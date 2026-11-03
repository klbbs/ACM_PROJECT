"""Durable, authenticated result delivery. Retransmission never invokes AI."""
import asyncio
import copy
import hashlib
import hmac
import json
import time
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.background import BackgroundTask

from acmweb.codex_bridge import AIError
from .models import ControlRequest

PROTOCOL = 'acm-hermes-receipt-v1'
ACK_TIMEOUT = 5
STREAM_ATTEMPTS = 3
POLL_INTERVAL = .5
MAX_RESULT_BYTES = 1024 * 1024
DDL = '''CREATE TABLE IF NOT EXISTS hermes_deliveries (
 job_id TEXT PRIMARY KEY, delivery_id TEXT NOT NULL UNIQUE,
 result_sha256 TEXT NOT NULL, result_json TEXT NOT NULL,
 created_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 last_sent_at INTEGER, next_attempt_at INTEGER, acknowledged_at INTEGER
);'''


class Receipt(BaseModel):
    model_config = ConfigDict(extra='forbid')
    delivery_id: str = Field(strict=True, pattern=r'^[a-f0-9]{32}$')
    result_sha256: str = Field(strict=True, pattern=r'^[a-f0-9]{64}$')
    received: bool = Field(strict=True)

    @model_validator(mode='after')
    def confirmed(self):
        if self.received is not True:
            raise ValueError('received must be true after durable receipt')
        return self


def result_document(job_id, result):
    """Stable public JSON bytes; never hash volatile screenshot availability."""
    if not isinstance(result, dict):
        raise AIError('抽题结果格式无效，未创建待确认交付')
    result = copy.deepcopy(result)
    result.pop('artifacts_expired', None)
    result.pop('artifact_id', None)
    if result.get('format') == 'image':
        result['screenshots'] = [{'index': i, 'url': f'/api/hermes/draws/{job_id}/screenshots/{i}'}
                                 for i, _ in enumerate(result.get('screenshots', []))]
    elif 'screenshots' in result:
        for key in ('screenshots','expires_at','translation_provenance','image_links'):
            result.pop(key, None)
        result.update(format='legacy_report', problem_explanation=None,
                      notice='历史结果仅保留原报告，不会自动重新生成题目或图片。')
    try:
        document = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        raw = document.encode('utf-8')
    except (TypeError, ValueError, UnicodeError):
        raise AIError('抽题结果无法安全序列化，未创建待确认交付') from None
    if len(raw) > MAX_RESULT_BYTES:
        raise AIError('抽题结果超过交付上限，未截断或确认结果')
    return document, hashlib.sha256(raw).hexdigest()


class Deliveries:
    def __init__(self, database):
        self.db = database
        with self.db.connect() as db:
            db.executescript(DDL)

    def record(self, db, job_id, result):
        """Called in the same transaction as generation and exclusion memory."""
        document, digest = result_document(job_id, result)
        old = db.execute('SELECT result_sha256 FROM hermes_deliveries WHERE job_id=?', (job_id,)).fetchone()
        if old:
            if old['result_sha256'] != digest:
                raise AIError('任务已有不同的交付结果，未覆盖原消息')
            return
        db.execute('INSERT INTO hermes_deliveries(job_id,delivery_id,result_sha256,result_json,created_at) VALUES(?,?,?,?,?)',
                   (job_id, uuid.uuid4().hex, digest, document, int(time.time())))

    @staticmethod
    def _job(db, job_id):
        row = db.execute("SELECT status,result FROM web_jobs WHERE id=? AND kind='hermes_draw'", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, '找不到本人的 Hermes 任务')
        return row

    @staticmethod
    def _metadata(row):
        state = 'acknowledged' if row['acknowledged_at'] is not None else 'awaiting_ack' if row['attempts'] else 'pending'
        return {'protocol': PROTOCOL, 'job_id': row['job_id'], 'delivery_id': row['delivery_id'],
                'result_sha256': row['result_sha256'], 'state': state, 'attempts': row['attempts'],
                'acknowledged_at': row['acknowledged_at'], 'next_attempt_at': row['next_attempt_at'],
                'ack_timeout_seconds': ACK_TIMEOUT}

    def status(self, job_id):
        with self.db.connect() as db:
            job = self._job(db, job_id)
            row = db.execute('SELECT * FROM hermes_deliveries WHERE job_id=?', (job_id,)).fetchone()
        value = self._metadata(row) if row else {'protocol': PROTOCOL, 'job_id': job_id,
            'state': 'not_started' if job['status'] == 'completed' else 'not_ready'}
        return {**value, 'ack_required': job['status'] == 'completed' and value['state'] != 'acknowledged',
                'stream_url':f'/api/hermes/draws/{job_id}/delivery/stream',
                'ack_url':f'/api/hermes/draws/{job_id}/delivery/ack'}

    def start(self, job_id):
        # Lazy upgrade for completed jobs predating receipt support. GET is read-only.
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            job = self._job(db, job_id)
            if job['status'] != 'completed' or not job['result']:
                raise HTTPException(409, '任务尚未成功完成，没有可交付的结果；不会重新抽题')
            if not db.execute('SELECT 1 FROM hermes_deliveries WHERE job_id=?', (job_id,)).fetchone():
                self.record(db, job_id, json.loads(job['result']))

    def claim(self, job_id):
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            job = self._job(db, job_id)
            if job['status'] != 'completed':
                raise HTTPException(409, '任务没有成功完成，不能发送结果')
            row = db.execute('SELECT * FROM hermes_deliveries WHERE job_id=?', (job_id,)).fetchone()
            if not row:
                raise HTTPException(409, '请先开启本任务的结果交付')
            metadata = self._metadata(row)
            if row['acknowledged_at'] is not None:
                return {'type': 'acknowledged', **metadata}
            now = time.time()
            if row['next_attempt_at'] is not None and row['next_attempt_at'] > now:
                return {'type': 'waiting', **metadata, 'retry_after_seconds': row['next_attempt_at'] - now}
            deadline = now + ACK_TIMEOUT
            db.execute('UPDATE hermes_deliveries SET attempts=attempts+1,last_sent_at=?,next_attempt_at=? WHERE job_id=?',
                       (now, deadline, job_id))
            return {'type': 'result', **metadata, 'state': 'awaiting_ack', 'attempts': row['attempts'] + 1,
                    'next_attempt_at': deadline, 'result_json': row['result_json']}

    def acknowledge(self, job_id, receipt):
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            job = self._job(db, job_id)
            if job['status'] != 'completed':
                raise HTTPException(409, '任务没有成功完成，不能确认结果')
            row = db.execute('SELECT * FROM hermes_deliveries WHERE job_id=?', (job_id,)).fetchone()
            if not row or not hmac.compare_digest(row['delivery_id'], receipt.delivery_id):
                raise HTTPException(404, '找不到此任务对应的交付消息')
            if not hmac.compare_digest(row['result_sha256'], receipt.result_sha256):
                raise HTTPException(409, '结果摘要不匹配，未确认收到；请重新读取同一条消息')
            if not row['attempts']:
                raise HTTPException(409, '结果尚未发送，不能提前确认')
            db.execute('UPDATE hermes_deliveries SET acknowledged_at=COALESCE(acknowledged_at,?),next_attempt_at=NULL WHERE job_id=?',
                       (int(time.time()), job_id))
            row = db.execute('SELECT * FROM hermes_deliveries WHERE job_id=?', (job_id,)).fetchone()
        return {'type': 'acknowledged', **self._metadata(row)}


def register(app, service):
    store = service.control.deliveries
    active = {}

    @app.get('/api/hermes/draws/{job_id}/delivery')
    def status(job_id: str):
        return store.status(job_id)

    @app.post('/api/hermes/draws/{job_id}/delivery/ack')
    def acknowledge(job_id: str, payload: Receipt):
        return store.acknowledge(job_id, payload)

    @app.post('/api/hermes/draws/{job_id}/delivery/stream')
    async def stream(job_id: str, payload: ControlRequest, request: Request):
        store.start(job_id)
        if job_id in active or len(active) >= 2:
            raise HTTPException(429, '结果交付连接已打开，请使用现有连接或稍后重连', headers={'Retry-After': '2'})
        token = object()
        active[job_id] = token

        def release():
            if active.get(job_id) is token:
                active.pop(job_id, None)

        async def events():
            sent = 0
            deadline = time.monotonic() + ACK_TIMEOUT * (STREAM_ATTEMPTS + 1)
            try:
                while time.monotonic() < deadline:
                    if await request.is_disconnected():
                        return
                    message = store.claim(job_id)
                    if message['type'] == 'acknowledged':
                        yield json.dumps(message, ensure_ascii=False) + '\n'
                        return
                    if message['type'] == 'result':
                        if sent >= STREAM_ATTEMPTS:
                            # claim must not advance a dispatch that is not sent.
                            raise AssertionError('delivery attempt budget exhausted')
                        sent += 1
                        yield json.dumps(message, ensure_ascii=False) + '\n'
                    if sent >= STREAM_ATTEMPTS:
                        # Wait once more for the final receipt, without claiming
                        # a fourth dispatch. Unacked data stays durably pending.
                        while time.time() < message['next_attempt_at'] and time.monotonic() < deadline:
                            if await request.is_disconnected(): return
                            state = store.status(job_id)
                            if state['state'] == 'acknowledged':
                                yield json.dumps({'type': 'acknowledged', **state}) + '\n'
                                return
                            await asyncio.sleep(POLL_INTERVAL)
                        break
                    await asyncio.sleep(POLL_INTERVAL)
                yield json.dumps({'type': 'pending', **store.status(job_id)}) + '\n'
            finally:
                release()

        return StreamingResponse(events(), media_type='application/x-ndjson',
            headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'}, background=BackgroundTask(release))

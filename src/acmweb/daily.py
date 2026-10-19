"""User-driven daily practice goals, limited AI rerolls and check-ins."""
import asyncio
import json
import time
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .catalog import candidates

TZ = ZoneInfo('Asia/Shanghai')


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    daily_goal: int = Field(default=1, ge=1, le=10)
    duration_days: int = Field(default=30, ge=1, le=365)


class Roll(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: Literal['api', 'codex']
    model: str = Field(min_length=1, max_length=150)


class Complete(BaseModel):
    model_config = ConfigDict(extra='forbid')
    problem_id: str = Field(min_length=1, max_length=100)


class Daily:
    def __init__(self, database, ai):
        self.db, self.ai = database, ai
        self.roll_lock = asyncio.Lock()
        with database.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS daily_rolls(day TEXT NOT NULL,seq INTEGER NOT NULL,problem_id TEXT NOT NULL,data TEXT NOT NULL,created_at INTEGER NOT NULL,PRIMARY KEY(day,seq));
            CREATE TABLE IF NOT EXISTS daily_completions(day TEXT NOT NULL,problem_id TEXT NOT NULL,created_at INTEGER NOT NULL,PRIMARY KEY(day,problem_id));
            CREATE TABLE IF NOT EXISTS daily_checkins(day TEXT PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN ('normal','k')),completed INTEGER NOT NULL,goal INTEGER NOT NULL,updated_at INTEGER NOT NULL);
            ''')

    @staticmethod
    def today():
        return datetime.now(TZ).date().isoformat()

    def state(self):
        settings = Settings.model_validate(self.db.get('daily_settings', {})).model_dump()
        start = self.db.get('daily_started_at')
        today = self.today()
        end = (date.fromisoformat(start) + timedelta(days=settings['duration_days'] - 1)).isoformat() if start else None
        with self.db.connect() as db:
            rolls = [dict(row) | {'data': json.loads(row['data'])} for row in db.execute('SELECT seq,problem_id,data,created_at FROM daily_rolls WHERE day=? ORDER BY seq', (today,))]
            completed = db.execute('SELECT COUNT(*) FROM daily_completions WHERE day=?', (today,)).fetchone()[0]
            checkin = db.execute('SELECT kind FROM daily_checkins WHERE day=?', (today,)).fetchone()
            month_start = today[:7] + '-01'
            month_end = (date.fromisoformat(month_start).replace(day=28) + timedelta(days=4)).replace(day=1).isoformat()
            stats = db.execute("SELECT SUM(kind='normal') normal,SUM(kind='k') k,COUNT(*) total FROM daily_checkins WHERE day>=? AND day<?", (month_start, month_end)).fetchone()
            all_stats = db.execute("SELECT SUM(kind='normal') normal,SUM(kind='k') k,COUNT(*) total FROM daily_checkins").fetchone()
        active = bool(start and start <= today <= end)
        rerolls_used = max(0, len(rolls) - 1)
        return {'settings': settings, 'started_at': start, 'ends_at': end, 'active': active,
                'day': today, 'rolls_used': len(rolls), 'rerolls_used': rerolls_used,
                'rerolls_remaining': max(0, 2 - rerolls_used),
                'rolls': rolls, 'completed': completed, 'checkin': {'kind': checkin['kind'] if checkin else None,
                'month_normal': stats['normal'] or 0, 'month_k': stats['k'] or 0, 'month_total': stats['total'] or 0,
                'all_normal': all_stats['normal'] or 0, 'all_k': all_stats['k'] or 0, 'all_total': all_stats['total'] or 0}}

    def configure(self, payload):
        settings = payload.model_dump()
        self.db.set('daily_settings', settings)
        start = self.db.get('daily_started_at')
        end = (date.fromisoformat(start) + timedelta(days=settings['duration_days'] - 1)).isoformat() if start else None
        if not start or self.today() > end:
            self.db.set('daily_started_at', self.today())
        return self.state()

    async def roll(self, payload):
        async with self.roll_lock:
            return await self._roll(payload)

    async def _roll(self, payload):
        state = self.state()
        if not state['active']:
            raise HTTPException(409, '每日任务未开始或周期已结束，请保存每日题量与周期后开启')
        if state['rerolls_remaining'] <= 0:
            raise HTTPException(409, '今日两次换题机会已用完')
        previous = {row['problem_id'] for row in state['rolls']}
        prefs = self.db.get('training', {})
        pool = candidates(self.db, prefs.get('minimum', 800), prefs.get('maximum', 1600), prefs.get('preferred_tags', []), 60, prefs.get('min_contest', 0))
        pool = [item for item in pool if item['id'] not in previous]
        if not pool:
            raise HTTPException(409, '没有新的候选题，请同步 Codeforces 题库或调整训练范围')
        result = await self.ai.complete('daily_roll', payload.provider, payload.model,
            {'statistics': {'daily_goal': state['settings']['daily_goal'], 'completed': state['completed']}, 'candidates': pool}, DailySelection)
        choices = {item['id']: item for item in pool}
        if result.data['id'] not in choices:
            raise HTTPException(502, 'AI 返回了候选列表之外的题目')
        item = {**choices[result.data['id']], 'reason': result.data['reason']}
        seq = state['rolls_used'] + 1
        with self.db.connect() as db:
            db.execute('INSERT INTO daily_rolls VALUES(?,?,?,?,?)', (state['day'], seq, item['id'], json.dumps(item, ensure_ascii=False), int(time.time())))
        return self.state()

    def complete(self, payload):
        state = self.state()
        now = int(time.time())
        with self.db.connect() as db:
            # Any completed problem earns a normal check-in; a rolled task can
            # upgrade the day to K once the configured daily goal is reached.
            db.execute('INSERT OR IGNORE INTO daily_completions VALUES(?,?,?)', (state['day'], payload.problem_id, now))
            completed = db.execute('SELECT COUNT(*) FROM daily_completions WHERE day=?', (state['day'],)).fetchone()[0]
            task_done = db.execute('''SELECT 1 FROM daily_rolls r JOIN daily_completions c
                ON c.day=r.day AND c.problem_id=r.problem_id WHERE r.day=? LIMIT 1''', (state['day'],)).fetchone()
            kind = 'k' if state['active'] and task_done and completed >= state['settings']['daily_goal'] else None
            existing = db.execute('SELECT kind FROM daily_checkins WHERE day=?', (state['day'],)).fetchone()
            kind = 'k' if kind == 'k' or (existing and existing['kind'] == 'k') else 'normal'
            db.execute('INSERT INTO daily_checkins VALUES(?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET kind=excluded.kind,completed=excluded.completed,goal=excluded.goal,updated_at=excluded.updated_at', (state['day'], kind, completed, state['settings']['daily_goal'], now))
            db.execute("INSERT INTO practice_feedback VALUES('codeforces',?,?,?,?) ON CONFLICT(platform,problem_id) DO UPDATE SET status='done',updated_at=excluded.updated_at", (payload.problem_id.removeprefix('codeforces:'), 'done', 'daily practice', now))
        return self.state()


class DailySelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    reason: str = Field(min_length=1, max_length=300)


def register(app: FastAPI, daily: Daily):
    @app.get('/api/daily')
    def daily_state():
        return daily.state()

    @app.put('/api/daily/settings')
    def daily_settings(payload: Settings):
        return daily.configure(payload)

    @app.post('/api/daily/roll')
    async def daily_roll(payload: Roll):
        return await daily.roll(payload)

    @app.post('/api/daily/complete')
    def daily_complete(payload: Complete):
        return daily.complete(payload)

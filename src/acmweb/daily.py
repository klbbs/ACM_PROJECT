"""Per-user daily practice plans and submission-derived check-ins."""
import json
import time
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from .catalog import candidates
from .codex_bridge import AIError

TZ = ZoneInfo("Asia/Shanghai")
DAILY_DDL = """
CREATE TABLE IF NOT EXISTS daily_plans (
  day TEXT PRIMARY KEY, status TEXT NOT NULL, goal_count INTEGER NOT NULL,
  starts_on TEXT NOT NULL, ends_on TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_plan_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, ordinal INTEGER NOT NULL,
  problem_id TEXT NOT NULL, data TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL, FOREIGN KEY(day) REFERENCES daily_plans(day)
);
CREATE INDEX IF NOT EXISTS daily_plan_tasks_day ON daily_plan_tasks(day,active);
CREATE TABLE IF NOT EXISTS daily_roll_reservations (
  token TEXT PRIMARY KEY, day TEXT NOT NULL, task_id INTEGER NOT NULL,
  status TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS daily_roll_day ON daily_roll_reservations(day,status);
CREATE TABLE IF NOT EXISTS daily_roll_history (
  day TEXT NOT NULL, problem_id TEXT NOT NULL, PRIMARY KEY(day,problem_id)
);
"""


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Settings(Strict):
    daily_goal: int = Field(default=1, ge=1, le=10, strict=True)
    minutes_per_day: int = Field(default=60, ge=5, le=600, strict=True)
    duration_days: int = Field(default=30, ge=1, le=365, strict=True)


class SelectedProblem(Strict):
    id: str = Field(min_length=1, max_length=100)
    reason: str = Field(default="", max_length=500)


class DailySelection(Strict):
    selected_problems: list[SelectedProblem] = Field(max_length=20)


class GenerateRequest(Strict):
    provider: Literal["api", "codex"]
    model: str = Field(min_length=1, max_length=150)


class Roll(GenerateRequest):
    task_id: int | None = Field(default=None, gt=0, strict=True)


class Complete(Strict):
    problem_id: str = Field(min_length=1, max_length=100)


class Daily:
    def __init__(self, database, ai):
        self.db, self.ai = database, ai
        self.guard = None
        self.active_ai = False
        with self.db.connect() as db:
            db.executescript(DAILY_DDL)
            # Reservations are short leases. Another server process may still own a live call.
            db.execute("DELETE FROM daily_roll_reservations WHERE status='reserved' AND created_at<?", (int(time.time())-600,))
            db.execute("UPDATE daily_plans SET status='failed' WHERE status='generating' AND created_at<?", (int(time.time())-600,))
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'daily_rolls' in tables:
                db.execute("INSERT OR IGNORE INTO daily_roll_history SELECT day,problem_id FROM daily_rolls")
                for row in db.execute("SELECT day,COUNT(*) total FROM daily_rolls GROUP BY day").fetchall():
                    for i in range(min(2, max(0, row['total']-1))):
                        db.execute("INSERT OR IGNORE INTO daily_roll_reservations VALUES(?,?,0,'used',?)",
                                   (f"legacy:{row['day']}:{i}", row['day'], int(time.time())))

    @staticmethod
    def today(now=None):
        return datetime.fromtimestamp(time.time() if now is None else now, TZ).date()

    def settings(self):
        raw = self.db.get("daily_settings", {})
        try:
            return Settings.model_validate(raw).model_dump()
        except Exception:
            return Settings().model_dump()

    def save_settings(self, value):
        clean = Settings.model_validate(value).model_dump()
        self.db.set("daily_settings", clean)
        self._cycle(self.today().isoformat(), create=True)
        return self.state()

    def configure(self, payload):
        return self.save_settings(payload.model_dump())

    def _cycle(self, day, create=False):
        state = self.db.get("daily_task_cycle", {})
        if state.get("starts_on") and state.get("ends_on") and state["starts_on"] <= day <= state["ends_on"]:
            return state
        legacy_start = self.db.get("daily_started_at")
        if not state and legacy_start:
            legacy_end = (date.fromisoformat(legacy_start) + timedelta(days=self.settings()["duration_days"]-1)).isoformat()
            if legacy_start <= day <= legacy_end:
                state = {"starts_on": legacy_start, "ends_on": legacy_end}
                self.db.set("daily_task_cycle", state)
                return state
        if not create:
            return state or None
        days = self.settings()["duration_days"]
        state = {"starts_on": day, "ends_on": (date.fromisoformat(day) + timedelta(days=days - 1)).isoformat()}
        self.db.set("daily_task_cycle", state)
        return state

    def request_generate(self, provider, model, today=None):
        day = today or self.today().isoformat()
        cycle = self._cycle(day, create=True)
        if day > cycle["ends_on"]:
            raise HTTPException(409, "每日任务周期已结束，请更新设置后开始新周期")
        settings = self.settings()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE daily_plans SET status='failed' WHERE status='generating' AND created_at<?", (int(time.time())-600,))
            row = db.execute("SELECT status FROM daily_plans WHERE day=?", (day,)).fetchone()
            if row and row[0] in {"generating", "ready"}:
                raise HTTPException(409, "今天的每日题单已生成或正在生成")
            db.execute("""INSERT INTO daily_plans(day,status,goal_count,starts_on,ends_on,created_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET status='generating',goal_count=excluded.goal_count,
                starts_on=excluded.starts_on,ends_on=excluded.ends_on,created_at=excluded.created_at""",
                (day, "generating", settings["daily_goal"], cycle["starts_on"], cycle["ends_on"], int(time.time())))
        # A generated plan snapshots its goal; later preference edits cannot lower today's K threshold.
        return {"day": day, "count": settings["daily_goal"], "provider": provider, "model": model}

    def request_roll(self, task_id, provider, model, today=None):
        day = today or self.today().isoformat()
        token = uuid.uuid4().hex
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM daily_roll_reservations WHERE status='reserved' AND created_at<?", (int(time.time())-600,))
            task = db.execute("SELECT problem_id FROM daily_plan_tasks WHERE id=? AND day=? AND active=1", (task_id, day)).fetchone()
            plan = db.execute("SELECT status FROM daily_plans WHERE day=?", (day,)).fetchone()
            used = db.execute("SELECT COUNT(*) FROM daily_roll_reservations WHERE day=? AND status IN ('reserved','used')", (day,)).fetchone()[0]
            if not task or not plan or plan[0] != "ready":
                raise HTTPException(409, "今天没有可换的每日题目")
            if db.execute("SELECT 1 FROM daily_roll_reservations WHERE day=? AND status='reserved'", (day,)).fetchone():
                raise HTTPException(409, "换题正在进行，请等待结果后再换题")
            raw_pid = task[0].removeprefix("codeforces:")
            if any(platform == 'codeforces' and pid == raw_pid and datetime.fromtimestamp(ts, TZ).date().isoformat() == day
                   for platform, pid, ts in self._trusted_ac(db, time.time())):
                raise HTTPException(409, "已完成的每日题目不能换题")
            if used >= 2:
                raise HTTPException(409, "每天最多使用 2 次换题机会")
            db.execute("INSERT INTO daily_roll_reservations VALUES(?,?,?,?,?)", (token, day, task_id, "reserved", int(time.time())))
            excluded = {row[0] for row in db.execute("SELECT problem_id FROM daily_plan_tasks WHERE day=?", (day,))}
            excluded.update(row[0] for row in db.execute("SELECT problem_id FROM daily_roll_history WHERE day=?", (day,)))
            db.execute("INSERT OR IGNORE INTO daily_roll_history VALUES(?,?)", (day, task[0]))
        return {"day": day, "token": token, "task_id": task_id, "excluded_ids": sorted(excluded),
                "provider": provider, "model": model}

    def _candidate_pool(self, count, excluded):
        prefs = self.db.get("training", {})
        pool = candidates(self.db, minimum=prefs.get("minimum", 1000), maximum=prefs.get("maximum", 1600),
                          limit=1000000, min_contest=prefs.get("min_contest", 0))
        excluded = set(excluded)
        with self.db.connect() as db:
            excluded.update(f"{platform}:{pid}" for platform, pid, ts in self._trusted_ac(db, time.time()))
        return [row for row in pool if row["id"] not in excluded][:60]

    async def generate(self, payload):
        day, count = payload["day"], payload["count"]
        try:
            with self.db.connect() as db:
                history = [row[0] for row in db.execute('SELECT problem_id FROM daily_roll_history WHERE day=?', (day,))]
            pool = self._candidate_pool(count, history)
            if len(pool) < count:
                raise AIError(f"题库中只有 {len(pool)} 道符合条件的未完成题，无法生成 {count} 道每日题目")
            chosen = await self._select(payload["provider"], payload["model"], pool, count, [])
            if day != self.today().isoformat():
                raise AIError("日期已变化，请为新的一天重新生成任务")
            now = int(time.time())
            with self.db.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute("SELECT status FROM daily_plans WHERE day=?", (day,)).fetchone()
                if not current or current[0] != "generating":
                    raise AIError("每日计划状态已变化，请重试")
                db.execute("DELETE FROM daily_plan_tasks WHERE day=?", (day,))
                db.executemany("INSERT INTO daily_plan_tasks(day,ordinal,problem_id,data,active,created_at) VALUES(?,?,?,?,1,?)",
                    [(day, i, row["id"], json.dumps(row, ensure_ascii=False), now) for i, row in enumerate(chosen)])
                db.execute("UPDATE daily_plans SET status='ready' WHERE day=?", (day,))
            return {"day": day, "tasks": chosen}
        except BaseException:
            with self.db.connect() as db:
                db.execute("UPDATE daily_plans SET status='failed' WHERE day=? AND status='generating'", (day,))
            raise

    async def _roll(self, payload):
        token, day, task_id = payload["token"], payload["day"], payload["task_id"]
        try:
            pool = self._candidate_pool(1, payload["excluded_ids"])
            if not pool:
                raise AIError("没有新的候选题可替换，请同步题库或调整训练难度")
            chosen = await self._select(payload["provider"], payload["model"], pool, 1, payload["excluded_ids"], task_id)
            replacement = chosen[0]
            if day != self.today().isoformat():
                raise AIError("日期已变化，本次换题不消耗额度")
            now = int(time.time())
            with self.db.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                reservation = db.execute("SELECT status FROM daily_roll_reservations WHERE token=? AND day=?", (token, day)).fetchone()
                active = db.execute("SELECT id FROM daily_plan_tasks WHERE id=? AND day=? AND active=1", (task_id, day)).fetchone()
                if not reservation or reservation[0] != "reserved" or not active:
                    raise AIError("换题请求已失效，请刷新每日任务")
                prior = db.execute("SELECT problem_id FROM daily_plan_tasks WHERE id=?", (task_id,)).fetchone()[0]
                if any(platform == 'codeforces' and pid == prior.removeprefix('codeforces:')
                       and datetime.fromtimestamp(ts, TZ).date().isoformat() == day
                       for platform, pid, ts in self._trusted_ac(db, time.time())):
                    raise AIError("原题已在换题期间通过，本次换题取消并退还机会")
                db.execute("UPDATE daily_plan_tasks SET active=0 WHERE id=?", (task_id,))
                db.execute("INSERT INTO daily_plan_tasks(day,ordinal,problem_id,data,active,created_at) SELECT day,ordinal,?,?,1,? FROM daily_plan_tasks WHERE id=?",
                           (replacement["id"], json.dumps(replacement, ensure_ascii=False), now, task_id))
                db.execute("INSERT OR IGNORE INTO daily_roll_history VALUES(?,?)", (day, replacement["id"]))
                db.execute("INSERT OR IGNORE INTO daily_roll_history VALUES(?,?)", (day, prior))
                db.execute("UPDATE daily_roll_reservations SET status='used' WHERE token=?", (token,))
            return {"day": day, "replaced": prior, "task": replacement}
        except BaseException:
            with self.db.connect() as db:
                db.execute("DELETE FROM daily_roll_reservations WHERE token=? AND status='reserved'", (token,))
            raise

    async def _select(self, provider, model, pool, count, excluded, target_id=None):
        prefs = self.db.get("training", {})
        if self.guard:
            self.guard()
        self.active_ai = True
        try:
            result = await self.ai.complete("daily_selection", provider, model,
            {"requested_count": count, "excluded_ids": excluded, "target_id": target_id,
             "difficulty_range": [prefs.get("minimum", 1000), prefs.get("maximum", 1600)],
             "training_preferences": prefs, "daily_budget": self.settings(),
             "candidates": [{k: p.get(k) for k in ("id", "title", "difficulty", "tags", "url")} for p in pool]}, DailySelection)
        finally:
            self.active_ai = False
        allowed = {item["id"]: item for item in pool}
        selected, seen = [], set()
        validated = DailySelection.model_validate(result.data)
        for item in validated.selected_problems:
            row = item.model_dump()
            pid = row.get("id")
            if pid not in allowed or pid in seen or pid in excluded:
                raise AIError("AI 返回了候选列表以外或重复的题目，未保存每日计划")
            seen.add(pid)
            selected.append({**allowed[pid], "reason": str(row.get("reason", ""))[:500]})
        if len(selected) != count:
            raise AIError(f"AI 未按要求返回 {count} 道不同题目，请重试")
        return selected

    def _trusted_ac(self, db, now):
        rows = {(r[0], r[1], r[2]) for r in db.execute(
            "SELECT DISTINCT platform,problem_id,submitted_at FROM submissions WHERE verdict='AC' AND submitted_at>0 AND submitted_at<=?", (now,))}
        # Workbench sync fetches judge evidence directly from the platform. Draft/self-test data is excluded.
        for row in db.execute("SELECT problem_id,judge_data FROM wb_attempts WHERE verdict='AC' AND submission_id IS NOT NULL AND handle<>'' AND last_synced>0"):
            try:
                judge = json.loads(row['judge_data'])
                ts = judge.get('creationTimeSeconds')
                if judge.get('verdict') in {'OK', 'AC'} and isinstance(ts, int) and 0 < ts <= now:
                    rows.add(('codeforces', row['problem_id'].removeprefix('codeforces:'), ts))
            except (ValueError, TypeError, AttributeError):
                continue
        return rows

    async def roll(self, payload):
        state = self.state()
        if not state['active']:
            raise HTTPException(409, '每日任务未开始或周期已结束，请先保存每日任务设置')
        if not state['plan'] or state['plan']['status'] != 'ready':
            values = self.request_generate(payload.provider, payload.model)
            await self.generate(values)
        else:
            if payload.task_id is None:
                raise HTTPException(422, '请选择要替换的一道每日题目')
            values = self.request_roll(payload.task_id, payload.provider, payload.model)
            await self._roll(values)
        return self.state()

    def complete(self, payload):
        # Compatibility endpoint: a button can refresh verified progress, never assert a solution.
        state = self.state()
        pid = payload.problem_id
        canonical = pid if ':' in pid else 'codeforces:' + pid
        with self.db.connect() as db:
            verified = {f"{platform}:{problem}" for platform, problem, ts in self._trusted_ac(db, time.time())
                        if datetime.fromtimestamp(ts, TZ).date().isoformat() == state['day']}
        if canonical not in verified:
            raise HTTPException(409, '尚未找到这道题今日的官方通过记录，请先提交并同步平台判题结果')
        return state

    def snapshot(self, now=None):
        current_time = time.time() if now is None else now
        today = self.today(current_time).isoformat()
        cycle = self._cycle(today)
        settings = self.settings()
        with self.db.connect() as db:
            db.execute("DELETE FROM daily_roll_reservations WHERE status='reserved' AND created_at<?", (int(time.time())-600,))
            db.execute("UPDATE daily_plans SET status='failed' WHERE status='generating' AND created_at<?", (int(time.time())-600,))
            plan = db.execute("SELECT * FROM daily_plans WHERE day=?", (today,)).fetchone()
            all_tasks = db.execute("SELECT id,day,data,problem_id FROM daily_plan_tasks WHERE active=1 ORDER BY ordinal").fetchall()
            rolls_used = db.execute("SELECT COUNT(*) FROM daily_roll_reservations WHERE day=? AND status IN ('reserved','used')", (today,)).fetchone()[0]
            # Only crawler/judge records count. Manual feedback, editor self-tests and AI claims never do.
            all_ac = self._trusted_ac(db, current_time)
            legacy_tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            legacy_count = db.execute('SELECT COUNT(*) FROM daily_completions').fetchone()[0] if 'daily_completions' in legacy_tables else 0
            plans = db.execute("SELECT day,goal_count FROM daily_plans WHERE status='ready' AND day<=?", (today,)).fetchall()
            legacy_k = []
            if {'daily_rolls', 'daily_completions', 'daily_checkins'} <= legacy_tables:
                for old in db.execute("SELECT day,goal FROM daily_checkins WHERE kind='k' AND day<=?", (today,)).fetchall():
                    completed = {row[0] for row in db.execute('SELECT problem_id FROM daily_completions WHERE day=?', (old['day'],))}
                    recommended = {row[0] for row in db.execute('SELECT problem_id FROM daily_rolls WHERE day=?', (old['day'],))}
                    legacy_k.append((old['day'], old['goal'], completed, recommended))
        by_day = {}
        for platform, pid, ts in all_ac:
            day = datetime.fromtimestamp(ts, TZ).date().isoformat()
            by_day.setdefault(day, set()).add((platform, pid))
        tasks_by_day = {}
        for task in all_tasks:
            tasks_by_day.setdefault(task['day'], []).append(task)
        k_days = set()
        for historical_plan in plans:
            day = historical_plan['day']
            required = [row['problem_id'].removeprefix('codeforces:') for row in tasks_by_day.get(day, [])]
            if len(required) == historical_plan['goal_count'] and required and all(
                    ('codeforces', pid) in by_day.get(day, set()) for pid in required):
                k_days.add(day)
        for day, goal, recorded, recommended in legacy_k:
            trusted = {f"{platform}:{pid}" for platform, pid in by_day.get(day, set())}
            if goal >= 1 and len(recorded & trusted) >= goal and recorded <= trusted and recommended & trusted:
                k_days.add(day)
        ordinary_days = {day for day, rows in by_day.items() if rows}
        month_start = date.fromisoformat(today).replace(day=1).isoformat()
        monthly_days = {day for day in ordinary_days if month_start <= day <= today}
        solved = {pid for platform, pid in by_day.get(today, set()) if platform == 'codeforces'}
        active_tasks = tasks_by_day.get(today, [])
        completed = [row['problem_id'] for row in active_tasks if row['problem_id'].removeprefix('codeforces:') in solved]
        snapshot = {"today": today, "timezone": "Asia/Shanghai", "settings": settings,
            "active": bool(cycle and cycle["starts_on"] <= today <= cycle["ends_on"]),
            "cycle": cycle, "plan": ({"status": plan['status'], "goal_count": plan["goal_count"], "tasks": [
                {**json.loads(row["data"]), "task_id": row["id"], "completed": row["problem_id"].removeprefix("codeforces:") in solved}
                for row in active_tasks], "rolls_remaining": max(0, 2-rolls_used), "completed_ids": completed} if plan else None),
            "sign_in": {"kind": "k" if today in k_days else ("ordinary" if today in ordinary_days else "none"),
                "monthly": {"ordinary": len(monthly_days-k_days), "k": len(monthly_days & k_days)},
                "total": {"ordinary": len(ordinary_days-k_days), "k": len(k_days)},
                "days": [{"date": day, "kind": "k" if day in k_days else "ordinary"} for day in sorted(ordinary_days)]}}
        sign = snapshot['sign_in']
        snapshot.update({'day': today, 'started_at': cycle['starts_on'] if cycle else None,
            'ends_at': cycle['ends_on'] if cycle else None, 'completed': len(by_day.get(today, set())),
            'locked_goal': plan['goal_count'] if plan else settings['daily_goal'],
            'rerolls_remaining': max(0, 2-rolls_used), 'rerolls_used': rolls_used,
            'legacy_notice': '旧版手动完成记录已保留为历史档案；签到统计现仅依据平台验证的通过记录。' if legacy_count else '',
            'checkin': {'kind': 'normal' if sign['kind']=='ordinary' else (sign['kind'] if sign['kind']=='k' else None),
                'month_normal': sign['monthly']['ordinary'], 'month_k': sign['monthly']['k'],
                'month_total': sum(sign['monthly'].values()), 'all_normal': sign['total']['ordinary'],
                'all_k': sign['total']['k'], 'all_total': sum(sign['total'].values())}})
        return snapshot

    def state(self):
        return self.snapshot()

    @staticmethod
    def _midnight(day):
        return int(datetime.combine(date.fromisoformat(day), datetime.min.time(), TZ).timestamp())


def register(app, daily):
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

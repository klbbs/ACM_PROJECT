"""Per-user daily practice plans and submission-derived check-ins."""
import asyncio
import json
import math
import time
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, model_validator

from .catalog import recommendation_candidates, refresh_recommendation_sessions
from .analytics import overview
from .training_topics import TOPICS
from .codex_bridge import AIError
from .runtime_logs import emit_event
from .selection_report_links import prepare_selection_reports

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
CREATE TABLE IF NOT EXISTS daily_task_times (
  task_id INTEGER PRIMARY KEY REFERENCES daily_plan_tasks(id),
  duration_minutes INTEGER NOT NULL CHECK(duration_minutes BETWEEN 1 AND 1440),
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_roll_history (
  day TEXT NOT NULL, problem_id TEXT NOT NULL, PRIMARY KEY(day,problem_id)
);
"""


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Settings(Strict):
    daily_goal: int = Field(default=1, ge=1, le=50, strict=True)
    duration_days: int = Field(default=30, ge=1, le=365, strict=True)
    selection_type: Literal["random", "specialist"] = "random"
    topic_id: str | None = Field(default=None, max_length=80)
    training_mode: Literal["learn", "consolidate", "variation", "strengthen"] = "learn"

    @model_validator(mode="after")
    def valid_topic(self):
        if self.selection_type == "specialist" and self.topic_id not in TOPICS:
            raise ValueError("请选择支持的训练专题")
        if self.selection_type == "random":
            self.topic_id = None
            self.training_mode = "learn"
        return self


    @model_validator(mode="before")
    @classmethod
    def discard_legacy_minutes(cls, value):
        if isinstance(value, dict):
            return {k: v for k, v in value.items() if k != "minutes_per_day"}
        return value


class TaskTime(Strict):
    task_id: int = Field(gt=0, strict=True)
    duration_minutes: int = Field(ge=1, le=1440, strict=True)


class SelectedProblem(Strict):
    id: str = Field(min_length=1, max_length=100)
    reason: str = Field(default="", max_length=500)


class DailySelection(Strict):
    selected_problems: list[SelectedProblem] = Field(max_length=50)


class DailyCountError(AIError):
    def __init__(self, required, actual):
        self.required, self.actual = required, actual
        super().__init__(f"每日题单要求 {required} 道，AI 实际返回 {actual} 道，本次未保存；已保存题单不受影响，请手动重试")


class GenerateRequest(Strict):
    provider: Literal["api", "codex"]
    model: str = Field(min_length=1, max_length=150)


class Roll(GenerateRequest):
    task_id: int | None = Field(default=None, gt=0, strict=True)


class Complete(Strict):
    problem_id: str = Field(min_length=1, max_length=100)


class Daily:
    def __init__(self, database, ai, accounts=None, event_log=None):
        self.db, self.ai, self.accounts = database, ai, accounts
        self.event_log = event_log
        self.specialist_training = None
        self.selection_reports = None
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
        generation_token = uuid.uuid4().hex
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
            db.execute("DELETE FROM web_settings WHERE key=?", (self._failure_key(day),))
            options = self._options(settings)
            db.execute("INSERT INTO web_settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (self._options_key(day), json.dumps(options, ensure_ascii=False)))
            db.execute("INSERT INTO web_settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (self._generation_key(day), json.dumps(generation_token)))
        # A generated plan snapshots its goal; later preference edits cannot lower today's K threshold.
        return {"day": day, "count": settings["daily_goal"], "provider": provider, "model": model,
                "training_options": options, "generation_token": generation_token}

    @staticmethod
    def _generation_key(day):
        return f"daily_plan_generation_token:{day}"

    @staticmethod
    def _options_key(day):
        return f"daily_plan_options:{day}"

    @staticmethod
    def _options(settings):
        return {key: settings[key] for key in ("selection_type", "topic_id", "training_mode")}

    def _plan_options(self, day):
        # Old plans always used random selection, regardless of newly edited preferences.
        raw = self.db.get(self._options_key(day), {})
        return self._options(Settings.model_validate(raw).model_dump())

    def request_roll(self, task_id, provider, model, today=None):
        day = today or self.today().isoformat()
        token = uuid.uuid4().hex
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM daily_roll_reservations WHERE status='reserved' AND created_at<?", (int(time.time())-600,))
            task = db.execute("SELECT problem_id,data,ordinal FROM daily_plan_tasks WHERE id=? AND day=? AND active=1", (task_id, day)).fetchone()
            plan = db.execute("SELECT status,goal_count FROM daily_plans WHERE day=?", (day,)).fetchone()
            used = db.execute("SELECT COUNT(*) FROM daily_roll_reservations WHERE day=? AND status IN ('reserved','used')", (day,)).fetchone()[0]
            if not task or not plan or plan[0] != "ready":
                raise HTTPException(409, "今天没有可换的每日题目")
            if db.execute("SELECT 1 FROM daily_roll_reservations WHERE day=? AND status='reserved'", (day,)).fetchone():
                raise HTTPException(409, "换题正在进行，请等待结果后再换题")
            if any(self._canonical(platform, pid) == task[0] and datetime.fromtimestamp(ts, TZ).date().isoformat() == day
                   for platform, pid, ts in self._trusted_ac(db, time.time())):
                raise HTTPException(409, "已完成的每日题目不能换题")
            if used >= plan['goal_count']:
                raise HTTPException(409, f"今天最多使用 {plan['goal_count']} 次换题机会，按今日锁定题量计算")
            db.execute("INSERT INTO daily_roll_reservations VALUES(?,?,?,?,?)", (token, day, task_id, "reserved", int(time.time())))
            excluded = {row[0] for row in db.execute("SELECT problem_id FROM daily_plan_tasks WHERE day=?", (day,))}
            excluded.update(row[0] for row in db.execute("SELECT problem_id FROM daily_roll_history WHERE day=?", (day,)))
            db.execute("INSERT OR IGNORE INTO daily_roll_history VALUES(?,?)", (day, task[0]))
        return {"day": day, "token": token, "task_id": task_id, "excluded_ids": sorted(excluded),
                "provider": provider, "model": model, "target_problem": json.loads(task['data']),
                "training_options": self._plan_options(day), "ordinal": task["ordinal"]}

    def _candidate_pool(self, count, excluded):
        prefs = self.db.get("training", {})
        pool = recommendation_candidates(self.db, self.accounts, minimum=prefs.get("minimum", 1000),
                          maximum=prefs.get("maximum", 1600), limit=120,
                          published_after=prefs.get("published_after"), excluded_ids=excluded)
        excluded = set(excluded)
        with self.db.connect() as db:
            excluded.update(self._canonical(platform, pid) for platform, pid, ts in self._trusted_ac(db, time.time()))
        return [row for row in pool if row["id"] not in excluded][:60]

    async def generate(self, payload):
        day, count = payload["day"], payload["count"]
        generation_token = payload.get("generation_token")
        try:
            with self.db.connect() as db:
                lease = db.execute("SELECT value FROM web_settings WHERE key=?", (self._generation_key(day),)).fetchone()
                current = db.execute("SELECT status FROM daily_plans WHERE day=?", (day,)).fetchone()
                if (not generation_token or not lease or json.loads(lease[0]) != generation_token
                        or not current or current[0] != 'generating'):
                    raise AIError('每日计划请求已失效，未开始选题或阅读报告，请刷新重试')
                history = [row[0] for row in db.execute('SELECT problem_id FROM daily_roll_history WHERE day=?', (day,))]
            chosen = await self._choose_with_reports(payload, count, history)
            if day != self.today().isoformat():
                raise AIError("日期已变化，请为新的一天重新生成任务")
            now = int(time.time())
            with self.db.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute("SELECT status FROM daily_plans WHERE day=?", (day,)).fetchone()
                lease = db.execute("SELECT value FROM web_settings WHERE key=?", (self._generation_key(day),)).fetchone()
                if (not current or current[0] != "generating" or not generation_token
                        or not lease or json.loads(lease[0]) != generation_token):
                    raise AIError("每日计划状态已变化，请重试")
                self._assert_no_battles(db, [row["id"] for row in chosen])
                db.execute("DELETE FROM daily_plan_tasks WHERE day=?", (day,))
                db.executemany("INSERT INTO daily_plan_tasks(day,ordinal,problem_id,data,active,created_at) VALUES(?,?,?,?,1,?)",
                    [(day, i, row["id"], json.dumps(row, ensure_ascii=False), now) for i, row in enumerate(chosen)])
                db.execute("UPDATE daily_plans SET status='ready' WHERE day=?", (day,))
                db.execute("DELETE FROM web_settings WHERE key=?", (self._failure_key(day),))
            emit_event(self.event_log, 'daily.generated', requested_count=count, actual_count=len(chosen))
            return {"day": day, "tasks": chosen}
        except BaseException as error:
            with self.db.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                lease = db.execute("SELECT value FROM web_settings WHERE key=?", (self._generation_key(day),)).fetchone()
                changed = 0
                if generation_token and lease and json.loads(lease[0]) == generation_token:
                    changed = db.execute("UPDATE daily_plans SET status='failed' WHERE day=? AND status='generating'", (day,)).rowcount
                if changed:
                    # Persist only our fixed descriptions, never upstream text or validation inputs.
                    failure = self._generation_failure(error)
                    db.execute("INSERT INTO web_settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (self._failure_key(day), json.dumps(failure, ensure_ascii=False)))
            if changed:
                details = {'reason': 'daily_' + failure['code'], 'requested_count': count}
                if isinstance(error, DailyCountError):
                    details['actual_count'] = error.actual
                emit_event(self.event_log, 'daily.failed', level='ERROR', **details)
            raise

    @staticmethod
    def _failure_key(day):
        return f"daily_generation_failure:{day}"

    @staticmethod
    def _generation_failure(error):
        # Messages are chosen locally so a provider cannot persist secrets in its errors.
        if isinstance(error, DailyCountError):
            return {"code": "invalid_count", "message": str(error)}
        reason = getattr(error, 'reason', None)
        if reason == 'ai_output_truncated':
            return {"code": "output_truncated", "message": "模型输出被截断，未保存题单；思考内容也可能占用输出预算，请在连接与设置中自行调整 token 上限或思考强度，再手动重试。"}
        if reason in {'ai_empty_final', 'ai_invalid_output', 'ai_response_incomplete', 'ai_response_refused'}:
            return {"code": "invalid_output", "message": "模型未返回完整、可解析的最终报告，未保存题单；不会用思考过程替代答案，也不会自动重试收费请求。"}
        if reason in {'ai_timeout', 'ai_network', 'ai_upstream_http'}:
            return {"code": "provider_unavailable", "message": "AI 服务连接失败、超时或拒绝请求，未保存题单；请检查提供方设置后手动重试。"}
        if reason == 'selection_report_configuration_changed' or getattr(error, 'selection_reason', None) == 'selection_report_configuration_changed':
            return {"code": "provider_configuration", "message": "生成期间 AI 配置已变化，未保存题单；请确认当前默认模型和提供方后重新发起。"}
        text = str(error) if isinstance(error, (AIError, HTTPException)) else ''
        if isinstance(error, ValidationError) or '格式' in text or 'JSON' in text or '结构' in text:
            return {"code": "invalid_output", "message": "AI 返回格式或题目数量不符合每日任务要求，未保存题单；请检查模型后手动重试。"}
        if '候选' in text or '题库中只有' in text:
            return {"code": "candidates_unavailable", "message": "符合条件的候选题不足、无效或资格已变化，未保存题单；请检查平台登录、题库与训练条件后重试。"}
        if '超时' in text or '网络' in text or 'HTTP' in text:
            return {"code": "provider_unavailable", "message": "AI 服务连接失败、超时或拒绝请求，未保存题单；请检查提供方设置后手动重试。"}
        if 'Key' in text or '模型' in text or '白名单' in text or '通道' in text:
            return {"code": "provider_configuration", "message": "AI 模型、密钥或提供方权限尚未配置好，未保存题单；请检查连接与设置后重试。"}
        if isinstance(error, (KeyboardInterrupt, SystemExit)) or type(error).__name__ == 'CancelledError':
            return {"code": "interrupted", "message": "每日题单生成已中断，未保存新题单；已有题目保留，可手动重试。"}
        return {"code": "generation_failed", "message": "每日题单生成失败，未保存新题单；已有题目保留，请检查设置后手动重试。"}

    async def _roll(self, payload):
        token, day, task_id = payload["token"], payload["day"], payload["task_id"]
        try:
            chosen = await self._choose_with_reports(payload, 1, payload["excluded_ids"], replacing=True)
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
                self._assert_no_battles(db, [replacement["id"]])
                prior = db.execute("SELECT problem_id FROM daily_plan_tasks WHERE id=?", (task_id,)).fetchone()[0]
                if any(self._canonical(platform, pid) == prior
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

    async def _choose_with_reports(self, payload, count, excluded, replacing=False):
        # Keep the entire operation (session refresh, initial selection and
        # reading reports) shorter than the existing ten-minute reservations.
        try:
            return await asyncio.wait_for(self._choose(payload, count, excluded, replacing), timeout=540)
        except asyncio.TimeoutError:
            raise AIError('每日选题与阅读报告超时，未保存题单且不扣换题次数；请稍后手动重试') from None

    async def _choose(self, payload, count, excluded, replacing):
        options = payload.get("training_options", self._plan_options(payload["day"]))
        if options["selection_type"] == "specialist":
            return await self._select_specialist(payload, count, excluded, replacing=replacing)
        await refresh_recommendation_sessions(self.accounts)
        pool = self._candidate_pool(count, excluded)
        if len(pool) < count:
            if replacing:
                raise AIError("没有新的候选题可替换，请同步题库或调整训练难度")
            raise AIError(f"题库中只有 {len(pool)} 道符合条件的未完成题，无法生成 {count} 道每日题目")
        return await self._select(payload["provider"], payload["model"], pool, count, excluded,
                                  payload["task_id"] if replacing else None,
                                  payload.get('target_problem') if replacing else None)

    async def _select_specialist(self, payload, count, excluded, replacing=False):
        if self.specialist_training is None:
            raise AIError("专项选题服务尚未配置，请稍后重试")
        options = payload.get("training_options", self._plan_options(payload["day"]))
        learn = options["training_mode"] == "learn"
        template_first = (payload.get("ordinal") == 0) if replacing and learn else None
        constraints = {}
        if replacing:
            bounds = self._replacement_difficulty_bounds(payload["day"], payload["task_id"], payload["ordinal"])
            if bounds:
                constraints["difficulty_bounds"] = bounds
        items = await self.specialist_training.select_items(
            options["topic_id"], options["training_mode"], count, payload["provider"], payload["model"],
            excluded_ids=excluded, template_first=template_first,
            forbid_templates=bool(replacing and learn and payload.get("ordinal") != 0), **constraints)
        if len(items) != count:
            raise DailyCountError(count, len(items))
        rows, seen, reports = [], set(), set()
        for item in items:
            pid = item["problem_id"]
            if pid in seen or pid in excluded or ':' not in pid:
                raise AIError("专项候选无效或重复，未保存每日计划")
            report_id = item.get('ai_report_id')
            if not isinstance(report_id, str) or not report_id.strip() or len(report_id) > 128 or report_id in reports:
                raise AIError('专项题目缺少匹配的阅读报告，未保存每日计划，请重试')
            seen.add(pid)
            reports.add(report_id)
            platform, raw = pid.split(':', 1)
            rows.append({**item, "id": pid, "problem_id": raw, "platform": platform})
        return rows

    def _replacement_difficulty_bounds(self, day, task_id, ordinal):
        # Ratings are native to each judge. Never compare or convert across platforms.
        bounds = {}
        with self.db.connect() as db:
            rows = db.execute("SELECT ordinal,data FROM daily_plan_tasks WHERE day=? AND active=1 AND id<>?",
                              (day, task_id)).fetchall()
        for row in rows:
            item = json.loads(row["data"])
            difficulty = item.get("difficulty")
            if type(difficulty) not in (int, float) or not math.isfinite(difficulty) or difficulty <= 0:
                continue
            platform = item.get("platform") or str(item.get("id", "")).split(':', 1)[0]
            if not platform or row["ordinal"] == ordinal:
                continue
            side = "minimum" if row["ordinal"] < ordinal else "maximum"
            limits = bounds.setdefault(platform, {})
            previous = limits.get(side)
            limits[side] = difficulty if previous is None else (max(previous, difficulty) if side == "minimum" else min(previous, difficulty))
        return bounds

    def _learning_evidence(self):
        # Only aggregate, source-derived training signals; no code, credentials or profiles.
        try:
            data = overview(self.db, days=30)
        except (ValueError, KeyError, TypeError):
            return {"days": 30, "available": False, "notice": "近期提交材料不足，不能推断薄弱专题"}
        return {"days": 30, "stats": data["stats"], "topics": data["topics"],
                "verdicts": data["verdicts"], "suggested_cf_difficulty": data["suggested_cf_difficulty"],
                "notice": "标签通过率仅为练习证据，不等同能力评分；无证据时不要虚构薄弱点"}

    async def _select(self, provider, model, pool, count, excluded, target_id=None, target_problem=None):
        prefs = self.db.get("training", {})
        if self.guard:
            self.guard()
        self.active_ai = True
        # Every transport receives today's exact count, including schema-less relay prompts.
        selection_schema = create_model('DailySelection', __base__=DailySelection,
            selected_problems=(list[SelectedProblem], Field(min_length=count, max_length=count)))
        try:
            result = await self.ai.complete("daily_selection", provider, model,
            {"requested_count": count, "excluded_ids": excluded, "target_id": target_id,
             "selection_mode": "single_replacement" if target_id is not None else "daily_plan",
             "replacement_target": ({key: target_problem.get(key) for key in
                 ('id', 'platform', 'title', 'difficulty', 'tags', 'reason')} if target_problem else None),
             "difficulty_range": [prefs.get("minimum", 1000), prefs.get("maximum", 1600)],
             "training_preferences": prefs, "daily_budget": self.settings(),
             "actual_time_evidence": self.evidence(), "recent_learning_evidence": self._learning_evidence(),
             "candidates": [{k: p.get(k) for k in ("id", "platform", "title", "difficulty", "difficulty_source", "native_difficulty", "difficulty_label", "tags", "url", "published_at", "published_at_source")} for p in pool]}, selection_schema)
            allowed = {item["id"]: item for item in pool}
            selected, seen = [], set()
            try:
                validated = DailySelection.model_validate(result.data)
            except ValidationError:
                raise AIError("AI 返回的每日题目格式不符合要求，未保存题单") from None
            for item in validated.selected_problems:
                row = item.model_dump()
                pid = row.get("id")
                if pid not in allowed or pid in seen or pid in excluded:
                    raise AIError("AI 返回了候选列表以外或重复的题目，未保存每日计划")
                seen.add(pid)
                selected.append({**allowed[pid], "reason": str(row.get("reason", ""))[:500]})
            if len(selected) != count:
                raise DailyCountError(count, len(selected))
            await refresh_recommendation_sessions(self.accounts)
            self._assert_current_selection(selected)
            selected = await prepare_selection_reports(self.selection_reports, selected, provider, model,
                context={'mode': 'random', 'mode_title': '每日随机训练'})
            await refresh_recommendation_sessions(self.accounts)
            self._assert_current_selection(selected)
            return selected
        finally:
            self.active_ai = False

    def _assert_current_selection(self, selected):
        # Read current preferences, not those captured before a potentially
        # lengthy model/statement-reading request.
        prefs = self.db.get("training", {})
        current = recommendation_candidates(self.db, self.accounts, minimum=prefs.get("minimum", 1000),
            maximum=prefs.get("maximum", 1600), limit=len(selected),
            preferred_ids=[row["id"] for row in selected])
        if not {row["id"] for row in selected} <= {row["id"] for row in current}:
            raise AIError("候选资格已变化（登录、训练日期、提交或对战），未保存题单，请重试")

    @staticmethod
    def _assert_no_battles(db, ids):
        row = db.execute("SELECT value FROM web_settings WHERE key='battle_problem_ids'").fetchone()
        if row and set(ids) & set(json.loads(row[0])):
            raise AIError("候选刚被分配为对战题，本次选题取消且不扣换题次数，请重试")

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
        if payload.task_id is not None:
            values = self.request_roll(payload.task_id, payload.provider, payload.model)
            await self._roll(values)
        elif state['plan'] and state['plan']['status'] == 'ready':
            raise HTTPException(422, '请选择要替换的一道每日题目')
        else:
            values = self.request_generate(payload.provider, payload.model)
            await self.generate(values)
        return self.state()

    def complete(self, payload):
        # Compatibility endpoint: a button can refresh verified progress, never assert a solution.
        state = self.state()
        pid = payload.problem_id
        canonical = pid if ':' in pid else 'codeforces:' + pid
        with self.db.connect() as db:
            verified = {self._canonical(platform, problem) for platform, problem, ts in self._trusted_ac(db, time.time())
                        if datetime.fromtimestamp(ts, TZ).date().isoformat() == state['day']}
        if canonical not in verified:
            raise HTTPException(409, '尚未找到这道题今日的官方通过记录，请先提交并同步平台判题结果')
        return state

    @staticmethod
    def _canonical(platform, pid):
        return pid if pid.startswith(platform + ':') else platform + ':' + pid

    def save_time(self, payload):
        battles = set(self.db.get("battle_problem_ids", []))
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute("SELECT day,problem_id FROM daily_plan_tasks WHERE id=? AND active=1",
                              (payload.task_id,)).fetchone()
            if not task:
                raise HTTPException(404, '找不到本人的每日任务')
            verified = {(self._canonical(platform, pid), datetime.fromtimestamp(ts, TZ).date().isoformat())
                        for platform, pid, ts in self._trusted_ac(db, time.time())}
            if task['problem_id'] in battles or (task['problem_id'], task['day']) not in verified:
                raise HTTPException(409, '请先同步这道题任务当日的官方 AC，再记录实际用时')
            db.execute("""INSERT INTO daily_task_times VALUES(?,?,?) ON CONFLICT(task_id)
                DO UPDATE SET duration_minutes=excluded.duration_minutes,updated_at=excluded.updated_at""",
                (payload.task_id, payload.duration_minutes, int(time.time())))
        return self.state()

    def evidence(self):
        battles = set(self.db.get("battle_problem_ids", []))
        # User estimates inform training; they never prove an AC or a check-in.
        with self.db.connect() as db:
            verified = {(self._canonical(platform, pid), datetime.fromtimestamp(ts, TZ).date().isoformat())
                        for platform, pid, ts in self._trusted_ac(db, time.time())}
            rows = db.execute("""SELECT t.problem_id,t.day,m.duration_minutes,m.updated_at
                FROM daily_task_times m JOIN daily_plan_tasks t ON t.id=m.task_id
                WHERE t.active=1 ORDER BY t.day DESC,m.updated_at DESC LIMIT 60""").fetchall()
        return [{**dict(row), 'source': 'self_reported', 'verified_ac': True}
                for row in rows if row['problem_id'] not in battles and (row['problem_id'], row['day']) in verified]

    def snapshot(self, now=None):
        current_time = time.time() if now is None else now
        today = self.today(current_time).isoformat()
        cycle = self._cycle(today)
        settings = self.settings()
        with self.db.connect() as db:
            db.execute("DELETE FROM daily_roll_reservations WHERE status='reserved' AND created_at<?", (int(time.time())-600,))
            db.execute("UPDATE daily_plans SET status='failed' WHERE status='generating' AND created_at<?", (int(time.time())-600,))
            plan = db.execute("SELECT * FROM daily_plans WHERE day=?", (today,)).fetchone()
            failure_row = db.execute("SELECT value FROM web_settings WHERE key=?", (self._failure_key(today),)).fetchone()
            times = {row["task_id"]: row["duration_minutes"] for row in db.execute("SELECT * FROM daily_task_times")}
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
        battles = set(self.db.get("battle_problem_ids", []))
        tasks_by_day = {}
        for task in all_tasks:
            tasks_by_day.setdefault(task['day'], []).append(task)
        k_days = set()
        for historical_plan in plans:
            day = historical_plan['day']
            required = [row['problem_id'] for row in tasks_by_day.get(day, [])]
            if len(required) == historical_plan['goal_count'] and required and all(
                    pid not in battles and pid in {self._canonical(platform, item) for platform, item in by_day.get(day, set())} for pid in required):
                k_days.add(day)
        for day, goal, recorded, recommended in legacy_k:
            trusted = {self._canonical(platform, pid) for platform, pid in by_day.get(day, set())} - battles
            if goal >= 1 and len(recorded & trusted) >= goal and recorded <= trusted and recommended & trusted:
                k_days.add(day)
        ordinary_days = {day for day, rows in by_day.items() if rows}
        month_start = date.fromisoformat(today).replace(day=1).isoformat()
        monthly_days = {day for day in ordinary_days if month_start <= day <= today}
        solved = {self._canonical(platform, pid) for platform, pid in by_day.get(today, set())} - battles
        active_tasks = tasks_by_day.get(today, [])
        completed = [row['problem_id'] for row in active_tasks if row['problem_id'] in solved]
        # Match today's immutable plan size, not editable preferences. Existing
        # successful/reserved rolls still count when upgrading from fixed quotas.
        rolls_limit = plan['goal_count'] if plan else settings['daily_goal']
        rolls_remaining = max(0, rolls_limit - rolls_used)
        failure = json.loads(failure_row[0]) if failure_row else {"code": "interrupted", "message": "上次每日题单未生成成功，未保存新题单；请检查设置后手动重试。"}
        options = self._plan_options(today) if plan else self._options(settings)
        snapshot = {"today": today, "locked_options": options, "timezone": "Asia/Shanghai", "settings": settings,
            "time_evidence": self.evidence(),
            "active": bool(cycle and cycle["starts_on"] <= today <= cycle["ends_on"]),
            "cycle": cycle, "plan": ({"status": plan['status'], "goal_count": plan["goal_count"],
                "failure": failure if plan['status'] == 'failed' else None, "training_options": options, "tasks": [
                {**json.loads(row["data"]), "task_id": row["id"], "duration_minutes": times.get(row["id"]), "completed": row["problem_id"] in solved}
                for row in active_tasks], "rolls_limit": rolls_limit, "rolls_remaining": rolls_remaining, "completed_ids": completed} if plan else None),
            "sign_in": {"kind": "k" if today in k_days else ("ordinary" if today in ordinary_days else "none"),
                "monthly": {"ordinary": len(monthly_days-k_days), "k": len(monthly_days & k_days)},
                "total": {"ordinary": len(ordinary_days-k_days), "k": len(k_days)},
                "days": [{"date": day, "kind": "k" if day in k_days else "ordinary"} for day in sorted(ordinary_days)]}}
        sign = snapshot['sign_in']
        snapshot.update({'day': today, 'started_at': cycle['starts_on'] if cycle else None,
            'ends_at': cycle['ends_on'] if cycle else None, 'completed': len(by_day.get(today, set())),
            'locked_goal': plan['goal_count'] if plan else settings['daily_goal'],
            'rerolls_limit': rolls_limit, 'rerolls_remaining': rolls_remaining, 'rerolls_used': rolls_used,
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

    @app.post('/api/daily/time')
    def daily_time(payload: TaskTime):
        return daily.save_time(payload)

    @app.post('/api/daily/complete')
    def daily_complete(payload: Complete):
        return daily.complete(payload)

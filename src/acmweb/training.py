"""Persistent, per-workspace specialist plans using verified catalog candidates."""
import json
import time
import uuid
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from .catalog import recommendation_candidates, refresh_recommendation_sessions
from .codex_bridge import AIError
from .training_topics import TOPICS, is_template, matches_topic, topic_groups

DDL = '''
CREATE TABLE IF NOT EXISTS training_plans (
    id TEXT PRIMARY KEY, topic_id TEXT NOT NULL, created_at INTEGER NOT NULL, data TEXT NOT NULL
);
'''
LOCK_KEY = 'specialist_generation_lock'


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Generate(Strict):
    topic_id: str = Field(min_length=1, max_length=80)
    count: int = Field(default=4, ge=3, le=8, strict=True)
    provider: Literal['api', 'codex']
    model: str = Field(min_length=1, max_length=150)


class Choice(Strict):
    id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)
    learning_level: int = Field(ge=1, le=8, strict=True)


class Training:
    def __init__(self, database, ai, accounts=None, event_log=None):
        self.db, self.ai, self.accounts, self.event_log = database, ai, accounts, event_log
        self.guard = None
        self.active_ai = False
        with self.db.connect() as db:
            db.executescript(DDL)

    def _ac_ids(self):
        now = int(time.time())
        with self.db.connect() as db:
            ids = {row['platform'] + ':' + row['problem_id'].removeprefix(row['platform'] + ':')
                   for row in db.execute("SELECT platform,problem_id FROM submissions WHERE verdict='AC' AND submitted_at>0 AND submitted_at<=?", (now,))}
            rows = db.execute("""SELECT a.problem_id,a.judge_data FROM wb_attempts a
                WHERE a.verdict='AC' AND a.submission_id IS NOT NULL AND a.handle<>'' AND a.last_synced>0
                AND NOT EXISTS (SELECT 1 FROM submissions s WHERE s.id=CAST(a.submission_id AS TEXT)
                    AND s.platform=substr(a.problem_id,1,instr(a.problem_id,':')-1)
                    AND CASE WHEN s.platform='codeforces' THEN lower(s.account)=lower(a.handle)
                             ELSE s.account=a.handle END)""").fetchall()
        for row in rows:
            try:
                platform = row['problem_id'].split(':', 1)[0]
                judge = json.loads(row['judge_data'] or '{}')
                timestamp = judge.get('creationTimeSeconds') if platform == 'codeforces' else judge.get('submitted_at')
                if (judge.get('verdict') in {'OK', 'AC'} and type(timestamp) is int and 0 < timestamp <= now
                        and judge.get('platform', platform) == platform
                        and (platform == 'codeforces' or judge.get('source') == 'official_remote_submission')):
                    ids.add(row['problem_id'])
            except (ValueError, TypeError, AttributeError):
                continue
        return ids

    def _decorate(self, data, completed=None):
        result = json.loads(data)
        completed = self._ac_ids() if completed is None else completed
        for item in result['items']:
            item['completed'] = item['problem_id'] in completed
        return result

    def plans(self):
        with self.db.connect() as db:
            rows = db.execute('SELECT data FROM training_plans ORDER BY created_at DESC,id DESC').fetchall()
        completed = self._ac_ids()
        return {'plans': [self._decorate(row['data'], completed) for row in rows]}

    def get_plan(self, plan_id):
        with self.db.connect() as db:
            row = db.execute('SELECT data FROM training_plans WHERE id=?', (plan_id,)).fetchone()
        if not row:
            raise HTTPException(404, '找不到本人的专项训练计划')
        return self._decorate(row['data'])

    def _pool(self, topic, preferred_ids=None):
        prefs = self.db.get('training', {})
        pool = recommendation_candidates(self.db, self.accounts,
            minimum=prefs.get('minimum', 800), maximum=prefs.get('maximum', 3500),
            published_after=prefs.get('published_after'), limit=3000,
            preferred_ids=preferred_ids or topic['template_ids'])
        completed = self._ac_ids()
        return [p for p in pool if p['id'] not in completed and matches_topic(p, topic)]

    def _lock(self):
        token, now = uuid.uuid4().hex, int(time.time())
        completed = self._ac_ids()
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT value FROM web_settings WHERE key=?', (LOCK_KEY,)).fetchone()
            if row:
                lease = json.loads(row[0])
                if lease['at'] > now - 600:
                    raise HTTPException(409, '专项选题正在进行，请等待后再试')
            unfinished = sum(any(item['problem_id'] not in completed for item in json.loads(row[0])['items'])
                for row in db.execute('SELECT data FROM training_plans'))
            if unfinished >= 100:
                raise HTTPException(409, '尚未完成的专项训练计划已达 100 个，请先完成现有训练并同步官方 AC 记录')
            db.execute('INSERT OR REPLACE INTO web_settings VALUES(?,?)', (LOCK_KEY, json.dumps({'token': token, 'at': now})))
        return token

    def _unlock(self, token):
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT value FROM web_settings WHERE key=?', (LOCK_KEY,)).fetchone()
            if row and json.loads(row[0]).get('token') == token:
                db.execute('DELETE FROM web_settings WHERE key=?', (LOCK_KEY,))

    async def generate(self, payload: Generate):
        topic = TOPICS.get(payload.topic_id)
        if topic is None:
            raise HTTPException(422, '请选择支持的训练专题')
        token = self._lock()
        try:
            await refresh_recommendation_sessions(self.accounts)
            pool = self._pool(topic)
            templates = [p['id'] for p in pool if is_template(p, topic)]
            if not templates:
                raise AIError('没有符合当前登录、日期及难度条件的未完成模板题；请同步平台题库并验证对应平台登录，或调整训练偏好；不会用普通题替代模板题')
            usable_count = 1 + sum(p['id'] not in templates for p in pool)
            if usable_count < payload.count:
                raise AIError(f'该专题只有 {usable_count} 道符合要求的模板及递进题，不足 {payload.count} 道；请同步题库、验证平台登录或调整训练偏好')
            # Keep template evidence and a bounded topic-specific prompt, never send unrelated catalog entries.
            candidates = [p for p in pool if p['id'] in templates][:8] + [p for p in pool if p['id'] not in templates][:92]
            templates = [p['id'] for p in candidates if p['id'] in templates]
            schema = create_model('SpecialistSelection', __base__=Strict,
                selected_problems=(list[Choice], Field(min_length=payload.count, max_length=payload.count)))
            if self.guard:
                self.guard()
            self.active_ai = True
            response = await self.ai.complete('specialist_selection', payload.provider, payload.model,
                {'topic': {k: topic[k] for k in ('id', 'title', 'description')},
                 'requested_count': payload.count, 'template_candidate_ids': templates,
                 'preferences': self.db.get('training', {}), 'candidates': candidates}, schema)
            try:
                selected = schema.model_validate(response.data).selected_problems
            except ValidationError:
                raise AIError('AI 返回的专项题单格式或数量不符合要求，本次未保存；请手动重试') from None
            by_id = {p['id']: p for p in candidates}
            ids = [row.id for row in selected]
            if len(set(ids)) != len(ids) or any(pid not in by_id for pid in ids):
                raise AIError('AI 返回了重复或不在官方候选库中的题目，本次未保存')
            if ids[0] not in templates or any(pid in templates for pid in ids[1:]):
                raise AIError('AI 未按要求安排首题为模板、后续为递进应用题，本次未保存')
            if [row.learning_level for row in selected] != list(range(1, payload.count + 1)):
                raise AIError('AI 未提供严格递进的学习难度层级，本次未保存')
            # Ratings are comparable only within the same source, never CF vs another platform.
            previous = {}
            for pid in ids:
                p = by_id[pid]
                rating = p.get('difficulty') if p['platform'] == 'codeforces' else p.get('native_difficulty')
                if type(rating) is int:
                    key = p['platform']
                    if key in previous and rating < previous[key]:
                        raise AIError('AI 返回的同平台难度顺序倒退，本次未保存')
                    previous[key] = rating
            # Expired login, newly solved problems or battle allocation must not leak into saved plans.
            current = {p['id'] for p in self._pool(topic, ids)}
            if not set(ids) <= current:
                raise AIError('候选资格已变化（登录、提交、日期或对战），本次未保存，请手动重试')
            now = int(time.time())
            items = []
            for i, choice in enumerate(selected):
                p = by_id[choice.id]
                stage = 'template' if i == 0 else 'foundation' if i == 1 else 'application' if i < payload.count - 1 else 'challenge'
                items.append({'position': i, 'problem_id': p['id'], 'title': p['title'],
                    'platform': p['platform'], 'url': p['url'], 'difficulty': p.get('difficulty') if p.get('difficulty') is not None else p.get('native_difficulty'),
                    'difficulty_label': p.get('difficulty_label') or p.get('difficulty_source') or '平台未标定难度',
                    'stage': stage, 'reason': choice.reason, 'completed': False})
            plan = {'id': uuid.uuid4().hex, 'topic_id': topic['id'], 'topic_title': topic['title'],
                    'title': topic['title'] + ' · 专项训练', 'created_at': now, 'items': items}
            with self.db.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                lease = db.execute('SELECT value FROM web_settings WHERE key=?', (LOCK_KEY,)).fetchone()
                if not lease or json.loads(lease[0]).get('token') != token:
                    raise HTTPException(409, '本次选题已超时，请重新生成')
                battle = db.execute("SELECT value FROM web_settings WHERE key='battle_problem_ids'").fetchone()
                if battle and set(ids) & set(json.loads(battle[0])):
                    raise AIError('候选刚被分配为对战题，本次未保存，请重试')
                db.execute('INSERT INTO training_plans VALUES(?,?,?,?)', (plan['id'], topic['id'], now, json.dumps(plan, ensure_ascii=False)))
            return plan
        finally:
            self.active_ai = False
            self._unlock(token)


def register(app, training):
    @app.get('/api/training/topics')
    def topics():
        return {'groups': topic_groups()}

    @app.get('/api/training/plans')
    def plans():
        return training.plans()

    @app.post('/api/training/plans')
    async def generate(payload: Generate):
        return await training.generate(payload)

    @app.get('/api/training/plans/{plan_id}')
    def plan(plan_id: str):
        return training.get_plan(plan_id)

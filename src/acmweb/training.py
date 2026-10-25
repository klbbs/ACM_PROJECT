"""Persistent, per-workspace specialist plans using verified catalog candidates."""
import asyncio
import json
import time
import uuid
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from .catalog import (PLATFORMS, PUBLISHED_SOURCES, _date_floor, _pool,
                      publication_time, recommendation_candidates,
                      refresh_recommendation_sessions, refresh_specialist_candidates,
                      verified_platforms)
from .codex_bridge import AIError
from .runtime_logs import emit_event
from .selection_report_links import prepare_selection_reports
from .training_topics import MODES, TOPICS, is_template, matches_topic, topic_groups

DDL = '''
CREATE TABLE IF NOT EXISTS training_plans (
    id TEXT PRIMARY KEY, topic_id TEXT NOT NULL, created_at INTEGER NOT NULL, data TEXT NOT NULL
);
'''
LOCK_KEY = 'specialist_generation_lock'


def _balanced_shortlist(pool, limit):
    # Shortlist across every eligible platform instead of letting a large
    # template collection consume the entire AI payload.
    grouped = {}
    for row in pool:
        grouped.setdefault(row['platform'], []).append(row)
    result = []
    for index in range(limit):
        progressed = False
        for rows in grouped.values():
            if index < len(rows) and len(result) < limit:
                result.append(rows[index])
                progressed = True
        if not progressed or len(result) >= limit:
            break
    return result


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Generate(Strict):
    topic_id: str = Field(min_length=1, max_length=80)
    mode: Literal['learn', 'consolidate', 'variation', 'strengthen'] = 'learn'
    count: int = Field(default=4, ge=1, le=50, strict=True)
    provider: Literal['api', 'codex']
    model: str = Field(min_length=1, max_length=150)


class Choice(Strict):
    id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)
    learning_level: int = Field(ge=1, le=50, strict=True)


class Training:
    def __init__(self, database, ai, accounts=None, event_log=None):
        self.db, self.ai, self.accounts, self.event_log = database, ai, accounts, event_log
        self.selection_reports = None
        self.guard = None
        self.active_ai = False
        self._selecting = False
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
        result.setdefault('mode', 'learn')
        result.setdefault('mode_title', next(m['title'] for m in MODES if m['id'] == result['mode']))
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
            preferred_ids=preferred_ids or topic['template_ids'],
            predicate=lambda p: matches_topic(p, topic), allow_attempted=True,
            prioritize=lambda p: is_template(p, topic))
        completed = self._ac_ids()
        return [p for p in pool if p['id'] not in completed]

    def _pool_diagnostics(self, topic, excluded, bounds):
        """Application-owned counts/reasons only; never expose credentials."""
        prefs = self.db.get('training', {})
        floor = _date_floor(prefs.get('published_after'))
        active = set(verified_platforms(self.accounts))
        configured = ({row.get('platform') for row in self.accounts.document().get('accounts', [])}
                      if callable(getattr(self.accounts, 'document', None)) else active)
        complete = self._ac_ids()
        names = {'codeforces': 'Codeforces', 'atcoder': 'AtCoder', 'luogu': '洛谷', 'nowcoder': '牛客'}
        details = []
        for platform in PLATFORMS:
            if platform not in active:
                if platform in configured:
                    details.append(names[platform] + '：登录未通过检测或检测已过期，请到连接与设置检测登录状态')
                continue
            raw = [p for p in _pool(self.db, platform, include_excluded=True) if matches_topic(p, topic)]
            templates = [p for p in raw if is_template(p, topic)]
            reasons = []
            if not raw:
                reasons.append('题库尚无该专题候选，需同步或等待官方列表可访问')
            elif not templates:
                reasons.append('官方候选中暂无可确认的模板题；可选择巩固、变式或加强')
            if floor is not None:
                unknown = sum(p.get('published_at_source') not in PUBLISHED_SOURCES
                    or not publication_time(p.get('published_at')) for p in templates)
                old = sum(p.get('published_at_source') in PUBLISHED_SOURCES
                    and publication_time(p.get('published_at')) and p['published_at'] < floor for p in templates)
                if unknown:
                    reasons.append(f'{unknown} 道模板缺少可靠发布日期')
                if old:
                    reasons.append(f'{old} 道模板早于 {prefs["published_after"]}')
            if platform == 'codeforces':
                outside = sum(type(p.get('difficulty')) is not int
                    or not prefs.get('minimum', 800) <= p['difficulty'] <= prefs.get('maximum', 3500) for p in templates)
                if outside:
                    reasons.append(f'{outside} 道模板不满足 CF {prefs.get("minimum", 800)}–{prefs.get("maximum", 3500)} 难度')
            done = sum(p['id'] in complete for p in templates)
            reserved = sum(p['id'] in excluded or not self._within_difficulty_bounds(p, bounds) for p in templates)
            if done:
                reasons.append(f'{done} 道模板已通过')
            if reserved:
                reasons.append(f'{reserved} 道模板与本次题单重复或不满足递进范围')
            details.append(names[platform] + '：' + ('；'.join(reasons) or f'该专题 {len(raw)} 道候选、{len(templates)} 道模板'))
        message = '。'.join(details)
        if floor is not None:
            message += f'。当前最早发布日期为 {prefs["published_after"]}，不会跳过日期限制；如需旧模板，请在训练偏好中自行调整或清除该日期'
        return message

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

    async def select_items(self, topic_id, mode, count, provider, model,
                           excluded_ids=(), template_first=None, forbid_templates=False, difficulty_bounds=None):
        """Select verified progressive items for standalone or daily training.

        This does not persist plans or take a standalone plan lease: the caller
        owns its daily/standalone lease. Shared AI budgets still apply here.
        """
        if self._selecting:
            raise HTTPException(409, '专项选题正在进行，请等待后再试')
        self._selecting = True
        try:
            return await asyncio.wait_for(self._select_items(topic_id, mode, count, provider, model,
                excluded_ids, template_first, forbid_templates, difficulty_bounds), timeout=540)
        except asyncio.TimeoutError:
            raise AIError('专项选题与阅读报告超时，本次未保存；请稍后手动重试') from None
        finally:
            self._selecting = False

    @staticmethod
    def _difficulty_bounds(bounds):
        if bounds is None:
            return {}
        if not isinstance(bounds, dict):
            raise HTTPException(422, '递进难度范围格式不正确')
        normalized = {}
        for platform, limits in bounds.items():
            if not isinstance(platform, str) or not isinstance(limits, dict) or set(limits) - {'minimum', 'maximum'}:
                raise HTTPException(422, '递进难度范围格式不正确')
            minimum, maximum = limits.get('minimum'), limits.get('maximum')
            if any(value is not None and type(value) is not int for value in (minimum, maximum)):
                raise HTTPException(422, '递进难度范围必须为整数或空值')
            normalized[platform] = {'minimum': minimum, 'maximum': maximum}
        return normalized

    @staticmethod
    def _within_difficulty_bounds(problem, bounds):
        platform = problem['platform']
        limits = bounds.get(platform)
        if not limits:
            return True
        rating = problem.get('difficulty') if platform == 'codeforces' else problem.get('native_difficulty')
        # Missing/zero ratings remain unknown. Never compare native platform
        # levels with Codeforces ratings, or invent a converted numeric score.
        if type(rating) is not int or rating <= 0:
            return True
        return ((limits['minimum'] is None or rating >= limits['minimum'])
                and (limits['maximum'] is None or rating <= limits['maximum']))

    async def _select_items(self, topic_id, mode, count, provider, model,
                            excluded_ids, template_first, forbid_templates, difficulty_bounds):
        # Validate callers as strictly as the public endpoint, including bool/int.
        request = Generate(topic_id=topic_id, mode=mode, count=count, provider=provider, model=model)
        topic = TOPICS.get(request.topic_id)
        if topic is None:
            raise HTTPException(422, '请选择支持的训练专题')
        template_first = mode == 'learn' if template_first is None else template_first
        if template_first and forbid_templates:
            raise HTTPException(422, '模板首题与排除模板不能同时设置')
        await refresh_recommendation_sessions(self.accounts)
        excluded = set(excluded_ids)
        bounds = self._difficulty_bounds(difficulty_bounds)
        pool = [p for p in self._pool(topic) if p['id'] not in excluded
                and self._within_difficulty_bounds(p, bounds)]
        templates = [p['id'] for p in pool if is_template(p, topic)]
        usable_count = 1 + sum(p['id'] not in templates for p in pool) if template_first and templates else len(pool)
        if forbid_templates:
            usable_count = sum(p['id'] not in templates for p in pool)
        refresh_notice = ''
        if ((template_first and not templates) or usable_count < count) and callable(getattr(self.accounts, 'document', None)):
            # The explicit Generate/Roll action authorizes metadata discovery;
            # browsing saved plans does not trigger I/O or an AI request.
            refresh_result = await asyncio.to_thread(refresh_specialist_candidates, self.db, self.accounts, topic)
            refresh_notice = '。'.join(row['platform'] + ' 补充题库失败：' + row['message']
                                      for row in refresh_result if row['status'] == 'failed')
            pool = [p for p in self._pool(topic) if p['id'] not in excluded
                    and self._within_difficulty_bounds(p, bounds)]
            templates = [p['id'] for p in pool if is_template(p, topic)]
        if template_first and not templates:
            emit_event(self.event_log, 'training.selection_failed', 'WARNING',
                reason='training_template_unavailable', train_mode=mode,
                candidate_count=len(pool), template_count=0, requested_count=count)
            raise AIError('没有符合条件的未完成模板题。' + self._pool_diagnostics(topic, excluded, bounds)
                + ('。' + refresh_notice if refresh_notice else '')
                + '。仅从你本人已验证登录的全部平台共同筛选，不会用普通题替代模板题')
        if forbid_templates:
            pool = [p for p in pool if p['id'] not in templates]
            templates = []
        usable_count = 1 + sum(p['id'] not in templates for p in pool) if template_first else len(pool)
        if usable_count < count:
            emit_event(self.event_log, 'training.selection_failed', 'WARNING',
                reason='training_candidates_unavailable', train_mode=mode,
                candidate_count=usable_count, template_count=len(templates), requested_count=count)
            raise AIError(f'该专题只有 {usable_count} 道符合要求的候选题，不足 {count} 道。'
                + self._pool_diagnostics(topic, excluded, bounds)
                + ('。' + refresh_notice if refresh_notice else ''))
        # Bound payload size while preserving enough ordinary candidates even for
        # a user-selected 50-item course; template evidence remains explicit.
        candidates = ((_balanced_shortlist([p for p in pool if p['id'] in templates], 8)
                       + _balanced_shortlist([p for p in pool if p['id'] not in templates], 192))
                      if template_first else _balanced_shortlist(pool, 200))
        templates = [p['id'] for p in candidates if p['id'] in templates]
        schema = create_model('SpecialistSelection', __base__=Strict,
            selected_problems=(list[Choice], Field(min_length=count, max_length=count)))
        if self.guard:
            self.guard()
        self.active_ai = True
        try:
            response = await self.ai.complete('specialist_selection', provider, model,
                {'topic': {k: topic[k] for k in ('id', 'title', 'description')},
                 'mode': mode, 'mode_title': next(m['title'] for m in MODES if m['id'] == mode),
                 'requested_count': count, 'template_first_required': template_first,
                 'forbid_templates': forbid_templates, 'template_candidate_ids': templates,
                 'preferences': self.db.get('training', {}), 'difficulty_bounds': bounds,
                 'candidates': candidates}, schema)
            try:
                selected = schema.model_validate(response.data).selected_problems
            except ValidationError:
                raise AIError('AI 返回的专项题单格式或数量不符合要求，本次未保存；请手动重试') from None
            by_id = {p['id']: p for p in candidates}
            ids = [row.id for row in selected]
            if len(set(ids)) != len(ids) or any(pid not in by_id for pid in ids):
                raise AIError('AI 返回了重复或不在官方候选库中的题目，本次未保存')
            if template_first and (ids[0] not in templates or any(pid in templates for pid in ids[1:])):
                raise AIError('AI 未按要求安排首题为模板、后续为递进应用题，本次未保存')
            if [row.learning_level for row in selected] != list(range(1, count + 1)):
                raise AIError('AI 未提供严格递进的学习难度层级，本次未保存')
            # Ratings are comparable only within one platform.
            previous = {}
            for pid in ids:
                problem = by_id[pid]
                rating = problem.get('difficulty') if problem['platform'] == 'codeforces' else problem.get('native_difficulty')
                if type(rating) is int and rating > 0:
                    key = problem['platform']
                    if key in previous and rating < previous[key]:
                        raise AIError('AI 返回的同平台难度顺序倒退，本次未保存')
                    previous[key] = rating
            await refresh_recommendation_sessions(self.accounts)
            current = {p['id'] for p in self._pool(topic, ids) if self._within_difficulty_bounds(p, bounds)}
            if not set(ids) <= current or set(ids) & excluded:
                raise AIError('候选资格已变化（登录、提交、日期或对战），本次未保存，请手动重试')
            items = []
            for i, choice in enumerate(selected):
                problem = by_id[choice.id]
                stage = mode if mode != 'learn' else ('template' if template_first and i == 0 else
                    'foundation' if i <= 1 else 'application' if i < count - 1 else 'challenge')
                items.append({'position': i, 'problem_id': problem['id'], 'title': problem['title'],
                    'platform': problem['platform'], 'url': problem['url'],
                    'difficulty': problem.get('difficulty') if problem.get('difficulty') is not None else problem.get('native_difficulty'),
                    'difficulty_label': problem.get('difficulty_label') or problem.get('difficulty_source') or '平台未标定难度',
                    'stage': stage, 'learning_level': choice.learning_level,
                    'reason': choice.reason, 'completed': False})
            items = await prepare_selection_reports(self.selection_reports, items, provider, model,
                context={'topic_id': topic['id'], 'topic_title': topic['title'], 'mode': mode,
                         'mode_title': next(m['title'] for m in MODES if m['id'] == mode)})
            # Reading all statements may outlive the five-minute login evidence.
            # Recheck the current account set and all constraints before a caller
            # is allowed to persist this exact set of reported problems.
            await refresh_recommendation_sessions(self.accounts)
            current = {p['id']: p for p in self._pool(topic, ids) if self._within_difficulty_bounds(p, bounds)}
            if not set(ids) <= current.keys() or set(ids) & excluded:
                raise AIError('候选资格在阅读报告期间已变化（登录、提交、日期或对战），本次未保存，请手动重试')
            if (template_first and (not is_template(current[ids[0]], topic)
                    or any(is_template(current[pid], topic) for pid in ids[1:]))) or (
                    forbid_templates and any(is_template(current[pid], topic) for pid in ids)):
                raise AIError('模板资格在阅读报告期间已变化，本次未保存，请手动重试')
            current_ratings = {}
            for pid in ids:
                problem = current[pid]
                rating = problem.get('difficulty') if problem['platform'] == 'codeforces' else problem.get('native_difficulty')
                if type(rating) is int and rating > 0:
                    platform = problem['platform']
                    if platform in current_ratings and rating < current_ratings[platform]:
                        raise AIError('同平台递进难度在阅读报告期间已变化，本次未保存，请手动重试')
                    current_ratings[platform] = rating
            emit_event(self.event_log, 'training.selected', train_mode=mode,
                candidate_count=len(candidates), template_count=len(templates), requested_count=count)
            return items
        finally:
            self.active_ai = False

    async def generate(self, payload: Generate):
        topic = TOPICS.get(payload.topic_id)
        if topic is None:
            raise HTTPException(422, '请选择支持的训练专题')
        token = self._lock()
        try:
            items = await self.select_items(payload.topic_id, payload.mode, payload.count,
                payload.provider, payload.model)
            ids = {item['problem_id'] for item in items}
            now = int(time.time())
            mode_title = next(m['title'] for m in MODES if m['id'] == payload.mode)
            plan = {'id': uuid.uuid4().hex, 'topic_id': topic['id'], 'topic_title': topic['title'],
                    'mode': payload.mode, 'mode_title': mode_title,
                    'title': topic['title'] + ' · ' + mode_title, 'created_at': now, 'items': items}
            with self.db.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                lease = db.execute('SELECT value FROM web_settings WHERE key=?', (LOCK_KEY,)).fetchone()
                if not lease or json.loads(lease[0]).get('token') != token:
                    raise HTTPException(409, '本次选题已超时，请重新生成')
                battle = db.execute("SELECT value FROM web_settings WHERE key='battle_problem_ids'").fetchone()
                if battle and ids & set(json.loads(battle[0])):
                    raise AIError('候选刚被分配为对战题，本次未保存，请重试')
                db.execute('INSERT INTO training_plans VALUES(?,?,?,?)', (plan['id'], topic['id'], now, json.dumps(plan, ensure_ascii=False)))
            return plan
        finally:
            self._unlock(token)


def register(app, training):
    @app.get('/api/training/topics')
    def topics():
        return {'groups': topic_groups(), 'modes': MODES, 'max_count': 50}

    @app.get('/api/training/plans')
    def plans():
        return training.plans()

    @app.post('/api/training/plans')
    async def generate(payload: Generate):
        return await training.generate(payload)

    @app.get('/api/training/plans/{plan_id}')
    def plan(plan_id: str):
        return training.get_plan(plan_id)

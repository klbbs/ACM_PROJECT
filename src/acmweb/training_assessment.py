"""Opt-in specialist coaching from an immutable, verified accepted submission.

This module never executes or submits code and never changes completion evidence.
Private source stays in the existing per-user workbench store.
"""
import asyncio
import hashlib
import inspect
import json
import re
import time
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .codex_bridge import AIError
from .problem_submissions import SourceRequest

DDL = '''CREATE TABLE IF NOT EXISTS specialist_assessments (
 plan_id TEXT NOT NULL, position INTEGER NOT NULL, attempt_id TEXT NOT NULL,
 fingerprint TEXT NOT NULL, assessment TEXT NOT NULL, messages TEXT NOT NULL,
 PRIMARY KEY(plan_id, position)
);'''
Text = Annotated[str, Field(min_length=1, max_length=1600)]


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Dimension(Strict):
    category: Literal['code_quality', 'boundaries', 'correctness', 'complexity', 'strategy']
    finding: Text
    evidence: Text
    suggestion: Text
    confidence: Literal['high', 'medium', 'low', 'uncertain']


class SpecialistAssessment(Strict):
    summary: Text
    dimensions: list[Dimension] = Field(min_length=1, max_length=10)
    strengths: list[Text] = Field(max_length=8)
    next_steps: list[Text] = Field(max_length=8)
    overall_confidence: Literal['high', 'medium', 'low', 'uncertain']


class SpecialistChat(Strict):
    reply: str = Field(min_length=1, max_length=6000)


class AssessmentRequest(Strict):
    provider: Literal['api', 'codex']
    model: str = Field(min_length=1, max_length=150)
    # A deliberate POST is the user's request; keep old clients compatible.
    consent: Literal[True] = True
    attempt_id: str | None = Field(default=None, min_length=1, max_length=100)
    submission_id: str | None = Field(default=None, pattern=r'^[1-9]\d{0,11}$')
    handle: str | None = Field(default=None, min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.-]+$')


class ChatRequest(Strict):
    provider: Literal['api', 'codex']
    model: str = Field(min_length=1, max_length=150)
    consent: Literal[True] = True
    message: str = Field(min_length=1, max_length=2000)


def _no_solution(value):
    # A guardrail supplements the unified prompt; never silently persist code.
    text = json.dumps(value, ensure_ascii=False)
    if re.search(r'```|#include|\bint\s+main\s*\(|\bdef\s+\w+\s*\(|public\s+static', text):
        raise AIError('AI 输出疑似包含完整实现，已拦截；请重试或缩小问题范围')


class TrainingAssessment:
    def __init__(self, database, ai, workbench, problem_submissions, training):
        self.db, self.ai, self.wb = database, ai, workbench
        self.submissions, self.training = problem_submissions, training
        self.pending = set()
        self.active_ai = False
        self.runtime_guard = None
        self.guard = None
        with self.db.connect() as conn:
            conn.executescript(DDL)

    def _item(self, plan_id, position):
        plan = self.training.get_plan(plan_id)
        item = next((row for row in plan['items'] if row['position'] == position), None)
        if item is None:
            raise HTTPException(404, '专项训练题目不存在')
        return plan, item

    def _sources(self, item):
        pid = item['problem_id']
        platform = pid.split(':', 1)[0]
        accounts = self.wb.accounts.document().get('accounts', [])
        def bound(handle):
            return any(a.get('platform') == platform and
                       ((a.get('handle', '').casefold() == (handle or '').casefold()) if platform == 'codeforces'
                        else a.get('handle') == handle) for a in accounts)
        result = []
        for row in self.wb.history(pid)['items']:
            if not row.get('submission_id') or not bound(row.get('account')):
                continue
            verified = False
            if row.get('attempt_id'):
                try:
                    attempt = self.wb.attempt(row['attempt_id'])
                    verified = self._verified(attempt, row, pid)
                except HTTPException:
                    pass
            accepted = row.get('verdict') == 'AC'
            fetchable = accepted and platform in {'codeforces', 'atcoder'} and not verified
            reason = '' if accepted and verified else (
                '仅支持已同步且官方判题为 AC 的提交' if not accepted else
                '递交 AI 时会尝试读取并核验该次官方源码' if fetchable else
                '记录已保留；此平台暂不支持读取并核验官方源码，不能用编辑器草稿替代')
            result.append({key: row.get(key) for key in ('attempt_id', 'submission_id', 'account', 'platform', 'language', 'verdict')} |
                          {'platform': platform, 'source_available': bool(accepted and verified), 'can_fetch_source': fetchable, 'disabled_reason': reason})
        return result

    @staticmethod
    def _verified(attempt, source, pid):
        left, right = attempt.get('handle') or '', source.get('account') or ''
        account_matches = left.casefold() == right.casefold() if pid.startswith('codeforces:') else left == right
        return bool(attempt.get('code_verified') and attempt.get('code', '').strip() and
                    len(attempt['code'].encode()) <= 65536 and attempt.get('language') and
                    attempt.get('judge_data') and attempt.get('verdict') == source.get('verdict') == 'AC' and
                    attempt.get('problem_id') == pid and account_matches and
                    str(attempt.get('submission_id')) == source.get('submission_id') and
                    attempt.get('language') == source.get('language') and
                    hashlib.sha256(attempt['code'].encode()).hexdigest() == attempt.get('code_hash'))

    def _snapshot(self, item, attempt_id):
        sources = self._sources(item)
        source = next((row for row in sources if row.get('attempt_id') == attempt_id and row['source_available']), None)
        if not source:
            raise AIError('提交源码、平台账号或官方判题已变化，请重新选择已通过的提交')
        attempt = self.wb.attempt(attempt_id)
        if not self._verified(attempt, source, item['problem_id']):
            raise AIError('该次提交源码无法核验；不会使用编辑器草稿替代')
        with self.db.connect() as conn:
            row = conn.execute('SELECT data FROM wb_problems WHERE id=?', (item['problem_id'],)).fetchone()
        if not row:
            raise AIError('尚未获取该题题面，请先获取题面后重试')
        statement = json.loads(row['data'])
        fingerprint = self.wb._review_fingerprint(attempt, statement)
        return attempt, statement, fingerprint

    def state(self, plan_id, position):
        _, item = self._item(plan_id, position)
        sources = self._sources(item)
        assessment, messages = None, []
        with self.db.connect() as conn:
            saved = conn.execute('SELECT * FROM specialist_assessments WHERE plan_id=? AND position=?', (plan_id, position)).fetchone()
        if saved:
            try:
                _, _, fingerprint = self._snapshot(item, saved['attempt_id'])
                if fingerprint == saved['fingerprint']:
                    assessment, messages = json.loads(saved['assessment']), json.loads(saved['messages'])
            except (AIError, HTTPException):
                pass
        return {'assessment': assessment, 'messages': messages, 'sources': sources,
                'completed': bool(item.get('completed')), 'busy': (plan_id, position) in self.pending}

    async def _guard(self):
        guard = self.runtime_guard or self.guard
        if guard:
            result = guard()
            if inspect.isawaitable(result):
                await result

    async def assess(self, plan_id, position, request):
        key = (plan_id, position)
        if self.pending:
            raise HTTPException(409, '专项训练 AI 请求正在进行中，请等待完成')
        self.pending.add(key)
        try:
            await self._guard()
            self.active_ai = True
            plan, item = self._item(plan_id, position)
            if not item.get('completed'):
                raise AIError('请先同步本题官方 AC 提交，再递交 AI 评估')
            sources = self._sources(item)
            chosen = [row for row in sources if
                      (not request.attempt_id or row.get('attempt_id') == request.attempt_id) and
                      (not request.submission_id or row['submission_id'] == request.submission_id) and
                      (not request.handle or (row['account'].casefold() == request.handle.casefold()
                          if item['problem_id'].startswith('codeforces:') else row['account'] == request.handle)) and row['verdict'] == 'AC']
            if not chosen:
                raise AIError('没有找到该平台账号对应的已同步 AC 提交，请先同步本题记录')
            if not request.attempt_id and not request.submission_id and len(chosen) > 1:
                raise AIError('有多次通过提交，请选择要评估的提交记录')
            if len(chosen) != 1:
                raise AIError('提交记录不唯一，请明确选择平台账号与提交号')
            source = chosen[0]
            if not source['source_available']:
                if not source['can_fetch_source']:
                    raise AIError(source['disabled_reason'])
                try:
                    imported = await asyncio.to_thread(self.submissions.source, item['problem_id'],
                        SourceRequest(handle=source['account'], submission_id=source['submission_id']))
                except Exception:
                    raise AIError('判题记录已保留，但该次官方源码暂时无法读取或核验；请确认平台登录后重试，不会使用草稿替代') from None
                source['attempt_id'] = imported.get('attempt_id')
            with self.db.connect() as conn:
                cached = conn.execute('SELECT 1 FROM wb_problems WHERE id=?', (item['problem_id'],)).fetchone()
            if not cached:
                try:
                    await asyncio.to_thread(self.wb.statement, item['problem_id'])
                except Exception:
                    raise AIError('题面获取失败，尚未发送源码到 AI；请获取完整题面后重试') from None
            attempt, statement, fingerprint = self._snapshot(item, source['attempt_id'])
            with self.db.connect() as conn:
                saved = conn.execute('SELECT fingerprint FROM specialist_assessments WHERE plan_id=? AND position=?', key).fetchone()
            if saved and saved['fingerprint'] == fingerprint:
                return self.state(*key) | {'busy': False}
            result = await self.ai.complete('specialist_assessment', request.provider, request.model,
                self._material(item, attempt, statement, plan), SpecialistAssessment)
            try:
                data = SpecialistAssessment.model_validate(result.data).model_dump()
            except ValidationError:
                raise AIError('AI 评估格式不符合约定，未保存本次结果，请重试') from None
            _no_solution(data)
            assessment = {'result': data, 'provenance': result.provenance, 'fingerprint': fingerprint,
                          'analyzed_at': int(time.time()), 'submission': self._submission(attempt)}
            with self.db.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                _, current_item = self._item(*key)
                _, _, current = self._snapshot(current_item, attempt['id'])
                if current != fingerprint:
                    raise AIError('提交源码、判题、账号或题面已变化，本次结果未保存，请重试')
                conn.execute('INSERT INTO specialist_assessments VALUES(?,?,?,?,?,?) ON CONFLICT(plan_id,position) DO UPDATE SET attempt_id=excluded.attempt_id,fingerprint=excluded.fingerprint,assessment=excluded.assessment,messages=excluded.messages',
                             (*key, attempt['id'], fingerprint, json.dumps(assessment, ensure_ascii=False), '[]'))
            return self.state(*key) | {'busy': False}
        finally:
            self.pending.discard(key)
            self.active_ai = False

    @staticmethod
    def _submission(attempt):
        return {'attempt_id': attempt['id'], 'submission_id': str(attempt['submission_id']),
                'account': attempt['handle'], 'platform': attempt['problem_id'].split(':')[0],
                'language': attempt['language'], 'verdict': attempt['verdict']}

    def _material(self, item, attempt, statement, plan=None):
        plan = plan or {}
        return {'topic': {'topic_id': plan.get('topic_id'), 'topic_title': plan.get('topic_title'),
                          'stage': item.get('stage')}, 'problem': statement,
                'submission': self._submission(attempt) | {'source_code': attempt['code'],
                 'source_code_sha256': attempt['code_hash'], 'judge_result': attempt['judge_data'],
                 'source_provenance': '已核验的该次官方 AC 提交源码'},
                'limits': '未运行代码；AC 不证明不存在未定义行为；判断仅为推测，不提供整题完整实现'}

    async def chat(self, plan_id, position, request):
        key = (plan_id, position)
        if self.pending:
            raise HTTPException(409, '专项训练 AI 请求正在进行中，请等待完成')
        if not request.message.strip():
            raise AIError('请输入需要讨论的问题')
        self.pending.add(key)
        try:
            await self._guard()
            self.active_ai = True
            plan, item = self._item(*key)
            with self.db.connect() as conn:
                saved = conn.execute('SELECT * FROM specialist_assessments WHERE plan_id=? AND position=?', key).fetchone()
            if not saved:
                raise AIError('请先递交该题已通过的代码进行 AI 评估')
            attempt, statement, fingerprint = self._snapshot(item, saved['attempt_id'])
            if fingerprint != saved['fingerprint']:
                raise AIError('评估所用证据已变化，请重新评估后继续讨论')
            messages = json.loads(saved['messages'])
            if len(messages) >= 24:
                raise AIError('本题本次评估已达到 12 轮讨论上限，请整理结论后继续训练')
            material = self._material(item, attempt, statement, plan)
            material.update(assessment=json.loads(saved['assessment'])['result'], messages=messages,
                            question=request.message.strip())
            result = await self.ai.complete('specialist_chat', request.provider, request.model, material, SpecialistChat)
            try:
                reply = SpecialistChat.model_validate(result.data).reply
            except ValidationError:
                raise AIError('AI 回复格式不符合约定，未保存本次回复，请重试') from None
            _no_solution(reply)
            now = int(time.time())
            messages += [{'role': 'user', 'content': request.message.strip(), 'created_at': now},
                         {'role': 'assistant', 'content': reply, 'created_at': now}]
            with self.db.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                _, current_item = self._item(*key)
                _, _, current = self._snapshot(current_item, attempt['id'])
                if current != fingerprint:
                    raise AIError('提交源码、判题、账号或题面已变化，本次回复未保存，请重新评估')
                # Compare the original row as well, so separate service instances
                # cannot overwrite a newer conversation with an older response.
                updated = conn.execute('UPDATE specialist_assessments SET messages=? WHERE plan_id=? AND position=? AND fingerprint=? AND messages=?',
                    (json.dumps(messages, ensure_ascii=False), *key, fingerprint, saved['messages'])).rowcount
                if not updated:
                    raise AIError('讨论内容已更新，请刷新后重试')
            return self.state(*key) | {'busy': False}
        finally:
            self.pending.discard(key)
            self.active_ai = False


def register(app, service):
    @app.get('/api/training/plans/{plan_id}/items/{position}/assessment')
    def state(plan_id: str, position: int):
        return service.state(plan_id, position)

    @app.post('/api/training/plans/{plan_id}/items/{position}/assessment')
    async def assess(plan_id: str, position: int, payload: AssessmentRequest):
        return await service.assess(plan_id, position, payload)

    @app.post('/api/training/plans/{plan_id}/items/{position}/chat')
    async def chat(plan_id: str, position: int, payload: ChatRequest):
        return await service.chat(plan_id, position, payload)

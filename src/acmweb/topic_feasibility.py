"""Private, bounded reading evidence for methods absent from native OJ tags.

Only explicit generation or an explicit check calls AI. Evidence never creates
an official tag, a template, a solution, or a completion record.
"""
import asyncio
import hashlib
import json
import re
import time
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from .ai import AIProvider
from .catalog import recommendation_candidates, refresh_recommendation_sessions
from .codex_bridge import AIError
from .difficulty_estimates import cf_reference, estimate, overlaps
from .prompts import PROMPT_VERSION, TASK_POLICIES
from .selection_reports import SelectionReports
from .training_topics import TOPICS, LUOGU_TOPIC_TAGS, matches_topic

MAX_CANDIDATES = 6
MAX_CHECK_SECONDS = 120
MAX_PROBLEM_SECONDS = 25
MIN_PROBLEM_SECONDS = 5
SUMMARY_PREFIX = 'topic_method_summary:'
# Coarse tags are discovery hints ONLY; they never approve a fine method.
PROBE_TAGS = {
    'luogu-2': {'strings', 'string suffix structures'}, 'luogu-3': {'dp'},
    'luogu-4': {'dfs and similar', 'brute force'}, 'luogu-5': {'math'},
    'luogu-6': {'graphs', 'trees'}, 'luogu-8': {'geometry'},
    'luogu-11': {'data structures', 'trees'}, 'luogu-13': {'games'},
    'luogu-44': {'data structures'}, 'luogu-69': {'fft', 'math'},
    'luogu-72': {'number theory', 'math'}, 'luogu-146': {'dp'},
    'luogu-230': {'trees'}, 'luogu-252': {'combinatorics'},
    'luogu-266': {'probabilities'}, 'luogu-271': {'matrices', 'math'},
}
DDL = '''CREATE TABLE IF NOT EXISTS topic_method_evidence (
 problem_id TEXT NOT NULL, topic_id TEXT NOT NULL, provider TEXT NOT NULL,
 model TEXT NOT NULL, statement_hash TEXT NOT NULL, method_hash TEXT NOT NULL,
 prompt_hash TEXT NOT NULL, decision TEXT NOT NULL, data TEXT NOT NULL,
 created_at INTEGER NOT NULL, PRIMARY KEY(problem_id, topic_id, provider, model)
);'''
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=800)]
_PROGRAM = re.compile(r'\b(?:int|void)\s+main\s*\(|public\s+static\s+void\s+main\s*\(|\bfunc\s+main\s*\(|if\s+__name__\s*==')


class MethodFeasibilityResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    problem_id: str = Field(min_length=1, max_length=200)
    topic_id: str = Field(min_length=1, max_length=80)
    decision: Literal['yes', 'no', 'uncertain']
    evidence: list[Text] = Field(min_length=1, max_length=6)
    complexity: Text
    limitations: list[Text] = Field(min_length=1, max_length=6)

    @model_validator(mode='after')
    def supported_yes(self):
        if self.decision == 'yes' and re.search(r'未知|无法(?:确定|确认)|不确定|unknown|uncertain|cannot determine|undetermined', self.complexity, re.I):
            raise ValueError('yes requires supported complexity')
        return self


def _method_hash(topic):
    material = {k: topic[k] for k in ('id', 'title', 'description', 'keywords', 'source_version') if k in topic}
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _prompt_hash():
    return hashlib.sha256((PROMPT_VERSION + TASK_POLICIES['topic_method_feasibility']).encode()).hexdigest()


def _parents(topic):
    current = topic
    if not topic.get('official_exact'):
        tags = LUOGU_TOPIC_TAGS.get(topic['id'], set())
        if tags:
            current = TOPICS.get('luogu-' + str(sorted(tags)[0]), topic)
    result, seen = [], set()
    parent = current.get('parent_id')
    while parent and parent not in seen:
        seen.add(parent)
        current = TOPICS.get(parent)
        if current is None:
            break
        result.append(current)
        parent = current.get('parent_id')
    return result


class TopicFeasibility:
    def __init__(self, database, accounts, workbench, ai):
        self.db, self.accounts, self.wb, self.ai = database, accounts, workbench, ai
        self.reader = SelectionReports(database, ai, workbench)
        self.active_ai = False
        self._configuration_token = None
        with self.db.connect() as connection:
            # Preserve evidence from early development builds while adding a
            # separate negative cache for every model, so switching back is free.
            columns = connection.execute('PRAGMA table_info(topic_method_evidence)').fetchall()
            if columns and sum(bool(row['pk']) for row in columns) == 2:
                connection.execute('BEGIN IMMEDIATE')
                connection.execute('ALTER TABLE topic_method_evidence RENAME TO topic_method_evidence_legacy')
                connection.execute(DDL.removesuffix(';'))
                connection.execute('INSERT INTO topic_method_evidence SELECT * FROM topic_method_evidence_legacy')
                connection.execute('DROP TABLE topic_method_evidence_legacy')
            connection.executescript(DDL)

    def _configuration_fingerprint(self):
        if not isinstance(self.ai, AIProvider):
            return None  # Isolated test transports do not have credentials.
        with self.ai.config_lock:
            config = self.ai.config()
            key = self.ai.key(config)
        # Ephemeral fingerprint only; no configuration or secrets are stored.
        return hashlib.sha256(json.dumps({'config': config, 'key': key}, sort_keys=True).encode()).hexdigest()

    def _assert_configuration(self):
        if self._configuration_fingerprint() != self._configuration_token:
            raise AIError('核查过程中默认 AI 配置已变化，本题未收录，请重新发起核查')

    def _records(self, topic_id):
        records = {}
        with self.db.connect() as connection:
            rows = connection.execute('SELECT * FROM topic_method_evidence WHERE topic_id=?', (topic_id,)).fetchall()
            for row in rows:
                try:
                    value = MethodFeasibilityResult.model_validate_json(row['data']).model_dump()
                    if (value['problem_id'] != row['problem_id'] or value['topic_id'] != topic_id
                            or value['decision'] != row['decision'] or row['provider'] not in {'api', 'codex'}
                            or not row['model'] or len(row['model']) > 150
                            or row['method_hash'] != _method_hash(TOPICS[topic_id])
                            or row['prompt_hash'] != _prompt_hash()
                            or not 0 < row['created_at'] <= int(time.time())):
                        continue
                    _, fingerprint = self.reader._cached(row['problem_id'], connection)
                    if fingerprint == row['statement_hash']:
                        records[(row['problem_id'], row['provider'], row['model'])] = {**dict(row), 'value': value}
                except (AIError, ValidationError, ValueError, TypeError):
                    continue
        return records

    def approved_map(self, topic_id):
        return {pid: {'matched': True, 'source': 'ai_method_feasibility', 'problem_id': pid,
                     'topic_id': topic_id, 'decision': 'yes', 'statement_hash': row['statement_hash'],
                     'provider': row['provider'], 'model': row['model'], 'created_at': row['created_at'],
                     'terms': row['value']['evidence'][:2],
                     'notice': 'AI 方法适用性推断，非官方标签、唯一解法或模板资格。'}
                for (pid, _, _), row in self._records(topic_id).items() if row['decision'] == 'yes'}

    def summary(self, topic_id):
        value = self.db.get(SUMMARY_PREFIX + topic_id, {})
        if not isinstance(value, dict) or value.get('method_hash') != _method_hash(TOPICS[topic_id]) or value.get('prompt_hash') != _prompt_hash():
            return {}
        return {k: value[k] for k in ('checked_count', 'accepted_count', 'uncertain_count', 'rejected_count',
            'cached_count', 'not_checked_count', 'attempted_count', 'updated_at', 'notice', 'incomplete') if k in value}

    def _pool(self, topic, minimum, maximum, excluded, bounds, *, related_only=False, pid=None):
        from .training import Training, completed_problem_ids
        # Use precisely the same completion policy as final training selection.
        complete = completed_problem_ids(self.db)
        prefs = self.db.get('training', {})
        low = minimum if minimum is not None else prefs.get('minimum', 800)
        high = maximum if maximum is not None else prefs.get('maximum', 3500)
        if low > high:
            raise HTTPException(422, 'CF rating 下限不能大于上限')
        reference = cf_reference(self.db)
        constrained = minimum is not None or maximum is not None
        parents = _parents(topic)
        probe_tags = set().union(*(PROBE_TAGS.get(p['id'], set()) for p in [topic, *parents]))
        def eligible(problem):
            return ((pid is None or problem['id'] == pid) and problem['id'] not in complete
                and problem['id'] not in excluded and not matches_topic(problem, topic)
                and Training._within_difficulty_bounds(problem, bounds)
                and (not constrained or overlaps(estimate(problem, reference), low, high))
                and (not related_only or any(matches_topic(problem, p) for p in parents)
                     or (problem['platform'] == 'codeforces' and bool(probe_tags & set(problem.get('tags', []))))))
        # Relevance is filtered BEFORE truncation, across all verified platforms.
        return recommendation_candidates(self.db, self.accounts, low, high,
            published_after=prefs.get('published_after'), limit=120, allow_attempted=True,
            excluded_ids=set(excluded) | complete, predicate=eligible)

    async def check(self, topic_id, provider, model, cf_minimum=None, cf_maximum=None,
                    *, excluded_ids=(), difficulty_bounds=None):
        from .training import Training
        topic = TOPICS.get(topic_id)
        if topic is None:
            raise HTTPException(422, '请选择支持的训练专题')
        if provider not in {'api', 'codex'} or not isinstance(model, str) or not 0 < len(model.strip()) <= 150:
            raise AIError('请先保存有效的默认 AI 模型')
        if self.active_ai:
            raise AIError('方法可行性核查正在进行，请等待完成')
        self._configuration_token = self._configuration_fingerprint()
        self.active_ai = True
        result = {'topic_id': topic_id, 'checked_count': 0, 'accepted_count': 0, 'uncertain_count': 0,
            'rejected_count': 0, 'cached_count': 0, 'not_checked_count': 0, 'attempted_count': 0,
            'items': [], 'timed_out': False}
        try:
            try:
                await asyncio.wait_for(self._check(topic, provider, model.strip(), cf_minimum, cf_maximum,
                    set(excluded_ids), Training._difficulty_bounds(difficulty_bounds), result,
                    time.monotonic() + MAX_CHECK_SECONDS), MAX_CHECK_SECONDS)
            except asyncio.TimeoutError:
                result['timed_out'] = True
            result['incomplete'] = bool(result['timed_out'] or result['not_checked_count'])
            result['updated_at'] = int(time.time())
            result['notice'] = ('最多核查 6 道实际官方题面；结论为 AI 方法适用性推断，不是官方标签、唯一解法或模板认证。'
                '未完成的读取不作为 AI 不确定结论；不运行、提交代码或改变完成状态。'
                + ('本次有未完成核查，已保留此前的证据；不会自动重试收费请求。' if result['incomplete'] else ''))
            self.db.set(SUMMARY_PREFIX + topic_id, {**{k: v for k, v in result.items() if k != 'items'},
                'method_hash': _method_hash(topic), 'prompt_hash': _prompt_hash()})
            return result
        finally:
            self.active_ai = False
            self._configuration_token = None

    async def _check(self, topic, provider, model, minimum, maximum, excluded, bounds, result, deadline):
        await refresh_recommendation_sessions(self.accounts)
        records = self._records(topic['id'])
        reusable = {key[0] for key, row in records.items() if row['decision'] == 'yes'
                    or (row['provider'] == provider and row['model'] == model)}
        related = self._pool(topic, minimum, maximum, excluded, bounds, related_only=True)
        pool = related + [p for p in self._pool(topic, minimum, maximum, excluded, bounds)
                          if p['id'] not in {r['id'] for r in related}]
        result['cached_count'] = sum(p['id'] in reusable for p in pool)
        pool = [p for p in pool if p['id'] not in reusable][:MAX_CANDIDATES]
        for index, problem in enumerate(pool):
            remaining = deadline - time.monotonic()
            if remaining < MIN_PROBLEM_SECONDS:
                result['timed_out'] = True
                result['not_checked_count'] += len(pool) - index
                break
            if self._configuration_fingerprint() != self._configuration_token:
                result['not_checked_count'] += len(pool) - index
                break
            result['attempted_count'] += 1
            try:
                entry = await asyncio.wait_for(self._one(problem, topic, provider, model,
                    minimum, maximum, excluded, bounds), min(MAX_PROBLEM_SECONDS, remaining))
            except asyncio.CancelledError:
                result['not_checked_count'] += len(pool) - index
                raise
            except HTTPException:
                raise
            except Exception:
                entry = {'problem_id': problem['id'], 'topic_id': topic['id'], 'decision': 'not_checked',
                    'message': '官方题面读取或 AI 核查未完成，未收录为方法可行；可手动重试'}
            result['items'].append(entry)
            if entry['decision'] == 'not_checked':
                result['not_checked_count'] += 1
                continue
            result['checked_count'] += 1
            result[{'yes': 'accepted_count', 'no': 'rejected_count', 'uncertain': 'uncertain_count'}[entry['decision']]] += 1

    async def _one(self, problem, topic, provider, model, minimum, maximum, excluded, bounds):
        pid = problem['id']
        material, fingerprint = await self.reader._read(pid)
        self._assert_configuration()
        # Recheck eligibility after statement fetch, before sending paid data.
        if not self._pool(topic, minimum, maximum, excluded, bounds, pid=pid):
            raise AIError('候选资格已变化，未发送方法核查')
        response = await self.ai.complete('topic_method_feasibility', provider, model,
            {'problem_id': pid, 'topic_id': topic['id'],
             'topic': {k: topic[k] for k in ('id', 'title', 'description')},
             'statement': material, 'statement_hash': fingerprint,
             'reading_limitations': ['只读提供的官方文字、公式、样例；图片地址不代表看懂图片；未运行代码。']},
            MethodFeasibilityResult)
        self._assert_configuration()
        value = MethodFeasibilityResult.model_validate(response.data).model_dump()
        if value['problem_id'] != pid or value['topic_id'] != topic['id']:
            raise AIError('核查题号或专题不匹配，未保存')
        if _PROGRAM.search(json.dumps(value)):
            raise AIError('核查包含疑似完整程序，未保存')
        # Only safe provenance is retained, never endpoint, credentials or input.
        provenance = getattr(response, 'provenance', {})
        if (provenance.get('model', model) != model or provenance.get('channel', provider) != provider
                or provenance.get('provider', provider) != provider
                or provenance.get('task', 'topic_method_feasibility') != 'topic_method_feasibility'
                or provenance.get('prompt_version', PROMPT_VERSION) != PROMPT_VERSION):
            raise AIError('核查模型来源不匹配，未保存')
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            # Completion/battle updates use the same workspace database. This
            # reserved transaction excludes concurrent writers until insertion.
            if not self._pool(topic, minimum, maximum, excluded, bounds, pid=pid):
                raise AIError('核查期间候选资格已变化，未保存')
            _, current = self.reader._cached(pid, connection)
            if current != fingerprint:
                raise AIError('题面缓存已更新，需重新核查，未保存旧证据')
            connection.execute('INSERT OR REPLACE INTO topic_method_evidence VALUES(?,?,?,?,?,?,?,?,?,?)',
                (pid, topic['id'], provider, model, fingerprint, _method_hash(topic), _prompt_hash(), value['decision'],
                 json.dumps(value, ensure_ascii=False), int(time.time())))
        return {**value, 'provider': provider, 'model': model,
            'notice': 'AI 静态阅读推断，不是官方标签、模板认证或判题结果。'}

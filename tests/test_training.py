"""Isolated checks; fake AI/catalog fixtures do not represent live OJ acceptance."""
import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from acmweb.ai import AIResult
from acmweb.codex_bridge import AIError
from acmweb.database import Database
from acmweb.training import Generate, Training, register
from acmweb.training_topics import TOPICS, topic_groups


@pytest.fixture
def training(tmp_path):
    db = Database(tmp_path / 'training.sqlite3')
    accounts = SimpleNamespace(credential_checks=SimpleNamespace(statuses=lambda: {'items': [
        {'platform': 'luogu', 'status': 'authenticated', 'login_verified': True}]}))
    ai = AsyncMock()
    service = Training(db, ai, accounts)
    rows = [('luogu', 'luogu:P3367', json.dumps({'id': 'luogu:P3367', 'title': '【模板】并查集',
        'platform': 'luogu', 'problem_id': 'P3367', 'url': 'https://www.luogu.com.cn/problem/P3367',
        'tags': [], 'native_difficulty': 2}))]
    rows.extend(('luogu', f'luogu:P{9000+i}', json.dumps({'id': f'luogu:P{9000+i}', 'title': f'并查集练习 {i}',
        'platform': 'luogu', 'problem_id': f'P{9000+i}', 'url': f'https://www.luogu.com.cn/problem/P{9000+i}',
        'tags': ['dsu'], 'native_difficulty': 2 + i})) for i in range(1, 6))
    with db.connect() as conn:
        conn.executemany('INSERT INTO problem_catalog VALUES(?,?,?)', rows)
    async def choose(task, provider, model, payload, schema):
        assert task == 'specialist_selection'
        assert payload['topic']['id'] == 'dsu'
        props = schema.model_json_schema()['properties']['selected_problems']
        assert props['minItems'] == props['maxItems'] == payload['requested_count']
        templates = payload['template_candidate_ids']
        ordered = sorted([p for p in payload['candidates'] if p['id'] in templates], key=lambda p: p['id'])[:1]
        ordered += sorted([p for p in payload['candidates'] if p['id'] not in templates], key=lambda p: p.get('native_difficulty', 0))
        return AIResult({'selected_problems': [{'id': p['id'], 'reason': '练习集合合并与连通性', 'learning_level': i + 1}
            for i, p in enumerate(ordered[:payload['requested_count']])]}, {'model': model})
    ai.complete.side_effect = choose
    return service


def request(**kwargs):
    return Generate(topic_id='dsu', count=4, provider='api', model='mock', **kwargs)


def generate(service):
    return asyncio.run(service.generate(request()))


def test_topics_have_clear_catalog_and_reference():
    groups = topic_groups()
    assert len(groups) == 8 and len(TOPICS) >= 20
    assert {'dsu', 'fenwick', 'kmp', 'shortest-path', 'knapsack'} <= TOPICS.keys()
    assert all(t['source_url'] == 'https://oi-wiki.org/' for g in groups for t in g['topics'])
    groups[0]['topics'][0]['title'] = 'changed'
    assert topic_groups()[0]['topics'][0]['title'] != 'changed'


def test_generate_exact_progressive_persisted_plan_and_no_daily_rows(training):
    plan = generate(training)
    assert len(plan['items']) == 4
    assert [p['stage'] for p in plan['items']] == ['template', 'foundation', 'application', 'challenge']
    assert plan['items'][0]['problem_id'] == 'luogu:P3367'
    assert all(not p['completed'] for p in plan['items'])
    assert training.get_plan(plan['id']) == plan
    restored = Training(training.db, training.ai, training.accounts)
    assert restored.plans()['plans'] == [plan]
    with training.db.connect() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='daily_plan_tasks'").fetchone()


def test_multiple_plans_persist_and_user_database_isolation(training, tmp_path):
    first, second = generate(training), generate(training)
    assert first['id'] != second['id'] and len(training.plans()['plans']) == 2
    other = Training(Database(tmp_path / 'other.sqlite3'), training.ai, training.accounts)
    assert other.plans() == {'plans': []}
    with pytest.raises(HTTPException) as error:
        other.get_plan(first['id'])
    assert error.value.status_code == 404


def test_completion_requires_official_ac_not_draft_or_feedback(training):
    plan = generate(training)
    with training.db.connect() as db:
        db.execute('INSERT INTO practice_feedback VALUES(?,?,?,?,?)', ('luogu', 'P3367', 'done', '', int(time.time())))
    assert not training.get_plan(plan['id'])['items'][0]['completed']
    with training.db.connect() as db:
        db.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)', ('luogu', 'demo', '123', 'P3367', int(time.time()), 'WA', '{}', int(time.time())))
    assert not training.get_plan(plan['id'])['items'][0]['completed']
    with training.db.connect() as db:
        db.execute("UPDATE submissions SET verdict='AC'")
    assert training.get_plan(plan['id'])['items'][0]['completed']


def test_no_template_clear_error_before_ai(training):
    with training.db.connect() as db:
        db.execute("DELETE FROM problem_catalog WHERE id='luogu:P3367'")
    with pytest.raises(AIError, match='模板题'):
        generate(training)
    training.ai.complete.assert_not_called()


def test_no_verified_platform_and_date_limit_are_respected(training):
    training.accounts.credential_checks.statuses = lambda: {'items': []}
    with pytest.raises(AIError, match='已验证登录'):
        generate(training)
    training.ai.complete.assert_not_called()
    training.accounts.credential_checks.statuses = lambda: {'items': [{'platform': 'luogu', 'status': 'authenticated', 'login_verified': True}]}
    training.db.set('training', {'published_after': '2025-01-01'})
    with pytest.raises(AIError, match='模板题'):
        generate(training)


def test_shortage_never_fabricates_problems(training):
    with training.db.connect() as db:
        db.execute("DELETE FROM problem_catalog WHERE id NOT IN ('luogu:P3367','luogu:P9001')")
    with pytest.raises(AIError, match='不足 4 道'):
        generate(training)
    training.ai.complete.assert_not_called()


@pytest.mark.parametrize('change,message', [
    ('count', '格式或数量'), ('unknown', '官方候选'), ('duplicate', '重复'),
    ('not-template', '首题为模板'), ('repeated-template', '首题为模板'),
    ('level', '严格递进'), ('descending', '难度顺序倒退'),
])
def test_invalid_ai_output_preserves_previous_and_does_not_retry(training, change, message):
    first = generate(training)
    original = training.ai.complete.side_effect
    async def invalid(*args):
        result = await original(*args)
        rows = result.data['selected_problems']
        if change == 'count': rows.append(dict(rows[-1]))
        if change == 'unknown': rows[-1]['id'] = 'luogu:P999999'
        if change == 'duplicate': rows[-1]['id'] = rows[1]['id']
        if change == 'not-template': rows[0], rows[1] = rows[1], rows[0]
        if change == 'repeated-template':
            # A second valid template must not become an application exercise.
            rows[-1]['id'] = 'luogu:P9999'
        if change == 'level': rows[-1]['learning_level'] = 1
        if change == 'descending': rows[1]['id'], rows[2]['id'] = rows[2]['id'], rows[1]['id']
        return result
    if change == 'repeated-template':
        with training.db.connect() as db:
            db.execute('INSERT INTO problem_catalog VALUES(?,?,?)', ('luogu', 'luogu:P9999', json.dumps({
                'id': 'luogu:P9999', 'title': '【模板】并查集扩展', 'platform': 'luogu', 'url': 'https://www.luogu.com.cn/problem/P9999', 'tags': ['dsu']})))
    training.ai.complete.side_effect = invalid
    before = training.ai.complete.call_count
    with pytest.raises(AIError, match=message):
        generate(training)
    assert training.ai.complete.call_count == before + 1
    assert training.plans()['plans'] == [first]


def test_model_failure_no_automatic_retry_or_raw_details_and_lock_clears(training):
    training.ai.complete.side_effect = AIError('请先配置 API Key')
    with pytest.raises(AIError, match='请先配置 API Key'):
        generate(training)
    assert training.ai.complete.call_count == 1
    assert training.db.get('specialist_generation_lock') is None
    assert training.plans()['plans'] == []


def test_concurrent_duplicate_click_does_not_start_second_call(training):
    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()
        original = training.ai.complete.side_effect
        async def waiting(*args):
            entered.set()
            await finish.wait()
            return await original(*args)
        training.ai.complete.side_effect = waiting
        pending = asyncio.create_task(training.generate(request()))
        await entered.wait()
        with pytest.raises(HTTPException) as error:
            await training.generate(request())
        assert error.value.status_code == 409
        finish.set()
        await pending
    asyncio.run(run())
    assert training.ai.complete.call_count == 1


def test_candidate_race_after_ai_output_is_rejected(training):
    original = training.ai.complete.side_effect
    async def choose_then_battle(*args):
        result = await original(*args)
        training.db.set('battle_problem_ids', [result.data['selected_problems'][-1]['id']])
        return result
    training.ai.complete.side_effect = choose_then_battle
    with pytest.raises(AIError, match='资格已变化'):
        generate(training)
    assert training.plans()['plans'] == []


def test_api_topic_and_plan_paths_and_strict_count(training):
    app = FastAPI()
    register(app, training)
    client = TestClient(app)
    assert client.get('/api/training/topics').status_code == 200
    assert client.get('/api/training/plans').json() == {'plans': []}
    for bad in [2, 9, True, '4']:
        assert client.post('/api/training/plans', json={**request().model_dump(), 'count': bad}).status_code == 422
    result = client.post('/api/training/plans', json=request().model_dump())
    assert result.status_code == 200
    assert client.get('/api/training/plans/' + result.json()['id']).json() == result.json()


def test_global_budget_refuses_before_ai_and_releases_lease(training):
    def full_budget():
        raise HTTPException(429, 'AI 任务繁忙，请稍后再试')
    training.guard = full_budget
    with pytest.raises(HTTPException) as error:
        generate(training)
    assert error.value.status_code == 429
    training.ai.complete.assert_not_called()
    assert training.active_ai is False
    assert training.db.get('specialist_generation_lock') is None


def test_active_flag_during_ai_and_reset_on_failure(training):
    async def fail(*args):
        assert training.active_ai is True
        raise AIError('请先配置 API Key')
    training.ai.complete.side_effect = fail
    with pytest.raises(AIError):
        generate(training)
    assert training.active_ai is False


def test_raw_cross_platform_difficulty_not_compared(training):
    # A Luogu level 2 may be followed by a CF rating 900 and then Luogu level 3;
    # comparing the raw numbers would wrongly reject this progression.
    training.accounts.credential_checks.statuses = lambda: {'items': [
        {'platform': p, 'status': 'authenticated', 'login_verified': True} for p in ('luogu', 'codeforces')]}
    training.db.set('training', {'minimum': 800, 'maximum': 2000})
    with training.db.connect() as db:
        db.execute('INSERT INTO problem_catalog VALUES(?,?,?)', ('codeforces', 'codeforces:1-A', json.dumps({
            'id': 'codeforces:1-A', 'title': 'DSU application', 'platform': 'codeforces', 'contest_id': 1,
            'difficulty': 900, 'url': 'https://codeforces.com/problemset/problem/1/A', 'tags': ['dsu']})))
    async def mixed(task, provider, model, payload, schema):
        return AIResult({'selected_problems': [{'id': pid, 'reason': '连接问题的渐进练习', 'learning_level': i + 1}
            for i, pid in enumerate(['luogu:P3367', 'codeforces:1-A', 'luogu:P9001', 'luogu:P9002'])]}, {})
    training.ai.complete.side_effect = mixed
    assert len(generate(training)['items']) == 4


def test_invalid_official_attempt_evidence_never_marks_complete(training):
    plan = generate(training)
    with training.db.connect() as db:
        db.execute('''INSERT INTO wb_attempts(id,problem_id,language,code,code_hash,created_at,
            handle,submission_id,verdict,judge_data,last_synced) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            ('attempt', 'luogu:P3367', 'cpp', '', 'hash', int(time.time()), 'demo', 1, 'AC',
             json.dumps({'verdict': 'AC', 'submitted_at': int(time.time()), 'platform': 'luogu', 'source': 'self_test'}), int(time.time())))
    assert not training.get_plan(plan['id'])['items'][0]['completed']
    with training.db.connect() as db:
        db.execute("UPDATE wb_attempts SET judge_data=? WHERE id='attempt'", (json.dumps({
            'verdict': 'AC', 'submitted_at': int(time.time()), 'platform': 'luogu', 'source': 'official_remote_submission'}),))
    assert training.get_plan(plan['id'])['items'][0]['completed']


def test_official_rejudge_overrides_older_workbench_ac(training):
    plan = generate(training)
    now = int(time.time())
    with training.db.connect() as db:
        db.execute('''INSERT INTO wb_attempts(id,problem_id,language,code,code_hash,created_at,
            handle,submission_id,verdict,judge_data,last_synced) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            ('attempt', 'luogu:P3367', 'cpp', '', 'hash', now, 'demo', 123, 'AC',
             json.dumps({'verdict': 'AC', 'submitted_at': now, 'platform': 'luogu', 'source': 'official_remote_submission'}), now))
        db.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)', ('luogu', 'demo', '123', 'P3367', now, 'WA', '{}', now))
    assert not training.get_plan(plan['id'])['items'][0]['completed']

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
    assert len(groups) == 22 and sum(len(g['topics']) for g in groups) == 262
    assert len(TOPICS) >= 290
    assert {'dsu', 'fenwick', 'kmp', 'shortest-path', 'knapsack'} <= TOPICS.keys()
    assert all(t['source_url'] == 'https://www.luogu.com.cn/_lfe/tags' for g in groups for t in g['topics'])
    assert all(t['path'] and t['source_version'] for g in groups for t in g['topics'])
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
    for bad in [0, 51, True, '4']:
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


def test_realistic_luogu_titles_and_numeric_official_tags_generate_topic_plan(training):
    # Real catalog shape: most titles do not name their algorithm; Luogu tag 47
    # is the official DSU category and may arrive as an int or string.
    with training.db.connect() as db:
        for i, title in enumerate(['亲戚', '奶酪', '朋友', '星空', '村庄'], start=1):
            row = json.loads(db.execute('SELECT data FROM problem_catalog WHERE id=?', (f'luogu:P{9000+i}',)).fetchone()[0])
            row.update(title=title, tags=[47 if i % 2 else '47'])
            db.execute('UPDATE problem_catalog SET data=? WHERE id=?', (json.dumps(row), row['id']))
    result = generate(training)
    assert len(result['items']) == 4
    assert result['items'][0]['problem_id'] == 'luogu:P3367'
    assert all('并查集练习' not in p['title'] for p in result['items'])


def test_topic_specific_numeric_tags_do_not_bleed_across_platform_or_subtypes():
    from acmweb.training_topics import matches_topic, is_template
    tree_template = {'platform': 'luogu', 'id': 'luogu:P9998', 'title': '【模板】树形动态规划', 'tags': ['3', '152', 'dp']}
    assert not matches_topic(tree_template, TOPICS['linear-dp'])
    assert not is_template(tree_template, TOPICS['linear-dp'])
    assert matches_topic(tree_template, TOPICS['tree-dp'])
    assert matches_topic({'platform': 'luogu', 'title': '某个普通标题', 'tags': ['444']}, TOPICS['linear-dp'])
    assert not matches_topic({'platform': 'atcoder', 'title': 'Ordinary Task', 'tags': ['47']}, TOPICS['dsu'])
    assert not matches_topic({'platform': 'atcoder', 'title': 'Ordinary Task', 'tags': []}, TOPICS['dsu'])
    assert not matches_topic({'platform': 'luogu', 'title': '数字集合', 'tags': ['235']}, TOPICS['string-hash'])
    assert matches_topic({'platform': 'luogu', 'title': '字符串比较', 'tags': ['235', '2']}, TOPICS['string-hash'])


def _stored_plan(number, problem_id):
    return {'id': f'old-plan-{number}', 'topic_id': 'dsu', 'topic_title': '并查集', 'title': '历史训练',
            'created_at': number, 'items': [{'problem_id': problem_id, 'completed': False}]}


def test_completed_historical_plans_release_unfinished_plan_quota(training):
    with training.db.connect() as db:
        for number in range(100):
            plan = _stored_plan(number, 'codeforces:100-A')
            db.execute('INSERT INTO training_plans VALUES(?,?,?,?)', (plan['id'], 'dsu', number, json.dumps(plan)))
        now = int(time.time())
        db.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)', ('codeforces', 'demo', '456', '100-A', now, 'AC', '{}', now))
    assert len(generate(training)['items']) == 4
    assert len(training.plans()['plans']) == 101


def test_one_hundred_unfinished_plans_blocks_ai_with_actionable_message(training):
    with training.db.connect() as db:
        for number in range(100):
            plan = _stored_plan(number, 'codeforces:100-A')
            db.execute('INSERT INTO training_plans VALUES(?,?,?,?)', (plan['id'], 'dsu', number, json.dumps(plan)))
    with pytest.raises(HTTPException, match='同步官方 AC') as error:
        generate(training)
    assert error.value.status_code == 409
    training.ai.complete.assert_not_called()
    assert training.db.get('specialist_generation_lock') is None


def test_expired_old_owner_cannot_release_new_generation_lease(training):
    old = training._lock()
    training.db.set('specialist_generation_lock', {'token': old, 'at': 1})
    new = training._lock()
    assert new != old
    training._unlock(old)
    assert training.db.get('specialist_generation_lock')['token'] == new
    training._unlock(new)
    assert training.db.get('specialist_generation_lock') is None


@pytest.mark.parametrize('mode', ['learn', 'consolidate', 'variation', 'strengthen'])
def test_four_modes_and_user_selected_single_question(training, mode):
    plan = asyncio.run(training.generate(Generate(topic_id='dsu', count=1, mode=mode, provider='api', model='mock')))
    assert plan['mode'] == mode
    assert len(plan['items']) == 1
    assert plan['items'][0]['learning_level'] == 1
    assert plan['items'][0]['stage'] == ('template' if mode == 'learn' else mode)
    payload = training.ai.complete.call_args.args[3]
    assert payload['template_first_required'] is (mode == 'learn')
    assert payload['mode'] == mode
    assert training.get_plan(plan['id']) == plan


@pytest.mark.parametrize('mode', ['consolidate', 'variation', 'strengthen'])
def test_only_learning_mode_requires_a_template(training, mode):
    with training.db.connect() as db:
        db.execute("DELETE FROM problem_catalog WHERE id='luogu:P3367'")
    plan = asyncio.run(training.generate(Generate(topic_id='dsu', count=4, mode=mode, provider='api', model='mock')))
    assert len(plan['items']) == 4
    assert all(item['stage'] == mode for item in plan['items'])


@pytest.mark.parametrize('count', [12, 50])
def test_full_supported_user_count_is_exact_and_progressive(training, count):
    with training.db.connect() as db:
        # Uniform official difficulty still permits AI learning levels: do not
        # fabricate 50 different platform scores from absent source evidence.
        for i in range(1, 61):
            row = {'id': f'luogu:P{9100+i}', 'title': f'集合问题 {i}', 'platform': 'luogu',
                   'problem_id': f'P{9100+i}', 'url': f'https://www.luogu.com.cn/problem/P{9100+i}',
                   'tags': ['dsu'], 'native_difficulty': 3}
            db.execute('INSERT INTO problem_catalog VALUES(?,?,?)', ('luogu', row['id'], json.dumps(row)))
    plan = asyncio.run(training.generate(Generate(topic_id='dsu', count=count, provider='api', model='mock')))
    assert len(plan['items']) == count
    assert len({item['problem_id'] for item in plan['items']}) == count
    assert [item['learning_level'] for item in plan['items']] == list(range(1, count + 1))
    assert plan['items'][0]['stage'] == 'template'
    assert all(item['stage'] != 'template' for item in plan['items'][1:])


def test_shared_selector_exclusions_and_non_template_daily_roll(training):
    items = asyncio.run(training.select_items('dsu', 'learn', 1, 'api', 'mock',
        excluded_ids=('luogu:P9001',), template_first=False, forbid_templates=True))
    assert items[0]['problem_id'] == 'luogu:P9002'
    payload = training.ai.complete.call_args.args[3]
    assert payload['template_first_required'] is False
    assert payload['forbid_templates'] is True
    assert payload['template_candidate_ids'] == []
    assert all(p['id'] not in {'luogu:P3367', 'luogu:P9001'} for p in payload['candidates'])
    assert training.plans()['plans'] == []
    assert training.db.get('specialist_generation_lock') is None
    assert training.active_ai is False


def test_shared_selector_forces_template_for_first_daily_roll(training):
    items = asyncio.run(training.select_items('dsu', 'consolidate', 1, 'api', 'mock', template_first=True))
    assert items[0]['problem_id'] == 'luogu:P3367'
    assert training.ai.complete.call_args.args[3]['template_first_required'] is True


def test_shared_selector_rejects_concurrent_daily_and_standalone_requests(training):
    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()
        original = training.ai.complete.side_effect
        async def waiting(*args):
            entered.set()
            await finish.wait()
            return await original(*args)
        training.ai.complete.side_effect = waiting
        pending = asyncio.create_task(training.select_items('dsu', 'learn', 1, 'api', 'mock'))
        await entered.wait()
        with pytest.raises(HTTPException) as err:
            await training.generate(request())
        assert err.value.status_code == 409
        assert training.db.get('specialist_generation_lock') is None
        assert training.active_ai is True
        finish.set()
        await pending
    asyncio.run(run())
    assert training.active_ai is False
    assert training._selecting is False
    assert training.ai.complete.call_count == 1


def test_official_catalog_hierarchy_and_exact_platform_scoped_matching():
    from acmweb.training_topics import matches_topic
    groups = topic_groups()
    rows = {t['id']: t for group in groups for t in group['topics']}
    assert len(rows) == 262
    assert 'luogu-47' in rows and rows['luogu-47']['parent_id'] == 'luogu-11'
    assert rows['luogu-47']['path'] == ['树形数据结构', '并查集']
    assert all(t['parent_id'] is None or t['parent_id'] in rows for t in rows.values())
    problem = {'platform': 'luogu', 'id': 'luogu:P99999', 'title': '普通标题', 'tags': [47]}
    assert matches_topic(problem, TOPICS['luogu-47'])
    assert matches_topic(problem, TOPICS['luogu-11'])
    assert not matches_topic(problem, TOPICS['luogu-3'])
    assert not matches_topic({**problem, 'platform': 'atcoder'}, TOPICS['luogu-47'])
    assert not matches_topic({**problem, 'tags': [999999], 'title': '并查集'}, TOPICS['luogu-47'])
    assert matches_topic({'platform': 'codeforces', 'tags': ['dsu']}, TOPICS['luogu-47'])
    assert matches_topic({'platform': 'codeforces', 'tags': ['dp']}, TOPICS['luogu-3'])
    assert not matches_topic({'platform': 'codeforces', 'tags': ['dp']}, TOPICS['luogu-444'])
    assert not matches_topic({'platform': 'codeforces', 'tags': ['data structures']}, TOPICS['luogu-47'])
    # Hidden aliases survive for old API callers but do not double menu entries.
    assert 'dsu' in TOPICS and 'dsu' not in rows


def test_old_plan_missing_mode_is_restored_with_learning_default(training):
    plan = _stored_plan(1, 'luogu:P3367')
    with training.db.connect() as db:
        db.execute('INSERT INTO training_plans VALUES(?,?,?,?)', (plan['id'], 'dsu', 1, json.dumps(plan)))
    restored = training.get_plan(plan['id'])
    assert restored['mode'] == 'learn'
    assert restored['mode_title'] == '新学'
    assert restored['items'][0]['problem_id'] == 'luogu:P3367'


def test_topics_endpoint_describes_modes_and_free_count_limit(training):
    app = FastAPI()
    register(app, training)
    response = TestClient(app).get('/api/training/topics').json()
    assert response['max_count'] == 50
    assert [m['id'] for m in response['modes']] == ['learn', 'consolidate', 'variation', 'strengthen']
    assert [m['title'] for m in response['modes']] == ['新学', '巩固', '变式', '加强']
    with pytest.raises(ValueError):
        Generate(topic_id='dsu', count=4, provider='api', model='mock', mode='hard')


def test_official_numeric_topic_generates_and_old_alias_still_works(training):
    with training.db.connect() as db:
        rows = db.execute('SELECT id,data FROM problem_catalog').fetchall()
        for row in rows:
            data = json.loads(row['data'])
            data['tags'] = [47]
            db.execute('UPDATE problem_catalog SET data=? WHERE id=?', (json.dumps(data), row['id']))
    original = training.ai.complete.side_effect
    async def canonical_choose(task, provider, model, payload, schema):
        assert payload['topic']['id'] == 'luogu-47'
        # Reuse this fixture's ordering, without changing the actual request.
        return await original(task, provider, model, {**payload, 'topic': {**payload['topic'], 'id': 'dsu'}}, schema)
    training.ai.complete.side_effect = canonical_choose
    plan = asyncio.run(training.generate(Generate(topic_id='luogu-47', count=4, provider='api', model='mock')))
    assert plan['topic_id'] == 'luogu-47'
    assert plan['items'][0]['problem_id'] == 'luogu:P3367'
    training.ai.complete.side_effect = original
    assert len(generate(training)['items']) == 4


def test_conflicting_template_roll_options_fail_before_ai(training):
    with pytest.raises(HTTPException) as error:
        asyncio.run(training.select_items('dsu', 'learn', 1, 'api', 'mock',
            template_first=True, forbid_templates=True))
    assert error.value.status_code == 422
    training.ai.complete.assert_not_called()
    assert training._selecting is False

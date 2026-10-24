import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from acmweb.daily import Complete, Daily, Roll, Settings, TaskTime, TZ
from acmweb.ai import AIResult
from acmweb.database import Database
from acmweb.codex_bridge import AIError
from acmweb.runtime_logs import RuntimeLogs


@pytest.fixture
def daily(tmp_path):
    db = Database(tmp_path / 'daily.sqlite3')
    ai = AsyncMock()
    accounts = SimpleNamespace(credential_checks=SimpleNamespace(statuses=lambda: {"items": [{"platform": "codeforces", "status": "authenticated", "login_verified": True}]}))
    service = Daily(db, ai, accounts)
    service.configure(Settings(daily_goal=2, duration_days=14))
    with db.connect() as conn:
        conn.executemany('INSERT INTO problem_catalog VALUES(?,?,?)', [
            ('codeforces', f'codeforces:{i}-A', json.dumps({'id': f'codeforces:{i}-A', 'problem_id': f'{i}-A',
                'platform': 'codeforces', 'title': f'p{i}', 'difficulty': 1000, 'tags': [], 'contest_id': i,
                'url': f'https://codeforces.com/problemset/problem/{i}/A'})) for i in range(1, 12)])
    async def choose(task, provider, model, payload, schema):
        assert task == 'daily_selection'
        output_schema = schema.model_json_schema()
        assert output_schema['title'] == 'DailySelection'
        selected_schema = output_schema['properties']['selected_problems']
        assert selected_schema['minItems'] == selected_schema['maxItems'] == payload['requested_count']
        return AIResult({'selected_problems': [{'id': row['id'], 'reason': '适合练习'}
            for row in payload['candidates'][:payload['requested_count']]]}, {'model': model})
    ai.complete.side_effect = choose
    return service


def ac(daily, pid, ts=None, platform='codeforces', sid=None):
    ts = int(time.time()) if ts is None else ts
    with daily.db.connect() as db:
        db.execute('INSERT OR REPLACE INTO submissions VALUES(?,?,?,?,?,?,?,?)',
            (platform, 'demo', sid or pid, pid, ts, 'AC', '{}', ts))


def generate(daily):
    return asyncio.run(daily.roll(Roll(provider='api', model='mock')))


@pytest.mark.parametrize('goal', [1, 2, 3, 4])
def test_exact_daily_plan_and_goal_count_single_problem_rerolls(daily, goal):
    daily.configure(Settings(daily_goal=goal))
    state = generate(daily)
    assert len(state['plan']['tasks']) == goal and state['rerolls_remaining'] == goal
    assert state['rerolls_limit'] == state['plan']['rolls_limit'] == goal
    initial_payload = daily.ai.complete.call_args.args[3]
    assert initial_payload['requested_count'] == goal and initial_payload['target_id'] is None
    assert initial_payload['selection_mode'] == 'daily_plan'
    seen = {task['id'] for task in state['plan']['tasks']}
    for remaining in reversed(range(goal)):
        task = state['plan']['tasks'][0]
        unchanged = state['plan']['tasks'][1:]
        state = asyncio.run(daily.roll(Roll(provider='api', model='mock', task_id=task['task_id'])))
        replacement = state['plan']['tasks'][0]
        assert replacement['id'] not in seen
        assert replacement['task_id'] != task['task_id']
        seen.add(replacement['id'])
        assert len(state['plan']['tasks']) == goal and state['rerolls_remaining'] == remaining
        assert state['plan']['rolls_remaining'] == remaining
        assert state['plan']['tasks'][1:] == unchanged
        payload = daily.ai.complete.call_args.args[3]
        assert payload['requested_count'] == 1 and payload['target_id'] == task['task_id']
        assert payload['selection_mode'] == 'single_replacement'
        assert payload['replacement_target']['id'] == task['id']
    daily.ai.complete.reset_mock()
    with pytest.raises(HTTPException, match=f'最多使用 {goal} 次'):
        asyncio.run(daily.roll(Roll(provider='api', model='mock', task_id=state['plan']['tasks'][0]['task_id'])))
    daily.ai.complete.assert_not_called()


@pytest.mark.parametrize('goal', [1, 3])
def test_without_plan_roll_quota_follows_saved_daily_goal(daily, goal):
    state = daily.configure(Settings(daily_goal=goal))
    assert state['plan'] is None
    assert state['locked_goal'] == goal
    assert state['rerolls_limit'] == goal
    assert state['rerolls_remaining'] == goal and state['rerolls_used'] == 0


@pytest.mark.parametrize('original_goal,new_goal', [(1, 3), (3, 1)])
def test_preference_edits_cannot_increase_or_reduce_locked_roll_quota(daily, original_goal, new_goal):
    daily.configure(Settings(daily_goal=original_goal))
    before = generate(daily)
    changed = daily.configure(Settings(daily_goal=new_goal))
    assert changed['settings']['daily_goal'] == new_goal
    assert changed['locked_goal'] == changed['plan']['goal_count'] == original_goal
    assert changed['rerolls_limit'] == changed['plan']['rolls_limit'] == original_goal
    assert changed['rerolls_remaining'] == original_goal
    assert changed['plan']['tasks'] == before['plan']['tasks']

    state = asyncio.run(daily.roll(Roll(provider='api', model='mock',
                                       task_id=changed['plan']['tasks'][0]['task_id'])))
    for preference in (original_goal, new_goal):
        changed = daily.configure(Settings(daily_goal=preference))
        assert changed['locked_goal'] == original_goal
        assert changed['rerolls_remaining'] == original_goal - 1
        assert changed['plan']['tasks'] == state['plan']['tasks']
    for _ in range(original_goal - 1):
        state = asyncio.run(daily.roll(Roll(provider='api', model='mock',
                                           task_id=state['plan']['tasks'][0]['task_id'])))
    changed = daily.configure(Settings(daily_goal=10))
    assert changed['rerolls_remaining'] == 0 and changed['plan']['goal_count'] == original_goal
    daily.ai.complete.reset_mock()
    with pytest.raises(HTTPException):
        asyncio.run(daily.roll(Roll(provider='api', model='mock',
                                    task_id=state['plan']['tasks'][0]['task_id'])))
    daily.ai.complete.assert_not_called()


@pytest.mark.parametrize('ordinal', [0, 1, 2])
def test_roll_replaces_clicked_task_only_and_preserves_other_stored_rows(daily, ordinal):
    daily.configure(Settings(daily_goal=3))
    before = generate(daily)
    target = before['plan']['tasks'][ordinal]
    with daily.db.connect() as db:
        untouched = {row['id']: dict(row) for row in db.execute(
            'SELECT * FROM daily_plan_tasks WHERE active=1 AND id<>?', (target['task_id'],))}
    after = asyncio.run(daily.roll(Roll(provider='api', model='mock', task_id=target['task_id'])))
    assert after['plan']['tasks'][ordinal]['task_id'] != target['task_id']
    assert after['plan']['tasks'][ordinal]['id'] != target['id']
    for index, task in enumerate(before['plan']['tasks']):
        if index != ordinal:
            assert after['plan']['tasks'][index] == task
    with daily.db.connect() as db:
        for task_id, stored in untouched.items():
            assert dict(db.execute('SELECT * FROM daily_plan_tasks WHERE id=?', (task_id,)).fetchone()) == stored
        assert db.execute('SELECT active FROM daily_plan_tasks WHERE id=?', (target['task_id'],)).fetchone()[0] == 0
    payload = daily.ai.complete.call_args.args[3]
    assert payload['requested_count'] == 1 and payload['target_id'] == target['task_id']
    assert payload['selection_mode'] == 'single_replacement'
    assert payload['replacement_target']['id'] == target['id']


@pytest.mark.parametrize('status', [None, 'generating', 'failed'])
def test_task_target_without_ready_plan_is_rejected_without_generating_plan(daily, status):
    if status is not None:
        daily.request_generate('api', 'mock')
        if status == 'failed':
            with daily.db.connect() as db:
                db.execute("UPDATE daily_plans SET status='failed'")
    before = daily.state()
    with pytest.raises(HTTPException):
        asyncio.run(daily.roll(Roll(provider='api', model='mock', task_id=999999)))
    daily.ai.complete.assert_not_called()
    assert daily.state()['plan'] == before['plan']
    assert daily.state()['rerolls_remaining'] == before['rerolls_remaining']
    with daily.db.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM daily_plan_tasks').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM daily_roll_reservations').fetchone()[0] == 0


def test_ready_plan_requires_explicit_task_target_without_ai_call(daily):
    before = generate(daily)
    daily.ai.complete.reset_mock()
    with pytest.raises(HTTPException) as error:
        generate(daily)
    assert error.value.status_code == 422
    daily.ai.complete.assert_not_called()
    assert daily.state()['plan'] == before['plan']


@pytest.mark.parametrize('target_kind', ['nonexistent', 'replaced', 'previous_day'])
def test_invalid_or_stale_task_target_does_not_call_ai_or_consume_quota(daily, monkeypatch, target_kind):
    before = generate(daily)
    target_id = before['plan']['tasks'][0]['task_id']
    if target_kind == 'nonexistent':
        target_id = 999999
    elif target_kind == 'replaced':
        asyncio.run(daily.roll(Roll(provider='api', model='mock', task_id=target_id)))
    else:
        tomorrow = daily.today() + timedelta(days=1)
        monkeypatch.setattr(daily, 'today', lambda now=None: tomorrow)
        generate(daily)
    before = daily.state()
    daily.ai.complete.reset_mock()
    with pytest.raises(HTTPException):
        asyncio.run(daily.roll(Roll(provider='api', model='mock', task_id=target_id)))
    daily.ai.complete.assert_not_called()
    after = daily.state()
    assert after['plan'] == before['plan']
    assert after['rerolls_remaining'] == before['rerolls_remaining']


def test_arbitrary_complete_and_feedback_cannot_forge_checkin(daily):
    with pytest.raises(HTTPException, match='官方通过记录'):
        daily.complete(Complete(problem_id='codeforces:4-A'))
    with daily.db.connect() as db:
        db.execute("INSERT INTO practice_feedback VALUES('codeforces','4-A','done','',?)", (int(time.time()),))
    assert daily.state()['checkin']['kind'] is None


def test_verified_any_platform_ac_normal_and_complete_full_plan_k(daily):
    state = generate(daily)
    tasks = state['plan']['tasks']
    ac(daily, 'abc300_a', platform='atcoder')
    assert daily.state()['checkin']['kind'] == 'normal'
    ac(daily, tasks[0]['problem_id'])
    assert daily.state()['checkin']['kind'] == 'normal'
    daily.configure(Settings(daily_goal=1))
    assert daily.state()['locked_goal'] == 2 and daily.state()['checkin']['kind'] == 'normal'
    with pytest.raises(HTTPException, match='已完成'):
        asyncio.run(daily.roll(Roll(provider='api', model='mock', task_id=tasks[0]['task_id'])))
    ac(daily, tasks[1]['problem_id'])
    state = daily.complete(Complete(problem_id=tasks[1]['id']))
    assert state['checkin']['kind'] == 'k'
    assert state['checkin']['month_k'] == state['checkin']['all_k'] == 1
    assert state['checkin']['month_normal'] == state['checkin']['all_normal'] == 0


@pytest.mark.parametrize('goal', [1, 2, 3])
def test_failed_roll_refunds_quota_and_keeps_original_plan(daily, goal):
    daily.configure(Settings(daily_goal=goal))
    before = generate(daily)
    daily.ai.complete.side_effect = AIError('upstream unavailable')
    with pytest.raises(AIError):
        asyncio.run(daily.roll(Roll(provider='api', model='mock', task_id=before['plan']['tasks'][0]['task_id'])))
    after = daily.state()
    assert after['rerolls_remaining'] == goal and after['plan']['tasks'] == before['plan']['tasks']
    assert after['rerolls_used'] == 0
    with daily.db.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM daily_roll_reservations WHERE status IN ('used','reserved')").fetchone()[0] == 0
    assert not daily.active_ai


def test_invalid_ai_id_or_count_does_not_create_plan(daily):
    for items in ([{'id': 'codeforces:99999-A'}], [{'id': 'codeforces:1-A'}],
                  [{'id': 'codeforces:1-A'}, {'id': 'codeforces:1-A'}]):
        daily.ai.complete.side_effect = None
        daily.ai.complete.return_value = AIResult({'selected_problems': items}, {})
        with pytest.raises(AIError):
            generate(daily)
        assert daily.state()['plan']['status'] == 'failed'


def test_four_daily_tasks_reject_five_model_choices_without_saving_tasks(daily):
    """A relay ignoring the count may fail the request, never expand today's goal."""
    daily.configure(Settings(daily_goal=4))
    daily.ai.complete.side_effect = None
    daily.ai.complete.return_value = AIResult({'selected_problems': [
        {'id': f'codeforces:{i}-A', 'reason': '练习'} for i in range(1, 6)
    ]}, {})
    with pytest.raises(AIError, match='要求 4 道，AI 实际返回 5 道'):
        generate(daily)
    state = daily.state()
    assert state['plan']['status'] == 'failed'
    assert state['plan']['goal_count'] == 4
    assert state['plan']['tasks'] == []
    assert state['plan']['failure']['code'] == 'invalid_count'
    assert '要求 4 道，AI 实际返回 5 道' in state['plan']['failure']['message']
    assert state['rerolls_remaining'] == 4
    with daily.db.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM daily_plan_tasks').fetchone()[0] == 0

    restored = Daily(Database(daily.db.path), daily.ai, daily.accounts)
    assert restored.state()['plan']['failure'] == state['plan']['failure']


def test_four_daily_tasks_survive_database_reopen_without_new_ai_call(daily):
    daily.configure(Settings(daily_goal=4))
    before = generate(daily)
    before = asyncio.run(daily.roll(Roll(provider='api', model='relay-model',
        task_id=before['plan']['tasks'][2]['task_id'])))
    daily.ai.complete.reset_mock()
    restored = Daily(Database(daily.db.path), daily.ai, daily.accounts)
    after = restored.state()
    assert after['plan'] == before['plan']
    assert len(after['plan']['tasks']) == after['locked_goal'] == 4
    assert after['rerolls_remaining'] == 3
    with pytest.raises(HTTPException, match='请选择要替换'):
        asyncio.run(restored.roll(Roll(provider='api', model='another-model')))
    daily.ai.complete.assert_not_called()
    assert restored.state()['plan'] == before['plan']


@pytest.mark.parametrize('error', [AIError('upstream secret-key-DO-NOT-STORE'),
                                  ValueError('raw secret-key-DO-NOT-STORE'), asyncio.CancelledError()])
def test_generation_failure_is_persistent_safe_and_preserves_previous_tasks(daily, error):
    before = generate(daily)
    # An interrupted/failed legacy regeneration may already have saved task rows.
    with daily.db.connect() as db:
        db.execute("UPDATE daily_plans SET status='failed'")
    choose = daily.ai.complete.side_effect
    daily.ai.complete.side_effect = error
    with pytest.raises(type(error)):
        generate(daily)
    restored = Daily(Database(daily.db.path), daily.ai, daily.accounts)
    failure_state = restored.state()
    assert failure_state['plan']['status'] == 'failed'
    assert failure_state['plan']['tasks'] == before['plan']['tasks']
    assert failure_state['plan']['failure']['message']
    with daily.db.connect() as db:
        stored = db.execute('SELECT value FROM web_settings WHERE key=?',
                            (daily._failure_key(daily.today().isoformat()),)).fetchone()[0]
        assert 'secret-key' not in stored and 'upstream' not in stored
    daily.ai.complete.side_effect = choose
    assert generate(restored)['plan']['failure'] is None
    with daily.db.connect() as db:
        assert db.execute('SELECT value FROM web_settings WHERE key=?',
                          (daily._failure_key(daily.today().isoformat()),)).fetchone() is None


def test_bad_daily_model_output_never_persists_validation_input_or_other_user_failure(daily, tmp_path):
    daily.ai.complete.side_effect = None
    daily.ai.complete.return_value = AIResult({'selected_problems': [{'id': {'secret': 'PRIVATE-INPUT'}}]}, {})
    with pytest.raises(AIError, match='每日题目格式') as error:
        generate(daily)
    assert 'PRIVATE-INPUT' not in str(error.value)
    assert daily.state()['plan']['failure']['code'] == 'invalid_output'
    with daily.db.connect() as db:
        assert 'PRIVATE-INPUT' not in db.execute('SELECT value FROM web_settings WHERE key=?',
            (daily._failure_key(daily.today().isoformat()),)).fetchone()[0]
    other = Daily(Database(tmp_path / 'other-daily.sqlite3'), daily.ai, daily.accounts)
    assert other.state()['plan'] is None


def test_daily_generation_writes_only_safe_counts_to_runtime_logs(daily, tmp_path):
    daily.event_log = RuntimeLogs(tmp_path / 'runtime-logs')
    daily.configure(Settings(daily_goal=4))
    choose = daily.ai.complete.side_effect

    async def selection_with_private_reason(*args):
        result = await choose(*args)
        for item in result.data['selected_problems']:
            item['reason'] = 'PRIVATE-TRAINING-REASON'
        return result

    daily.ai.complete.side_effect = selection_with_private_reason
    generate(daily)
    records = daily.event_log.listing()['items']
    assert len(records) == 1
    assert records[0]['event'] == 'daily.generated'
    assert records[0]['level'] == 'INFO'
    assert records[0]['details'] == {'requested_count': 4, 'actual_count': 4}
    raw = (daily.event_log.directory / 'runtime.jsonl').read_text()
    assert 'PRIVATE' not in raw and 'codeforces:' not in raw


@pytest.mark.parametrize('wrong_count', [True, False])
def test_daily_generation_failure_runtime_logs_include_safe_reason_not_upstream_text(daily, tmp_path, wrong_count):
    daily.event_log = RuntimeLogs(tmp_path / 'failure-runtime-logs')
    daily.configure(Settings(daily_goal=4))
    if wrong_count:
        daily.ai.complete.side_effect = None
        daily.ai.complete.return_value = AIResult({'selected_problems': [
            {'id': f'codeforces:{i}-A', 'reason': 'PRIVATE-SOURCE'} for i in range(1, 6)
        ]}, {})
    else:
        daily.ai.complete.side_effect = AIError('PRIVATE-COOKIE PRIVATE-KEY PRIVATE-SOURCE')
    with pytest.raises(AIError):
        generate(daily)
    records = daily.event_log.listing()['items']
    assert len(records) == 1
    assert records[0]['event'] == 'daily.failed'
    assert records[0]['level'] == 'ERROR'
    expected = {'reason': 'daily_invalid_count' if wrong_count else 'daily_generation_failed', 'requested_count': 4}
    if wrong_count:
        expected['actual_count'] = 5
    assert records[0]['details'] == expected
    raw = (daily.event_log.directory / 'runtime.jsonl').read_text()
    assert 'PRIVATE' not in raw and 'codeforces:' not in raw


def test_not_enough_candidates_no_billed_ai_call(daily):
    with daily.db.connect() as db:
        db.execute('DELETE FROM problem_catalog')
    with pytest.raises(AIError, match='无法生成'):
        generate(daily)
    daily.ai.complete.assert_not_called()


@pytest.mark.parametrize('goal', [1, 2, 3])
def test_concurrent_roll_reservation_atomic_across_service_instances(daily, goal):
    daily.configure(Settings(daily_goal=goal))
    state = generate(daily)
    second = Daily(daily.db, daily.ai)
    token = daily.request_roll(state['plan']['tasks'][0]['task_id'], 'api', 'mock')
    with pytest.raises(HTTPException, match='正在进行'):
        second.request_roll(state['plan']['tasks'][-1]['task_id'], 'api', 'mock')
    assert second.state()['rerolls_remaining'] == goal - 1
    with daily.db.connect() as db:
        db.execute("UPDATE daily_roll_reservations SET created_at=? WHERE token=?", (int(time.time())-601, token['token']))
    assert second.state()['rerolls_remaining'] == goal


@pytest.mark.parametrize('goal', [1, 3])
def test_simultaneous_roll_requests_reserve_only_one_quota_slot(daily, goal):
    daily.configure(Settings(daily_goal=goal))
    state = generate(daily)
    second = Daily(daily.db, daily.ai)
    barrier = Barrier(2)

    def reserve(service):
        barrier.wait(timeout=5)
        try:
            return service.request_roll(state['plan']['tasks'][0]['task_id'], 'api', 'mock')
        except HTTPException as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(reserve, service) for service in (daily, second)]
        results = [future.result(timeout=10) for future in futures]
    successes = [result for result in results if isinstance(result, dict)]
    rejections = [result for result in results if isinstance(result, HTTPException)]
    assert len(successes) == len(rejections) == 1
    assert rejections[0].status_code == 409
    assert daily.state()['rerolls_used'] == 1
    assert daily.state()['rerolls_remaining'] == goal - 1
    with daily.db.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM daily_roll_reservations WHERE status='reserved'").fetchone()[0] == 1


def test_upstream_cancel_refunds_roll(daily):
    state = generate(daily)
    daily.ai.complete.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(daily.roll(Roll(provider='api', model='mock', task_id=state['plan']['tasks'][0]['task_id'])))
    assert daily.state()['rerolls_remaining'] == 2 and not daily.active_ai


def test_month_stats_exclude_future_and_count_distinct_days(daily):
    today = daily.today()
    today_ts = daily._midnight(today.isoformat()) + 10
    prior = today - timedelta(days=1)
    previous_month = today.replace(day=1) - timedelta(days=1)
    ac(daily, '1-A', ts=today_ts)
    ac(daily, '2-A', ts=today_ts, sid='duplicate')
    ac(daily, '3-A', ts=daily._midnight(prior.isoformat())+10)
    ac(daily, '4-A', ts=daily._midnight(previous_month.isoformat())+10)
    ac(daily, '5-A', ts=int(time.time())+86400)
    state = daily.state()
    expected_month = 2 if prior.month == today.month else 1
    assert state['checkin']['month_total'] == expected_month
    assert state['checkin']['all_total'] == len({today, prior, previous_month})
    assert state['checkin']['all_k'] == 0


def test_legacy_history_preserved_quota_migrated_but_manual_claims_untrusted(daily):
    day = daily.today().isoformat()
    with daily.db.connect() as db:
        db.executescript('''CREATE TABLE daily_rolls(day TEXT,seq INT,problem_id TEXT,data TEXT,created_at INT);
            CREATE TABLE daily_completions(day TEXT,problem_id TEXT,created_at INT);
            CREATE TABLE daily_checkins(day TEXT,kind TEXT,completed INT,goal INT,updated_at INT);''')
        for i in range(1, 4):
            db.execute('INSERT INTO daily_rolls VALUES(?,?,?,?,?)', (day, i, f'codeforces:{i}-A', '{}', 1))
        db.execute('INSERT INTO daily_completions VALUES(?,?,?)', (day, 'codeforces:1-A', 1))
        db.execute('INSERT INTO daily_checkins VALUES(?,?,?,?,?)', (day, 'k', 1, 1, 1))
    migrated = Daily(daily.db, daily.ai)
    state = migrated.state()
    assert state['rerolls_remaining'] == 0 and state['legacy_notice']
    assert state['checkin']['kind'] is None
    ac(daily, '1-A')
    assert migrated.state()['checkin']['kind'] == 'k'
    with daily.db.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM daily_checkins').fetchone()[0] == 1


def test_cycle_duration_locked_and_validation_strict(daily):
    before = daily.state()
    after = daily.configure(Settings(daily_goal=3, duration_days=365, minutes_per_day=90))
    assert before['ends_at'] == after['ends_at']
    for bad in ({'daily_goal': 0}, {'daily_goal': True}, {'duration_days': 366}):
        with pytest.raises(Exception):
            Settings(**bad)


def test_guard_rejection_roll_refunds_reservation(daily):
    before = generate(daily)
    daily.guard = lambda: (_ for _ in ()).throw(HTTPException(429, 'busy'))
    with pytest.raises(HTTPException):
        asyncio.run(daily.roll(Roll(provider='api', model='mock', task_id=before['plan']['tasks'][0]['task_id'])))
    assert daily.state()['rerolls_remaining'] == 2 and not daily.active_ai


def test_roll_race_original_becomes_ac_refunds_and_preserves_task(daily):
    before = generate(daily)
    task = before['plan']['tasks'][0]
    choose = daily.ai.complete.side_effect
    async def race(*args):
        result = await choose(*args)
        ac(daily, task['problem_id'])
        return result
    daily.ai.complete.side_effect = race
    with pytest.raises(AIError, match='换题期间通过'):
        asyncio.run(daily.roll(Roll(provider='api', model='mock', task_id=task['task_id'])))
    after = daily.state()
    assert after['rerolls_remaining'] == 2
    assert after['plan']['tasks'][0]['id'] == task['id'] and after['plan']['tasks'][0]['completed']


def test_workbench_official_judge_ac_excluded_from_candidates(daily):
    now = int(time.time())
    with daily.db.connect() as db:
        db.execute('''INSERT INTO wb_attempts(id,problem_id,language,code,code_hash,created_at,handle,submission_id,verdict,judge_data,last_synced)
            VALUES('verified','codeforces:4-A','cpp17','','',?,'demo',1,'AC',?,?)''',
            (now, json.dumps({'verdict': 'OK', 'creationTimeSeconds': now}), now))
    assert 'codeforces:4-A' not in {row['id'] for row in daily._candidate_pool(2, [])}
    assert daily.complete(Complete(problem_id='codeforces:4-A'))['checkin']['kind'] == 'normal'


def test_actual_time_requires_verified_task_and_is_correctable(daily):
    task = generate(daily)['plan']['tasks'][0]
    payload = TaskTime(task_id=task['task_id'], duration_minutes=45)
    with pytest.raises(HTTPException, match='官方 AC'):
        daily.save_time(payload)
    assert daily.evidence() == []
    assert daily.state()['checkin']['kind'] is None
    ac(daily, task['problem_id'])
    daily.save_time(payload)
    row = daily.evidence()[0]
    assert row['duration_minutes'] == 45 and row['source'] == 'self_reported' and row['verified_ac']
    result = daily.save_time(TaskTime(task_id=task['task_id'], duration_minutes=30))
    assert result['plan']['tasks'][0]['duration_minutes'] == 30
    assert result['checkin']['kind'] == 'normal'
    with daily.db.connect() as conn:
        conn.execute("UPDATE submissions SET verdict='WA'")
    assert daily.evidence() == []
    with pytest.raises(HTTPException):
        daily.save_time(payload)


def test_time_validation_isolated_and_legacy_budget_ignored(daily, tmp_path):
    task = generate(daily)['plan']['tasks'][0]
    ac(daily, task['problem_id'])
    other = Daily(Database(tmp_path / 'other.sqlite3'), daily.ai)
    with pytest.raises(HTTPException, match='找不到'):
        other.save_time(TaskTime(task_id=task['task_id'], duration_minutes=20))
    for bad in (0, 1441, -1, True, '30', 2.5):
        with pytest.raises(Exception):
            TaskTime(task_id=task['task_id'], duration_minutes=bad)
    daily.db.set('daily_settings', {'daily_goal': 3, 'duration_days': 9, 'minutes_per_day': 60})
    assert daily.settings() == {'daily_goal': 3, 'duration_days': 9, 'selection_type': 'random', 'topic_id': None, 'training_mode': 'learn'}
    assert Settings(minutes_per_day=0).model_dump() == {'daily_goal': 1, 'duration_days': 30, 'selection_type': 'random', 'topic_id': None, 'training_mode': 'learn'}


def test_prior_or_future_ac_cannot_support_task_time(daily):
    task = generate(daily)['plan']['tasks'][0]
    ac(daily, task['problem_id'], ts=int(time.time())-86400)
    ac(daily, task['problem_id'], ts=int(time.time())+86400, sid='future')
    with pytest.raises(HTTPException, match='官方 AC'):
        daily.save_time(TaskTime(task_id=task['task_id'], duration_minutes=20))


def test_other_platform_task_completion_and_duration(daily):
    generate(daily)
    day = daily.today().isoformat()
    with daily.db.connect() as conn:
        tasks = conn.execute('SELECT id FROM daily_plan_tasks WHERE day=? ORDER BY ordinal', (day,)).fetchall()
        for row, platform, pid in zip(tasks, ('atcoder', 'luogu'), ('abc300_a', 'P1000')):
            data = {'id': f'{platform}:{pid}', 'problem_id': pid, 'platform': platform, 'title': pid}
            conn.execute('UPDATE daily_plan_tasks SET problem_id=?,data=? WHERE id=?',
                         (data['id'], json.dumps(data), row['id']))
    ac(daily, 'abc300_a', platform='atcoder')
    ac(daily, 'P1000', platform='luogu')
    state = daily.state()
    assert state['checkin']['kind'] == 'k'
    assert all(row['completed'] for row in state['plan']['tasks'])
    with pytest.raises(HTTPException, match='已完成'):
        daily.request_roll(tasks[0]['id'], 'api', 'mock')
    state = daily.save_time(TaskTime(task_id=tasks[0]['id'], duration_minutes=15))
    assert state['time_evidence'][0]['problem_id'] == 'atcoder:abc300_a'


def test_daily_ai_receives_actual_time_not_retired_budget(daily):
    state = generate(daily)
    task = state['plan']['tasks'][0]
    ac(daily, task['problem_id'])
    daily.save_time(TaskTime(task_id=task['task_id'], duration_minutes=35))
    asyncio.run(daily.roll(Roll(provider='api', model='mock', task_id=state['plan']['tasks'][1]['task_id'])))
    payload = daily.ai.complete.call_args.args[3]
    assert 'minutes_per_day' not in payload['daily_budget']
    assert payload['actual_time_evidence'][0]['duration_minutes'] == 35
    assert payload['actual_time_evidence'][0]['source'] == 'self_reported'


def specialist(daily):
    async def select_items(topic_id, mode, count, provider, model, **options):
        excluded = set(options['excluded_ids'])
        rows = [dict(position=i + 1, problem_id=f'luogu:P{i}', platform='luogu', title=f'模板{i}',
                     url=f'https://www.luogu.com.cn/problem/P{i}', difficulty=1000+i,
                     stage='模板' if i == 1 else '巩固', reason='专题证据')
                for i in range(1, 100) if f'luogu:P{i}' not in excluded][:count]
        return rows
    service = SimpleNamespace(select_items=AsyncMock(side_effect=select_items))
    daily.specialist_training = service
    return service


def test_daily_selection_settings_modes_and_legacy_defaults(daily):
    daily.db.set('daily_settings', {'daily_goal': 4, 'duration_days': 7})
    assert daily.settings() == {'daily_goal': 4, 'duration_days': 7,
                               'selection_type': 'random', 'topic_id': None, 'training_mode': 'learn'}
    assert Settings(selection_type='random', topic_id='does-not-exist', training_mode='strengthen').topic_id is None
    for values in ({'daily_goal': 51}, {'selection_type': 'specialist'},
                   {'selection_type': 'specialist', 'topic_id': 'invalid'}, {'training_mode': 'invalid'}):
        with pytest.raises(ValueError):
            Settings(**values)
    assert Settings(daily_goal=50).daily_goal == 50


def test_daily_specialist_snapshot_survives_settings_changes_and_roll(daily):
    service = specialist(daily)
    daily.configure(Settings(daily_goal=3, selection_type='specialist', topic_id='binary-search', training_mode='learn'))
    state = generate(daily)
    assert len(state['plan']['tasks']) == 3
    assert state['plan']['training_options'] == state['locked_options'] == {
        'selection_type': 'specialist', 'topic_id': 'binary-search', 'training_mode': 'learn'}
    assert state['plan']['tasks'][0]['id'] == 'luogu:P1'
    assert state['plan']['tasks'][0]['problem_id'] == 'P1'
    daily.ai.complete.assert_not_awaited()
    daily.configure(Settings(daily_goal=8, selection_type='random'))
    for ordinal in [1, 0]:
        task = state['plan']['tasks'][ordinal]
        state = asyncio.run(daily.roll(Roll(provider='api', model='mock', task_id=task['task_id'])))
        call = service.select_items.call_args
        assert call.args[:3] == ('binary-search', 'learn', 1)
        assert call.kwargs['template_first'] == (ordinal == 0)
        assert call.kwargs['forbid_templates'] == (ordinal != 0)
        assert task['id'] in call.kwargs['excluded_ids']
        assert len(state['plan']['tasks']) == state['locked_goal'] == 3
    assert state['rerolls_remaining'] == 1
    assert state['settings']['selection_type'] == 'random'


def test_daily_random_plan_does_not_change_to_new_specialist_preferences(daily):
    state = generate(daily)
    service = specialist(daily)
    daily.configure(Settings(selection_type='specialist', topic_id='binary-search', training_mode='strengthen'))
    task_id = state['plan']['tasks'][0]['task_id']
    state = asyncio.run(daily.roll(Roll(provider='api', model='mock', task_id=task_id)))
    service.select_items.assert_not_awaited()
    assert state['locked_options']['selection_type'] == 'random'


def test_daily_old_plan_defaults_random_even_after_new_preferences(daily):
    state = generate(daily)
    with daily.db.connect() as conn:
        conn.execute('DELETE FROM web_settings WHERE key=?', (daily._options_key(state['day']),))
    daily.configure(Settings(selection_type='specialist', topic_id='binary-search'))
    assert daily.state()['locked_options']['selection_type'] == 'random'


def test_daily_allows_twelve_specialist_problems_and_failed_roll_refunds(daily):
    service = specialist(daily)
    daily.configure(Settings(daily_goal=12, selection_type='specialist', topic_id='binary-search', training_mode='variation'))
    state = generate(daily)
    assert len(state['plan']['tasks']) == state['rerolls_remaining'] == 12
    assert service.select_items.call_args.args[:3] == ('binary-search', 'variation', 12)
    service.select_items.side_effect = AIError('没有候选')
    with pytest.raises(AIError):
        asyncio.run(daily.roll(Roll(provider='api', model='mock', task_id=state['plan']['tasks'][0]['task_id'])))
    assert daily.state()['rerolls_remaining'] == 12
    assert daily.state()['plan']['tasks'] == state['plan']['tasks']


def test_daily_random_ai_receives_source_aggregates_not_private_records(daily, monkeypatch):
    import acmweb.daily as module
    monkeypatch.setattr(module, 'overview', lambda db, days: {
        'stats': {'submissions': 7, 'solved': 2}, 'topics': [{'tag': 'dp', 'acceptance': 20}],
        'verdicts': {'WA': 5, 'AC': 2}, 'suggested_cf_difficulty': 1100,
        'recent': [{'source': 'PRIVATE-CODE'}], 'platforms': [{'account': 'PRIVATE-HANDLE'}]})
    generate(daily)
    evidence = daily.ai.complete.call_args.args[3]['recent_learning_evidence']
    assert evidence['stats']['submissions'] == 7 and evidence['topics'][0]['tag'] == 'dp'
    assert 'PRIVATE' not in json.dumps(evidence)


def test_daily_random_supports_twelve_items_in_exact_count_schema(daily):
    with daily.db.connect() as db:
        for i in range(12, 24):
            row = {'id': f'codeforces:{i}-A', 'problem_id': f'{i}-A', 'platform': 'codeforces',
                   'title': f'problem{i}', 'difficulty': 1000, 'tags': [], 'contest_id': i,
                   'url': f'https://codeforces.com/problemset/problem/{i}/A'}
            db.execute('INSERT INTO problem_catalog VALUES(?,?,?)', ('codeforces', row['id'], json.dumps(row)))
    daily.configure(Settings(daily_goal=12))
    state = generate(daily)
    assert len(state['plan']['tasks']) == 12
    assert daily.ai.complete.call_args.args[3]['requested_count'] == 12

import asyncio
import json
import time
from datetime import timedelta
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from acmweb.daily import Complete, Daily, Roll, Settings, TaskTime, TZ
from acmweb.ai import AIResult
from acmweb.database import Database
from acmweb.codex_bridge import AIError


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


def test_exact_daily_plan_and_two_single_problem_rerolls(daily):
    state = generate(daily)
    assert len(state['plan']['tasks']) == 2 and state['rerolls_remaining'] == 2
    seen = {task['id'] for task in state['plan']['tasks']}
    for remaining in (1, 0):
        task = state['plan']['tasks'][0]
        state = asyncio.run(daily.roll(Roll(provider='api', model='mock', task_id=task['task_id'])))
        replacement = state['plan']['tasks'][0]
        assert replacement['id'] not in seen
        seen.add(replacement['id'])
        assert len(state['plan']['tasks']) == 2 and state['rerolls_remaining'] == remaining
    with pytest.raises(HTTPException, match='最多使用 2 次'):
        asyncio.run(daily.roll(Roll(provider='api', model='mock', task_id=state['plan']['tasks'][0]['task_id'])))


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


def test_failed_roll_refunds_quota_and_keeps_original_plan(daily):
    before = generate(daily)
    daily.ai.complete.side_effect = AIError('upstream unavailable')
    with pytest.raises(AIError):
        asyncio.run(daily.roll(Roll(provider='api', model='mock', task_id=before['plan']['tasks'][0]['task_id'])))
    after = daily.state()
    assert after['rerolls_remaining'] == 2 and after['plan']['tasks'] == before['plan']['tasks']
    assert not daily.active_ai


def test_invalid_ai_id_or_count_does_not_create_plan(daily):
    for items in ([{'id': 'codeforces:99999-A'}], [{'id': 'codeforces:1-A'}],
                  [{'id': 'codeforces:1-A'}, {'id': 'codeforces:1-A'}]):
        daily.ai.complete.side_effect = None
        daily.ai.complete.return_value = AIResult({'selected_problems': items}, {})
        with pytest.raises(AIError):
            generate(daily)
        assert daily.state()['plan']['status'] == 'failed'


def test_not_enough_candidates_no_billed_ai_call(daily):
    with daily.db.connect() as db:
        db.execute('DELETE FROM problem_catalog')
    with pytest.raises(AIError, match='无法生成'):
        generate(daily)
    daily.ai.complete.assert_not_called()


def test_concurrent_roll_reservation_atomic_across_service_instances(daily):
    state = generate(daily)
    second = Daily(daily.db, daily.ai)
    token = daily.request_roll(state['plan']['tasks'][0]['task_id'], 'api', 'mock')
    with pytest.raises(HTTPException, match='正在进行'):
        second.request_roll(state['plan']['tasks'][1]['task_id'], 'api', 'mock')
    assert second.state()['rerolls_remaining'] == 1
    with daily.db.connect() as db:
        db.execute("UPDATE daily_roll_reservations SET created_at=? WHERE token=?", (int(time.time())-601, token['token']))
    assert second.state()['rerolls_remaining'] == 2


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
    assert daily.settings() == {'daily_goal': 3, 'duration_days': 9}
    assert Settings(minutes_per_day=0).model_dump() == {'daily_goal': 1, 'duration_days': 30}


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

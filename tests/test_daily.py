import asyncio
import json

import pytest

from acmweb.daily import Complete, Daily, Roll, Settings
from acmweb.ai import AIResult
from acmweb.app import create_app
from acmweb.settings import Settings as AppSettings


def test_daily_goal_roll_quota_checkins_and_month_totals(tmp_path, monkeypatch):
    app = create_app(AppSettings(data_dir=tmp_path, config_path=tmp_path/'accounts.toml', enable_codex=False))
    daily = app.state.daily
    daily.db.set('daily_started_at', daily.today())
    daily.db.set('daily_settings', {'daily_goal': 2, 'duration_days': 30})
    with daily.db.connect() as db:
        db.executemany('INSERT INTO problem_catalog VALUES(?,?,?)', [
            ('codeforces', f'codeforces:{i}-A', json.dumps({'id': f'codeforces:{i}-A', 'problem_id': f'{i}-A', 'platform': 'codeforces', 'title': f'p{i}', 'difficulty': 1000, 'tags': [], 'contest_id': i, 'url': f'https://codeforces.com/problemset/problem/{i}/A'}))
            for i in range(1, 5)
        ])

    selected = iter(['codeforces:1-A', 'codeforces:2-A', 'codeforces:3-A'])
    async def complete(task, provider, model, payload, schema):
        assert task == 'daily_roll'
        pid = next(selected)
        return AIResult({'id': pid, 'reason': '适合今日练习'}, {'model': model})
    monkeypatch.setattr(app.state.ai, 'complete', complete)

    state = asyncio.run(daily.roll(Roll(provider='api', model='mock')))
    assert state['rerolls_remaining'] == 2
    first = state['rolls'][0]['problem_id']
    state = asyncio.run(daily.roll(Roll(provider='api', model='mock')))
    assert state['rerolls_remaining'] == 1 and state['rolls'][1]['problem_id'] != first
    state = asyncio.run(daily.roll(Roll(provider='api', model='mock')))
    assert state['rerolls_remaining'] == 0
    with pytest.raises(Exception, match='两次换题'):
        asyncio.run(daily.roll(Roll(provider='api', model='mock')))

    state = daily.complete(Complete(problem_id=state['rolls'][0]['problem_id']))
    assert state['checkin']['kind'] == 'normal'
    state = daily.complete(Complete(problem_id=state['rolls'][1]['problem_id']))
    assert state['checkin']['kind'] == 'k'
    assert state['checkin']['month_k'] == 1 and state['checkin']['month_total'] == 1
    assert state['checkin']['all_k'] == 1 and state['checkin']['all_total'] == 1
    state = daily.complete(Complete(problem_id='codeforces:4-A'))
    assert state['checkin']['kind'] == 'k' and state['completed'] == 3


def test_daily_settings_validate_and_reset_period_only_when_restarted(tmp_path):
    app = create_app(AppSettings(data_dir=tmp_path, config_path=tmp_path/'accounts.toml', enable_codex=False))
    daily = app.state.daily
    before = daily.configure(Settings(daily_goal=3, duration_days=14))
    start = before['started_at']
    with pytest.raises(Exception):
        Settings(daily_goal=0, duration_days=14)
    assert daily.configure(Settings(daily_goal=1, duration_days=14))['started_at'] == start


def test_any_problem_can_earn_normal_checkin_without_an_active_task(tmp_path):
    app = create_app(AppSettings(data_dir=tmp_path, config_path=tmp_path/'accounts.toml', enable_codex=False))
    daily = app.state.daily
    state = daily.complete(Complete(problem_id='codeforces:4-A'))
    assert state['checkin']['kind'] == 'normal'
    assert state['checkin']['month_normal'] == 1
    assert state['checkin']['month_total'] == 1
    assert state['checkin']['all_normal'] == 1 and state['checkin']['all_total'] == 1

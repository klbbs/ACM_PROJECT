"""Exercise the real daily-to-specialist seam with local, synthetic evidence."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from acmweb.ai import AIResult
from acmweb.daily import Daily, Roll, Settings
from acmweb.database import Database
from acmweb.training import Training


@pytest.fixture
def services(tmp_path):
    db = Database(tmp_path / 'daily.sqlite3')
    accounts = SimpleNamespace(credential_checks=SimpleNamespace(statuses=lambda: {'items': [
        {'platform': 'luogu', 'status': 'authenticated', 'login_verified': True},
    ]}))
    ai = SimpleNamespace(complete=AsyncMock())
    specialist = Training(db, ai, accounts)
    daily = Daily(db, ai, accounts)
    daily.specialist_training = specialist
    records = [('P3367', '【模板】并查集', 2), ('P99990', '【模板】集合管理', 2)]
    records += [(f'P{10000+i}', f'合成应用 {i}', 3) for i in range(1, 15)]
    with db.connect() as conn:
        conn.executemany('INSERT INTO problem_catalog VALUES(?,?,?)', [
            ('luogu', 'luogu:' + pid, json.dumps({'id': 'luogu:' + pid, 'problem_id': pid,
                'platform': 'luogu', 'title': title, 'native_difficulty': difficulty, 'tags': ['47'],
                'url': 'https://www.luogu.com.cn/problem/' + pid})) for pid, title, difficulty in records
        ])
    async def choose(task, provider, model, payload, schema):
        assert task == 'specialist_selection'
        candidates = payload['candidates']
        templates = set(payload['template_candidate_ids'])
        ordered = sorted(candidates, key=lambda p: (p['native_difficulty'], p['id']))
        if payload['template_first_required']:
            ordered = [p for p in ordered if p['id'] in templates][:1] + [p for p in ordered if p['id'] not in templates]
        return AIResult({'selected_problems': [{'id': p['id'], 'reason': '根据合成专题标签练习',
            'learning_level': i + 1} for i, p in enumerate(ordered[:payload['requested_count']])]}, {})
    ai.complete.side_effect = choose
    return daily, specialist, ai


def test_daily_specialist_uses_real_selector_and_keeps_locked_mode_during_roll(services):
    daily, specialist, ai = services
    daily.configure(Settings(daily_goal=3, selection_type='specialist', topic_id='luogu-47', training_mode='learn'))
    state = asyncio.run(daily.roll(Roll(provider='api', model='synthetic')))
    tasks = state['plan']['tasks']
    assert len(tasks) == 3 and tasks[0]['stage'] == 'template'
    assert all(task['id'] == 'luogu:' + task['problem_id'] for task in tasks)
    assert specialist.plans() == {'plans': []}  # Daily generation creates no unrelated standalone plan.
    daily.configure(Settings(daily_goal=12, selection_type='random'))
    replacement = asyncio.run(daily.roll(Roll(provider='api', model='synthetic', task_id=tasks[0]['task_id'])))
    assert len(replacement['plan']['tasks']) == 3
    assert replacement['locked_options']['selection_type'] == 'specialist'
    assert replacement['plan']['tasks'][0]['stage'] == 'template'
    assert replacement['plan']['tasks'][0]['id'] != tasks[0]['id']
    assert replacement['plan']['tasks'][1:] == tasks[1:]
    assert ai.complete.call_args.args[3]['mode'] == 'learn'
    assert ai.complete.call_args.args[3]['template_first_required'] is True
    assert not daily.active_ai and not specialist.active_ai


@pytest.mark.parametrize('mode', ['consolidate', 'variation', 'strengthen'])
def test_daily_non_learning_modes_generate_user_count_without_any_template(services, mode):
    daily, specialist, ai = services
    with daily.db.connect() as conn:
        conn.execute("DELETE FROM problem_catalog WHERE id IN ('luogu:P3367','luogu:P99990')")
    daily.configure(Settings(daily_goal=12, selection_type='specialist', topic_id='luogu-47', training_mode=mode))
    state = asyncio.run(daily.roll(Roll(provider='api', model='synthetic')))
    assert len(state['plan']['tasks']) == 12 and state['rerolls_remaining'] == 12
    assert all(task['stage'] == mode for task in state['plan']['tasks'])
    assert ai.complete.call_args.args[3]['template_first_required'] is False

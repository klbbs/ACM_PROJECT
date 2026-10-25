"""Selection/report transaction regressions, written but not run for this task.

Every report here is an explicit local stub; none represents live OJ/model use.
"""
import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from acmweb.codex_bridge import AIError
from acmweb.daily import Roll
from acmweb.selection_report_links import prepare_selection_reports
from acmweb.training import Generate
from selection_report_fixtures import SyntheticSelectionReports
from test_daily import daily
from test_daily_specialist_integration import services
from test_training import training


def linked(items):
    return [{**item, 'ai_report_id': f'isolated-report-{i}'} for i, item in enumerate(items)]


def test_daily_report_links_persist_and_ai_slot_covers_reading(daily):
    async def read(items, provider, model):
        assert daily.active_ai
        assert provider == 'api' and model == 'mock'
        assert all(item['mode'] == 'random' for item in items)
        return linked(items)
    daily.selection_reports = SimpleNamespace(prepare=AsyncMock(side_effect=read))
    state = asyncio.run(daily.roll(Roll(provider='api', model='mock')))
    assert [task['ai_report_id'] for task in state['plan']['tasks']] == ['isolated-report-0', 'isolated-report-1']
    assert not daily.active_ai
    with daily.db.connect() as db:
        records = db.execute('SELECT data FROM daily_plan_tasks ORDER BY ordinal').fetchall()
    assert [json.loads(record[0])['ai_report_id'] for record in records] == ['isolated-report-0', 'isolated-report-1']


def test_missing_report_service_does_not_persist_a_daily_plan(daily):
    daily.selection_reports = None
    with pytest.raises(AIError, match='阅读报告服务尚未配置'):
        asyncio.run(daily.roll(Roll(provider='api', model='mock')))
    assert daily.state()['plan']['status'] == 'failed'
    assert daily.state()['plan']['tasks'] == []
    assert not daily.active_ai


def test_failed_reading_during_daily_roll_preserves_old_tasks_and_refunds(daily):
    before = asyncio.run(daily.roll(Roll(provider='api', model='mock')))
    task_id = before['plan']['tasks'][0]['task_id']
    daily.selection_reports = SimpleNamespace(prepare=AsyncMock(side_effect=AIError('题面阅读后判定不适合本次训练')))
    with pytest.raises(AIError, match='不适合'):
        asyncio.run(daily.roll(Roll(provider='api', model='mock', task_id=task_id)))
    after = daily.state()
    assert after['plan']['tasks'] == before['plan']['tasks']
    assert after['rerolls_remaining'] == before['rerolls_remaining']
    assert not daily.active_ai


@pytest.mark.parametrize('change', ['date', 'difficulty', 'login', 'ac', 'battle'])
def test_daily_rechecks_all_eligibility_after_reading(daily, change):
    async def read(items, provider, model):
        first = items[0]
        if change == 'date':
            daily.db.set('training', {'published_after': '2099-01-01'})
        elif change == 'difficulty':
            daily.db.set('training', {'minimum': 1500, 'maximum': 1600})
        elif change == 'login':
            daily.accounts.credential_checks.statuses = lambda: {'items': []}
        elif change == 'battle':
            daily.db.set('battle_problem_ids', [first['id']])
        else:
            with daily.db.connect() as db:
                db.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)',
                    ('codeforces', 'fixture', 'official-ac', first['problem_id'], int(time.time()), 'AC', '{}', int(time.time())))
        return linked(items)
    daily.selection_reports = SimpleNamespace(prepare=AsyncMock(side_effect=read))
    with pytest.raises(AIError):
        asyncio.run(daily.roll(Roll(provider='api', model='mock')))
    assert daily.state()['plan']['tasks'] == []
    assert not daily.active_ai


def test_cancellation_while_reading_releases_daily_ai_slot_and_plan_lease(daily):
    async def run():
        entered = asyncio.Event()
        async def read(items, provider, model):
            assert daily.active_ai
            entered.set()
            await asyncio.Event().wait()
        daily.selection_reports = SimpleNamespace(prepare=AsyncMock(side_effect=read))
        task = asyncio.create_task(daily.roll(Roll(provider='api', model='mock')))
        await entered.wait()
        with pytest.raises(HTTPException):
            await daily.roll(Roll(provider='api', model='mock'))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not daily.active_ai
        assert daily.state()['plan']['status'] == 'failed'
    asyncio.run(run())


def test_stale_daily_generator_cannot_overwrite_or_fail_a_new_lease(daily):
    payload = daily.request_generate('api', 'mock')
    newer_token = 'newer-isolated-generation'
    async def read(items, provider, model):
        daily.db.set(daily._generation_key(payload['day']), newer_token)
        return linked(items)
    daily.selection_reports = SimpleNamespace(prepare=AsyncMock(side_effect=read))
    with pytest.raises(AIError, match='每日计划状态已变化'):
        asyncio.run(daily.generate(payload))
    assert daily.db.get(daily._generation_key(payload['day'])) == newer_token
    assert daily.state()['plan']['status'] == 'generating'
    assert daily.state()['plan']['tasks'] == []
    assert daily.db.get(daily._failure_key(payload['day'])) is None


def test_whole_daily_operation_timeout_refunds_roll_and_preserves_existing_plan(daily, monkeypatch):
    before = asyncio.run(daily.roll(Roll(provider='api', model='mock')))
    async def timeout(coroutine, timeout):
        assert timeout == 540
        coroutine.close()
        raise asyncio.TimeoutError()
    monkeypatch.setattr('acmweb.daily.asyncio.wait_for', timeout)
    with pytest.raises(AIError, match='选题与阅读报告超时'):
        asyncio.run(daily.roll(Roll(provider='api', model='mock', task_id=before['plan']['tasks'][0]['task_id'])))
    assert daily.state()['plan']['tasks'] == before['plan']['tasks']
    assert daily.state()['rerolls_remaining'] == before['rerolls_remaining']
    assert not daily.active_ai


def test_specialist_reading_covers_selection_slot_and_persists_link(training):
    async def read(items, provider, model):
        assert training.active_ai and training._selecting
        assert all(item['topic_id'] == 'dsu' and item['mode'] == 'learn' for item in items)
        return linked(items)
    training.selection_reports = SimpleNamespace(prepare=AsyncMock(side_effect=read))
    plan = asyncio.run(training.generate(Generate(topic_id='dsu', count=4, provider='api', model='mock')))
    assert all(item['ai_report_id'].startswith('isolated-report-') for item in plan['items'])
    assert training.get_plan(plan['id'])['items'] == plan['items']
    assert not training.active_ai and not training._selecting


def test_specialist_report_rejection_or_missing_service_never_replaces_old_plan(training):
    request = Generate(topic_id='dsu', count=4, provider='api', model='mock')
    first = asyncio.run(training.generate(request))
    training.selection_reports = None
    with pytest.raises(AIError, match='阅读报告服务尚未配置'):
        asyncio.run(training.generate(request))
    assert training.plans()['plans'] == [first]
    assert not training.active_ai and not training._selecting
    training.selection_reports = SimpleNamespace(prepare=AsyncMock(side_effect=AIError('题目不适合当前训练阶段')))
    with pytest.raises(AIError, match='不适合'):
        asyncio.run(training.generate(request))
    assert training.plans()['plans'] == [first]


def test_specialist_rechecks_ac_evidence_after_reading(training):
    async def read(items, provider, model):
        with training.db.connect() as db:
            db.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)',
                ('luogu', 'fixture', 'official-ac', 'P3367', int(time.time()), 'AC', '{}', int(time.time())))
        return linked(items)
    training.selection_reports = SimpleNamespace(prepare=AsyncMock(side_effect=read))
    with pytest.raises(AIError, match='阅读报告期间已变化'):
        asyncio.run(training.generate(Generate(topic_id='dsu', count=4, provider='api', model='mock')))
    assert training.plans()['plans'] == []
    assert not training.active_ai and not training._selecting


def test_daily_specialist_reads_once_through_shared_selector(services):
    from acmweb.daily import Settings
    daily, specialist, _ = services
    daily.configure(Settings(daily_goal=3, selection_type='specialist', topic_id='luogu-47'))
    unused = SyntheticSelectionReports()
    daily.selection_reports = unused
    state = asyncio.run(daily.roll(Roll(provider='api', model='synthetic')))
    assert len(specialist.selection_reports.calls) == 1
    assert unused.calls == []
    assert all(task['ai_report_id'] for task in state['plan']['tasks'])


@pytest.mark.parametrize('change', ['count', 'problem', 'missing', 'duplicate'])
def test_report_links_cannot_replace_problems_or_reuse_another_problems_report(change):
    rows = [{'id': 'codeforces:1-A', 'platform': 'codeforces', 'problem_id': '1-A'},
            {'id': 'codeforces:2-A', 'platform': 'codeforces', 'problem_id': '2-A'}]
    async def malformed(items, provider, model):
        output = linked(items)
        if change == 'count':
            output.pop()
        elif change == 'problem':
            output[0]['id'] = output[1]['id']
        elif change == 'missing':
            output[0].pop('ai_report_id')
        else:
            output[1]['ai_report_id'] = output[0]['ai_report_id']
        return output
    with pytest.raises(AIError, match='阅读报告'):
        asyncio.run(prepare_selection_reports(SimpleNamespace(prepare=malformed), rows, 'api', 'synthetic'))


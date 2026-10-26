"""Local synthetic transports only; these checks do not call AI or an OJ."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from acmweb.ai import ProviderConfig, ConnectionResult
from acmweb.codex_bridge import AIError
from acmweb.daily import Daily
from acmweb.training import Generate, LOCK_KEY
from test_ai_providers import ai, mock_http
from test_selection_reports import setup, PID, SECOND, statement, report, save, count
from test_training import training

THIRD = 'codeforces:4-B'


def test_report_concurrency_is_two_and_completion_order_does_not_reorder_selection(setup):
    service, db, ai, _, _ = setup
    for pid in (PID, SECOND, THIRD):
        save(db, statement(pid))
    async def scenario():
        entered_two = asyncio.Event()
        release_first = asyncio.Event()
        active, peak, completion = 0, 0, []
        async def complete(task, provider, model, payload, schema):
            nonlocal active, peak
            pid = payload['problem_id']
            active += 1
            peak = max(peak, active)
            try:
                if pid == PID:
                    await release_first.wait()
                elif pid == SECOND:
                    entered_two.set()
                completion.append(pid)
                return SimpleNamespace(data=report(pid), provenance={})
            finally:
                active -= 1
        ai.complete.side_effect = complete
        task = asyncio.create_task(service.prepare([{'id': x} for x in (PID, SECOND, THIRD)], 'api', 'm'))
        await asyncio.wait_for(entered_two.wait(), 2)
        await asyncio.sleep(0)
        release_first.set()
        result = await task
        assert [r['id'] for r in result] == [PID, SECOND, THIRD]
        assert completion[0] == SECOND and peak == 2 and active == 0
        assert count(db) == 3 and not service.active_ai
    asyncio.run(scenario())


@pytest.mark.parametrize('cancel_outer', [False, True])
def test_failure_or_cancellation_drains_siblings_and_never_starts_waiting_paid_call(setup, cancel_outer):
    service, db, ai, _, _ = setup
    for pid in (PID, SECOND, THIRD):
        save(db, statement(pid))
    async def scenario():
        both_entered, trigger = asyncio.Event(), asyncio.Event()
        called, cancelled = [], []
        async def complete(task, provider, model, payload, schema):
            pid = payload['problem_id']
            called.append(pid)
            if len(called) == 2:
                both_entered.set()
            try:
                await trigger.wait()
                if pid == PID:
                    raise AIError('synthetic failure')
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(pid)
                raise
        ai.complete.side_effect = complete
        task = asyncio.create_task(service.prepare([{'id': x} for x in (PID, SECOND, THIRD)], 'api', 'm'))
        await asyncio.wait_for(both_entered.wait(), 2)
        if cancel_outer:
            task.cancel()
        else:
            trigger.set()
        with pytest.raises(asyncio.CancelledError if cancel_outer else AIError):
            await task
        assert set(called) == {PID, SECOND}
        assert SECOND in cancelled
        assert count(db) == 0 and not service.active_ai
        assert service._batch_context.get() is None
    asyncio.run(scenario())


def test_configuration_change_mid_read_cannot_save_any_report(setup, monkeypatch):
    service, db, ai, _, _ = setup
    save(db, statement())
    configuration = ['old-private-key-digest']
    monkeypatch.setattr(service, '_configuration_fingerprint', lambda: configuration[0])
    async def changed(*_args):
        configuration[0] = 'new-private-key-digest'
        return SimpleNamespace(data=report(), provenance={})
    ai.complete.side_effect = changed
    with pytest.raises(AIError, match='配置已变化'):
        asyncio.run(service.prepare([{'id': PID}], 'api', 'm'))
    assert count(db) == 0


def test_request_progress_scope_restores_callback_and_ignores_disconnected_consumer(setup):
    service, db, _, _, _ = setup
    save(db, statement())
    outer, inner = [], []
    def disconnected(value):
        inner.append(value)
        raise RuntimeError('consumer disconnected')
    async def scenario():
        with service.progress_scope(outer.append):
            with service.progress_scope(disconnected):
                await service.prepare([{'id': PID}], 'api', 'm')
            await service.prepare([{'id': PID}], 'api', 'm')
        assert service._progress_callback.get() is None
    asyncio.run(scenario())
    assert [x['actual_count'] for x in inner] == [0, 1]
    assert [x['actual_count'] for x in outer] == [0, 1]
    assert count(db) == 2


@pytest.mark.parametrize('reserved', [False, True])
def test_specialist_reserved_background_slot_is_not_counted_twice(training, reserved):
    calls, progress = [], []
    training.guard = lambda: calls.append('guard')
    asyncio.run(training.generate(Generate(topic_id='dsu', count=2, provider='api', model='mock'),
        budget_reserved=reserved, on_progress=progress.append))
    assert calls == ([] if reserved else ['guard'])
    assert [p['stage'] for p in progress] == ['validating', 'selecting', 'reading', 'validating', 'saving']
    assert training._budget_reserved.get() is False and training._progress_callback.get() is None
    assert not training.active_ai and not training._selecting and training.db.get(LOCK_KEY) is None


def test_specialist_total_timeout_is_540_and_releases_600_second_lease(training, monkeypatch):
    async def timeout(coroutine, timeout):
        assert timeout == 540
        coroutine.close()
        raise asyncio.TimeoutError()
    monkeypatch.setattr('acmweb.training.asyncio.wait_for', timeout)
    with pytest.raises(AIError, match='专项选题与阅读报告超时'):
        asyncio.run(training.generate(Generate(topic_id='dsu', count=2, provider='api', model='mock'), budget_reserved=True))
    assert training.plans() == {'plans': []} and training.db.get(LOCK_KEY) is None
    assert not training.active_ai and not training._selecting and training._budget_reserved.get() is False


@pytest.mark.parametrize('vendor,limit,accepted', [('deepseek', 131072, True), ('deepseek', 131073, False),
    ('custom', 32768, True), ('custom', 32769, False), ('openai', 131072, False)])
def test_provider_budget_only_changes_with_explicit_supported_configuration(vendor, limit, accepted):
    if accepted:
        assert ProviderConfig(vendor=vendor, max_output_tokens=limit).max_output_tokens == limit
    else:
        with pytest.raises(ValidationError):
            ProviderConfig(vendor=vendor, max_output_tokens=limit)
    assert ProviderConfig(vendor='deepseek').max_output_tokens == 8192


@pytest.mark.parametrize('reason,code', [('ai_output_truncated','output_truncated'),('ai_empty_final','invalid_output'),
    ('ai_response_incomplete','invalid_output'),('ai_timeout','provider_unavailable'),('ai_network','provider_unavailable'),
    ('selection_report_configuration_changed','provider_configuration')])
def test_daily_reports_only_application_owned_failure_messages(reason, code):
    failure = AIError('PRIVATE KEY Cookie SOURCE')
    failure.reason = reason
    result = Daily._generation_failure(failure)
    assert result['code'] == code and 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('protocol,body,reason', [
    ('chat', {'choices':[{'finish_reason':'length','message':{'content':'SECRET'}}]}, 'ai_output_truncated'),
    ('responses', {'status':'incomplete','incomplete_details':{'reason':'max_output_tokens'},'output':[]}, 'ai_output_truncated'),
    ('anthropic', {'stop_reason':'max_tokens','content':[{'text':'SECRET'}]}, 'ai_output_truncated'),
    ('chat', {'choices':[{'finish_reason':'stop','message':{'content':None,'reasoning_content':'SECRET'}}]}, 'ai_empty_final'),
    ('chat', {'choices':[{'finish_reason':'stop','message':{'content':'{"ok": "SECRET"}'}}]}, 'ai_invalid_output'),
])
def test_transport_failure_diagnostics_have_safe_usage_and_no_retry(ai, monkeypatch, protocol, body, reason):
    from acmweb.runtime_logs import RuntimeLogs
    ai.event_log = RuntimeLogs(ai.settings.data_dir / 'logs')
    ai.save(ProviderConfig(api_key='PRIVATE-KEY', protocol=protocol, vendor='deepseek'))
    body['usage'] = {'prompt_tokens':12,'completion_tokens':400,'completion_tokens_details':{'reasoning_tokens':300},
                     'upstream_secret':'SECRET'}
    calls = mock_http(monkeypatch, body)
    with pytest.raises(AIError) as raised:
        asyncio.run(ai.complete('connection_test', 'api', 'synthetic', {'payload':'PRIVATE-SOURCE'}, ConnectionResult))
    assert raised.value.reason == reason and 'SECRET' not in str(raised.value)
    assert len(calls) == 1 and calls[0][1]['json'].get('max_tokens', calls[0][1]['json'].get('max_output_tokens')) == 8192
    rows = [json.loads(x) for x in (ai.settings.data_dir/'logs/runtime.jsonl').read_text().splitlines()]
    assert len(rows) == 1 and rows[0]['event'] == 'ai.failed'
    assert rows[0]['details']['reason'] == reason
    assert rows[0]['details']['input_tokens'] == 12 and rows[0]['details']['reasoning_tokens'] == 300
    assert 'SECRET' not in json.dumps(rows) and 'PRIVATE' not in json.dumps(rows)
    assert ai._request_metrics.get() is None and ai._request_key.get() is None


@pytest.mark.parametrize('phase', ['selection', 'reading'])
def test_specialist_config_change_cannot_mix_providers_or_start_extra_reading(training, monkeypatch, phase):
    configuration = ['captured-endpoint-key-and-model']
    monkeypatch.setattr(training, '_configuration_fingerprint', lambda: configuration[0])
    choose = training.ai.complete.side_effect
    reader = training.selection_reports
    read = reader.prepare
    reading_calls = []
    async def changed_selection(*args):
        result = await choose(*args)
        if phase == 'selection':
            configuration[0] = 'changed-provider'
        return result
    async def changed_reading(*args):
        reading_calls.append(True)
        result = await read(*args)
        if phase == 'reading':
            configuration[0] = 'changed-provider'
        return result
    training.ai.complete.side_effect = changed_selection
    reader.prepare = changed_reading
    with pytest.raises(AIError, match='配置已变化'):
        asyncio.run(training.generate(Generate(topic_id='dsu', count=2, provider='api', model='mock')))
    assert reading_calls == ([] if phase == 'selection' else [True])
    assert training.plans() == {'plans': []} and training.db.get(LOCK_KEY) is None
    assert training._selection_configuration.get() is None
    assert not training.active_ai and not training._selecting


def test_real_provider_configuration_fingerprint_tracks_secrets_but_is_not_persisted(ai):
    ai.save(ProviderConfig(protocol='chat', api_key='first-private-key', model='m'))
    before = ai.configuration_fingerprint()
    assert before == ai.configuration_fingerprint() and len(before) == 64
    ai.save(ProviderConfig(protocol='chat', api_key='second-private-key', model='m'))
    assert before != ai.configuration_fingerprint()
    assert before not in ai.secret_file.read_text()


def test_untrusted_usage_values_are_ignored(ai):
    metrics = {}
    token = ai._request_metrics.set(metrics)
    try:
        ai._capture_response_metrics({'usage': {'prompt_tokens': True, 'completion_tokens': -1,
            'completion_tokens_details': {'reasoning_tokens': 10**10}, 'key': 'PRIVATE'}})
        assert metrics == {}
        ai._capture_response_metrics({'usage': {'input_tokens': 0, 'output_tokens': 8,
            'output_tokens_details': {'reasoning_tokens': 3}}})
        assert metrics == {'input_tokens': 0, 'output_tokens': 8, 'reasoning_tokens': 3}
    finally:
        ai._request_metrics.reset(token)

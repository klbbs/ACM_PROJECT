import asyncio
import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from acmweb.selftest import SelfTestInput, SelfTester, normalized_output, register_selftest


def test_input_contract_rejects_arbitrary_execution_controls():
    assert SelfTestInput(code='print(1)', language='python3', stdin='2').expected is None
    with pytest.raises(ValueError):
        SelfTestInput(code='x', language='cpp17', callback_url='https://evil.example')
    with pytest.raises(ValueError):
        SelfTestInput(code='x', language='python3', compile_options='-x')
    with pytest.raises(ValueError):
        SelfTestInput(code='x', language='python3', stdin='字' * 21846)


def test_disabled_configuration_never_runs_submitted_code(monkeypatch):
    monkeypatch.setenv('ACM_SELFTEST_URL', '')
    monkeypatch.setenv('ACM_SELFTEST_KEY', '')
    runner = SelfTester(endpoint='', key='')
    result = asyncio.run(runner.capabilities())
    assert result['enabled'] is False
    assert '不会直接执行代码' in result['message']
    app = FastAPI()
    register_selftest(app)
    with TestClient(app) as client:
        response = client.post('/api/workbench/self-test/run', json={
            'code': 'print(1)', 'language': 'python3', 'consent': True,
        })
        assert response.status_code == 503
        assert '不会在网站服务器运行代码' in response.json()['detail']


def test_user_consent_required_and_language_allowlist():
    runner = SelfTester(endpoint='https://judge.example')
    with pytest.raises(Exception, match='同意'):
        asyncio.run(runner.run(SelfTestInput(code='print(1)', language='python3')))
    with pytest.raises(ValueError):
        SelfTestInput(code='x', language='javascript')


def test_output_comparison_ignores_only_line_trailing_space_and_final_blank_lines():
    assert normalized_output('a  \r\nb\n\n') == normalized_output('a\nb')
    assert normalized_output('a\nb') != normalized_output('a\nc')


def test_runner_payload_is_fixed_and_limits_are_not_user_controlled(monkeypatch):
    runner = SelfTester(endpoint='https://judge.example', key='server-only')
    async def fake_capabilities():
        return {'enabled': True, 'languages': {'python3': {'id': 71, 'name': 'Python 3'}}}
    monkeypatch.setattr(runner, 'capabilities', fake_capabilities)
    captured = {}
    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
    async def request(client, method, path, **kwargs):
        if method == 'POST':
            captured.update(url=path, payload=kwargs['json'])
            return {'token': 'fixed-token'}
        return {'status': {'id':3, 'description':'Accepted'},
                'stdout':base64.b64encode(b'ok\n').decode(),
                'time':'0.01','memory':100}
    monkeypatch.setattr(runner, 'client', FakeClient)
    monkeypatch.setattr(runner, 'request', request)
    async def no_sleep(*_):
        return None
    monkeypatch.setattr('acmweb.selftest.asyncio.sleep', no_sleep)
    async def go():
        return await runner.run(SelfTestInput(code='print(input())', language='python3', stdin='1', expected='ok', consent=True))
    result = asyncio.run(go())
    assert result['matched'] is True
    payload = captured['payload']
    assert payload['language_id'] == 71
    assert payload['cpu_time_limit'] == 2 and payload['wall_time_limit'] == 5
    assert payload['memory_limit'] == 131072 and payload['enable_network'] is False
    assert 'callback_url' not in payload and 'compile_options' not in payload and 'extra_files' not in payload
    assert 'server-only' not in repr(result)


def test_real_async_transport_polls_and_preserves_fixed_endpoint(monkeypatch):
    import httpx
    runner = SelfTester(endpoint='https://judge.example/api', key='test-runner-secret')
    requests = []
    polls = 0

    def handle(request):
        nonlocal polls
        requests.append(request)
        assert request.url.host == 'judge.example'
        assert request.headers['X-Auth-Token'] == 'test-runner-secret'
        if request.url.path.endswith('/languages'):
            return httpx.Response(200, json=[{'id': 71, 'name': 'Python (3.8.1)'}])
        if request.method == 'POST':
            return httpx.Response(201, json={'token': 'task-123'})
        polls += 1
        if polls == 1:
            return httpx.Response(200, json={'status': {'id': 2}})
        return httpx.Response(200, json={'status': {'id': 3, 'description': 'Accepted'},
                                        'stdout': base64.b64encode(b'3\n').decode(), 'memory': 300})

    monkeypatch.setattr(runner, 'client', lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle), headers={'X-Auth-Token': runner.key}))
    result = asyncio.run(runner.run(SelfTestInput(code='print(3)', language='python3', expected='4', consent=True)))
    assert polls == 2 and result['matched'] is False
    import json
    payload = json.loads(next(r.content for r in requests if r.method == 'POST'))
    assert payload['enable_per_process_and_thread_time_limit'] is False
    assert payload['enable_per_process_and_thread_memory_limit'] is False
    assert payload['number_of_runs'] == 1
    assert payload['stack_limit'] == 65536
    assert not any('test-runner-secret' in str(r.url) for r in requests)


@pytest.mark.parametrize('bad_memory', [{}, [], '1', -1, float('inf'), True])
def test_invalid_runner_memory_is_rejected_without_ui_crash(monkeypatch, bad_memory):
    import httpx
    runner = SelfTester(endpoint='https://judge.example')
    runner._languages, runner._languages_at = {'python3': {'id': 71}}, __import__('time').monotonic()
    def handle(request):
        if request.method == 'POST':
            return httpx.Response(201, json={'token': 'task-123'})
        return httpx.Response(200, content=__import__('json').dumps({
            'status': {'id': 3}, 'memory': bad_memory,
        }).encode())
    monkeypatch.setattr(runner, 'client', lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle), headers={'X-Auth-Token': runner.key}))
    with pytest.raises(Exception) as error:
        asyncio.run(runner.run(SelfTestInput(code='print(1)', language='python3', consent=True)))
    assert error.value.status_code == 502


def test_timeout_releases_global_slot_and_redirect_does_not_leak_key(monkeypatch):
    import httpx
    from acmweb import selftest
    runner = SelfTester(endpoint='https://judge.example', key='private-key')
    runner._languages, runner._languages_at = {'python3': {'id': 71}}, __import__('time').monotonic()
    async def slow_request(*args, **kwargs):
        await asyncio.sleep(2)
    monkeypatch.setattr(runner, 'request', slow_request)
    monkeypatch.setattr(selftest, 'RUN_TIMEOUT', 0.01)
    with pytest.raises(Exception) as error:
        asyncio.run(runner.run(SelfTestInput(code='print(1)', language='python3', consent=True)))
    assert error.value.status_code == 504
    assert selftest._slots.acquire(blocking=False)
    selftest._slots.release()
    redirect = httpx.MockTransport(lambda request: httpx.Response(302, headers={'Location': 'https://evil.example'}))
    async def go():
        async with httpx.AsyncClient(transport=redirect, follow_redirects=False) as client:
            await SelfTester.request(runner, client, 'POST', '/submissions')
    with pytest.raises(Exception) as error:
        asyncio.run(go())
    assert error.value.status_code == 502
    assert 'private-key' not in str(error.value)


def test_per_user_and_global_concurrency_are_bounded():
    from acmweb import selftest
    runner = SelfTester(endpoint='https://judge.example')
    assert runner._slot.acquire(blocking=False)
    try:
        with pytest.raises(Exception) as error:
            asyncio.run(runner.run(SelfTestInput(code='x', language='python3', consent=True)))
        assert error.value.status_code == 429
    finally:
        runner._slot.release()
    assert selftest._slots.acquire(blocking=False)
    assert selftest._slots.acquire(blocking=False)
    try:
        with pytest.raises(Exception) as error:
            asyncio.run(runner.run(SelfTestInput(code='x', language='python3', consent=True)))
        assert error.value.status_code == 429
        assert runner._slot.acquire(blocking=False)
        runner._slot.release()
    finally:
        selftest._slots.release()
        selftest._slots.release()


def test_runner_transport_and_decoded_output_have_independent_caps(monkeypatch):
    import httpx
    from acmweb.selftest import _decode, MAX_BYTES
    assert _decode(base64.b64encode(b'x' * (MAX_BYTES + 1)).decode()) == 'x' * MAX_BYTES
    with pytest.raises(ValueError):
        _decode('not-base64!')
    runner = SelfTester(endpoint='https://judge.example')
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b' ' * (4 * MAX_BYTES + 1)))
    async def go():
        async with httpx.AsyncClient(transport=transport) as client:
            return await runner.request(client, 'GET', '/languages')
    with pytest.raises(Exception) as error:
        asyncio.run(go())
    assert error.value.status_code == 502

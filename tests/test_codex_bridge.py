import asyncio
import json
import sys

import pytest

from acmweb.codex_bridge import AIError, CodexBridge, _official_failure


def test_official_rpc_auth_models_stream_and_readonly_policy(tmp_path):
    script=tmp_path/'fake-codex'
    script.write_text('#!'+sys.executable+'\n'+'''
import sys,json
def emit(value): print(json.dumps(value),flush=True)
for line in sys.stdin:
    request=json.loads(line)
    method=request['method']
    if 'id' not in request:continue
    params=request.get('params',{})
    result={}
    if method=='account/read':result={'account':{'type':'chatgpt','planType':'plus'}}
    if method=='model/list':result={'data':[{'id':'test-model','model':'test-model','displayName':'Test Model'}],'nextCursor':None}
    if method=='thread/start':
        assert params['approvalPolicy']=='never' and params['ephemeral'] is True
        result={'thread':{'id':'thread-test'}}
    if method=='turn/start':
        assert params['sandboxPolicy']=={'type':'readOnly','networkAccess':False}
        result={'turn':{'id':'turn-test','status':'inProgress'}}
    emit({'id':request['id'],'result':result})
    if method=='turn/start':
        emit({'method':'item/completed','params':{'threadId':'thread-test','turnId':'turn-test','item':{'type':'agentMessage','text':'{"ok": true}'}}})
        emit({'method':'turn/completed','params':{'threadId':'thread-test','turn':{'id':'turn-test','status':'completed'}}})
''')
    script.chmod(0o700)
    async def run():
        bridge=CodexBridge(str(script))
        try:
            account=await bridge.account()
            assert account['connected'] and account['type']=='chatgpt'
            assert (await bridge.models())[0]['id']=='test-model'
            text=await bridge.generate('Do not use tools','test-model',{'type':'object'})
            assert json.loads(text)=={'ok':True}
        finally:await bridge.close()
    asyncio.run(run())


@pytest.mark.parametrize(('info', 'status', 'reason'), [
    ('usageLimitExceeded', None, 'ai_usage_limit'),
    ('sessionBudgetExceeded', None, 'ai_usage_limit'),
    ('rateLimitExceeded', None, 'ai_rate_limited'),
    ('contextWindowExceeded', None, 'ai_context_limit'),
    ('unauthorized', None, 'ai_auth_required'),
    ('modelUnavailable', None, 'ai_model_unavailable'),
    ('badRequest', None, 'ai_upstream_http'),
    ({'responseStreamConnectionFailed': {'httpStatusCode': 401}}, 401, 'ai_auth_required'),
    ({'responseTooManyFailedAttempts': {'httpStatusCode': 429}}, 429, 'ai_rate_limited'),
    ({'httpConnectionFailed': {'httpStatusCode': 503}}, 503, 'ai_upstream_http'),
    ({'responseStreamDisconnected': {'httpStatusCode': None}}, None, 'ai_network'),
    ({'httpConnectionFailed': {'httpStatusCode': True}}, None, 'ai_network'),
    ({'httpConnectionFailed': {'httpStatusCode': 999}}, None, 'ai_network'),
    ('PRIVATE-UNKNOWN', None, 'ai_task_failed'),
])
def test_official_failure_uses_fixed_reasons_and_safe_status(info, status, reason):
    error = _official_failure({'codexErrorInfo': info, 'message': 'PRIVATE-KEY PRIVATE-SOURCE',
                               'additionalDetails': 'PRIVATE-COOKIE'})
    assert error.reason == reason
    assert getattr(error, 'upstream_status', None) == status
    assert 'PRIVATE' not in str(error)
    assert 'PRIVATE' not in repr(error.__dict__)


@pytest.mark.parametrize('error', [
    {'code': 'invalid_json_schema'},
    {'codexErrorInfo': 'badRequest', 'message': 'HTTP 400 invalid_json_schema PRIVATE-SOURCE'},
    {'message': 'Invalid schema for response_format: PRIVATE-KEY'},
])
def test_schema_rejection_is_not_misreported_as_quota(error):
    failure = _official_failure(error)
    assert failure.reason == 'ai_schema_invalid'
    assert '额度' not in str(failure)
    assert 'PRIVATE' not in str(failure)


class EventBridge(CodexBridge):
    def __init__(self, events):
        super().__init__()
        self.events = events

    async def account(self):
        return {'connected': True, 'type': 'chatgpt'}

    async def call(self, method, params=None, timeout=30):
        if method == 'thread/start':
            return {'thread': {'id': 'thread-test'}}
        if method == 'turn/start':
            for event in self.events:
                await self.streams['thread-test'].put(event)
            return {'turn': {'id': 'current'}}
        raise AssertionError(method)


def _event(method, *, turn='current', **params):
    return method, {'threadId': 'thread-test', 'turnId': turn, **params}


def _completed(status='completed', *, turn='current', error=None):
    return 'turn/completed', {'threadId': 'thread-test', 'turn': {'id': turn, 'status': status, 'error': error}}


def test_retry_and_stale_turn_events_do_not_abort_or_contaminate_result():
    bridge = EventBridge([
        ('item/completed', {'threadId': 'thread-test', 'item': {'type': 'agentMessage', 'text': 'UNBOUND-PRIVATE-SOURCE'}}),
        ('turn/completed', {'threadId': 'thread-test', 'turn': {'status': 'failed'}}),
        _event('error', turn='old', error={'codexErrorInfo': 'usageLimitExceeded'}, willRetry=False),
        _event('item/completed', turn='old', item={'type': 'agentMessage', 'text': 'OLD-PRIVATE-SOURCE'}),
        _completed('failed', turn='old', error={'codexErrorInfo': 'unauthorized'}),
        _event('error', error={'codexErrorInfo': {'httpConnectionFailed': {'httpStatusCode': 503}}}, willRetry=True),
        _event('item/agentMessage/delta', delta='{"ok":'),
        _event('item/completed', item={'type': 'agentMessage', 'text': '{"ok":true}'}),
        _completed(),
    ])
    try:
        assert asyncio.run(bridge.generate('prompt', 'test-model', {})) == '{"ok":true}'
        assert not bridge.streams
    finally:
        bridge.scratch.cleanup()


@pytest.mark.parametrize(('terminal', 'expected'), [
    (_completed('failed', error={'message': 'invalid_json_schema PRIVATE-CODE',
                               'codexErrorInfo': {'httpConnectionFailed': {'httpStatusCode': 400}}}), 'ai_schema_invalid'),
    (_completed('failed'), 'ai_task_failed'),
    (_completed('interrupted'), 'ai_cancelled'),
    (('transport/closed', {}), 'ai_network'),
])
def test_failed_turn_never_returns_partial_source_or_report(terminal, expected):
    bridge = EventBridge([_event('item/agentMessage/delta', delta='PRIVATE-PARTIAL-REPORT'), terminal])
    try:
        with pytest.raises(AIError) as raised:
            asyncio.run(bridge.generate('prompt', 'test-model', {}))
        assert raised.value.reason == expected
        assert 'PRIVATE' not in str(raised.value)
        assert not bridge.streams
    finally:
        bridge.scratch.cleanup()


def test_terminal_notification_can_use_sanitized_error_event():
    bridge = EventBridge([
        _event('error', error={'codexErrorInfo': 'unauthorized', 'message': 'PRIVATE-TOKEN'}, willRetry=False),
        _completed('failed'),
    ])
    try:
        with pytest.raises(AIError) as raised:
            asyncio.run(bridge.generate('prompt', 'test-model', {}))
        assert raised.value.reason == 'ai_auth_required'
        assert 'PRIVATE' not in str(raised.value)
    finally:
        bridge.scratch.cleanup()


def test_model_list_accepts_model_only_entries_without_eager_id_lookup():
    class Models(CodexBridge):
        async def call(self, method, params=None, timeout=30):
            assert method == 'model/list'
            return {'data': [{'model': 'official-model'}, {'id': 'legacy', 'displayName': 'Legacy'},
                             {'model': None}, 'invalid'], 'nextCursor': None}

    bridge = Models()
    try:
        models = asyncio.run(bridge.models())
        assert [(item['id'], item['name']) for item in models] == [('official-model', 'official-model'), ('legacy', 'Legacy')]
    finally:
        bridge.scratch.cleanup()


@pytest.mark.parametrize('code', [-32602, 'PRIVATE-KEY'])
def test_rpc_errors_never_reflect_upstream_text_or_untrusted_string_code(tmp_path, code):
    script = tmp_path / 'failed-rpc-codex'
    script.write_text('#!' + sys.executable + '\n' + '''
import sys,json
for line in sys.stdin:
    request=json.loads(line)
    if 'id' not in request: continue
    if request['method']=='initialize':
        result={'id':request['id'],'result':{}}
    else:
        result={'id':request['id'],'error':{'code':''' + repr(code) + ''',
            'message':'PRIVATE-PASSWORD PRIVATE-SOURCE','additionalDetails':'PRIVATE-KEY'}}
    print(json.dumps(result),flush=True)
''')
    script.chmod(0o700)

    async def run():
        bridge = CodexBridge(str(script))
        try:
            with pytest.raises(AIError) as raised:
                await bridge.account()
            assert raised.value.reason == 'ai_task_failed'
            assert 'PRIVATE' not in str(raised.value)
            assert getattr(raised.value, 'rpc_code', None) == (code if type(code) is int else None)
        finally:
            await bridge.close()
            bridge.scratch.cleanup()

    asyncio.run(run())


def test_headless_home_isolated_and_immediate_login_notification(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-reach-child')
    monkeypatch.setenv('CODEX_ACCESS_TOKEN', 'must-not-reach-child')
    home = tmp_path/'server-auth'
    script = tmp_path/'fake-device-codex'
    script.write_text('#!'+sys.executable+'\n'+'''
import json,os,sys
assert os.environ['CODEX_HOME'].endswith('/server-auth')
assert 'OPENAI_API_KEY' not in os.environ and 'CODEX_ACCESS_TOKEN' not in os.environ
for line in sys.stdin:
    request=json.loads(line)
    if 'id' not in request:continue
    result={}
    if request['method']=='account/login/start':
        assert request['params']['type']=='chatgptDeviceCode'
        result={'type':'chatgptDeviceCode','loginId':'test','userCode':'TEST-CODE','verificationUrl':'https://auth.openai.com/codex/device'}
    print(json.dumps({'id':request['id'],'result':result}),flush=True)
    if request['method']=='account/login/start':
        print(json.dumps({'method':'account/login/completed','params':{'loginId':'test','success':True}}),flush=True)
''')
    script.chmod(0o700)
    async def run():
        bridge = CodexBridge(str(script), home=home, device_only=True)
        try:
            result = await bridge.login()
            assert result['userCode'] == 'TEST-CODE'
            for _ in range(20):
                if bridge.login_result.get('completed'):
                    break
                await asyncio.sleep(.01)
            assert bridge.login_result == {'completed':True,'success':True}
            assert home.stat().st_mode & 0o777 == 0o700
        finally:
            await bridge.close()
    asyncio.run(run())

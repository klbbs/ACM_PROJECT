import asyncio
import json
import sys

from acmweb.codex_bridge import CodexBridge


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
        emit({'method':'item/completed','params':{'threadId':'thread-test','item':{'type':'agentMessage','text':'{"ok": true}'}}})
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

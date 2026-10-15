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

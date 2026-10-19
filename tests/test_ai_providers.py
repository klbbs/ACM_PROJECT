import asyncio
import json

import httpx
import pytest
from acmweb.ai import AIProvider, ProviderConfig
from acmweb.database import Database
from acmweb.settings import Settings
from acmweb.codex_bridge import AIError

@pytest.fixture
def ai(tmp_path):
    settings=Settings(data_dir=tmp_path,allow_env_ai_key=False,enable_codex=False)
    return AIProvider(Database(settings.database),settings,None)

def mock_http(monkeypatch, reply, status=200):
    calls=[]
    class Fake:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):calls.append((url,kwargs));return httpx.Response(status,json=reply)
        async def get(self,url,**kwargs):calls.append((url,kwargs));return httpx.Response(status,json=reply)
    monkeypatch.setattr('acmweb.ai.httpx.AsyncClient',lambda **kwargs:Fake())
    return calls

@pytest.mark.parametrize('vendor,base,protocol',[
 ('openai','https://api.openai.com/v1','responses'),('deepseek','https://api.deepseek.com','chat'),
 ('anthropic','https://api.anthropic.com/v1','anthropic'),('gemini','https://generativelanguage.googleapis.com/v1beta/openai','chat'),
 ('qwen','https://dashscope.aliyuncs.com/compatible-mode/v1','chat'),('siliconflow','https://api.siliconflow.cn/v1','chat'),
 ('moonshot','https://api.moonshot.cn/v1','chat'),('glm','https://open.bigmodel.cn/api/paas/v4','chat'),
])
def test_adapter_envelopes_and_auth(ai,monkeypatch,vendor,base,protocol):
    ai.save(ProviderConfig(vendor=vendor,base_url=base,protocol=protocol,api_key='fixture-key',thinking='disabled' if vendor in {'deepseek','qwen'} else 'auto'))
    if protocol=='responses':reply={'status':'completed','output':[{'content':[{'type':'output_text','text':'{"ok":true}'}]}]}
    elif protocol=='anthropic':reply={'stop_reason':'end_turn','content':[{'type':'thinking','thinking':'PRIVATE'},{'type':'text','text':'{"ok":true}'}]}
    else:reply={'choices':[{'finish_reason':'stop','message':{'reasoning_content':'PRIVATE','content':'{"ok":true}'}}]}
    calls=mock_http(monkeypatch,reply)
    result=asyncio.run(ai._api_generate('model-id','private code',{'type':'object'}))
    assert json.loads(result)=={'ok':True} and 'PRIVATE' not in result
    url,args=calls[0];assert url.startswith(base+'/') and len(calls)==1
    assert args['headers'].get('x-api-key' if protocol=='anthropic' else 'Authorization') in {'fixture-key','Bearer fixture-key'}
    assert 'temperature' not in args['json']
    if vendor=='deepseek':assert args['json']['thinking']=={'type':'disabled'}
    if vendor=='qwen':assert args['json']['enable_thinking'] is False
    assert not any(k in args['json'] for k in ('tools','web_search'))

def test_legacy_key_migration_and_endpoint_isolation(ai):
    ai.secret_file.write_text(json.dumps({'api_key':'old-private'}))
    assert ai.key()=='old-private'
    ai.save(ProviderConfig(base_url='https://api.deepseek.com',vendor='deepseek',protocol='chat'))
    assert ai.key()=='' and not ai.public_config()['has_key']
    ai.save(ProviderConfig(base_url='https://api.deepseek.com',vendor='deepseek',protocol='chat',api_key='deepseek-private'))
    captured=ai.config()
    ai.save(ProviderConfig())
    assert ai.key()=='old-private' and ai.headers(captured)['Authorization']=='Bearer deepseek-private'
    ai.save(ProviderConfig(base_url='https://api.deepseek.com',vendor='deepseek',protocol='chat',api_key=''))
    assert ai.key()==''
    assert 'private' not in json.dumps(ai.public_config())

@pytest.mark.parametrize('reply,status,match',[
 ({'error':'SECRET'},401,'401'),({'error':'SECRET'},429,'429'),
 ({'choices':[{'finish_reason':'length','message':{'content':'{"ok":true}'}}]},200,'截断'),
 ({'choices':[{'finish_reason':'stop','message':{'content':None,'reasoning_content':'SECRET'}}]},200,'最终文本'),
 ({'choices':[{'finish_reason':'stop','message':{'content':'not json'}}]},200,'JSON'),
])
def test_failures_never_retry_or_reveal_body(ai,monkeypatch,reply,status,match):
    ai.save(ProviderConfig(api_key='fixture-key',protocol='chat'))
    calls=mock_http(monkeypatch,reply,status)
    with pytest.raises(AIError,match=match) as error:asyncio.run(ai._api_generate('m','prompt',{}))
    assert 'SECRET' not in str(error.value) and len(calls)==1

def test_connection_test_and_model_fallback(ai,monkeypatch):
    ai.save(ProviderConfig(api_key='key',protocol='chat',model='manual-id'))
    calls=mock_http(monkeypatch,{'choices':[{'finish_reason':'stop','message':{'content':'```json\n{"ok":true}\n```'}}]})
    result=asyncio.run(ai.test_connection())
    assert result['ok'] and 'model' in result and 'user data' in calls[0][1]['json']['messages'][1]['content']
    mock_http(monkeypatch,{},404)
    models=asyncio.run(ai.models('api'))
    assert models['source']=='manual' and models['items'][0]['id']=='manual-id'

def test_presets_do_not_override_explicit_admin_allowlist(ai):
    ai.settings.isolated=True;ai.settings.ai_allowed_hosts=('api.openai.com',)
    presets=ai.presets()['items']
    assert next(p for p in presets if p['id']=='openai')['allowed']
    assert not next(p for p in presets if p['id']=='deepseek')['allowed']
    with pytest.raises(AIError):ai.save(ProviderConfig(base_url='https://api.deepseek.com'))


def test_native_claude_models_and_manual_model_preserved(ai,monkeypatch):
    ai.save(ProviderConfig(vendor='anthropic',protocol='anthropic',base_url='https://api.anthropic.com/v1',model='manual-id',api_key='fixture-key'))
    calls=mock_http(monkeypatch,{'data':[{'id':'remote-id','display_name':'Remote model'}]})
    result=asyncio.run(ai.models('api'))
    assert calls[0][0]=='https://api.anthropic.com/v1/models'
    assert calls[0][1]['headers']=={'x-api-key':'fixture-key','anthropic-version':'2023-06-01'}
    assert result['default_model']=='manual-id' and [m['id'] for m in result['items']]==['remote-id','manual-id']


def test_explicit_parameters_and_prompt_only_output(ai,monkeypatch):
    ai.save(ProviderConfig(vendor='deepseek',base_url='https://api.deepseek.com',protocol='chat',api_key='fixture-key',
                           output_mode='prompt',reasoning_effort='high',thinking='enabled',temperature=0.7,max_output_tokens=4096))
    calls=mock_http(monkeypatch,{'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}]})
    asyncio.run(ai._api_generate('manual-id','prompt',{'type':'object'}))
    payload=calls[0][1]['json']
    assert 'response_format' not in payload and payload['max_tokens']==4096
    assert payload['reasoning_effort']=='high' and payload['thinking']=={'type':'enabled'} and payload['temperature']==0.7
    with pytest.raises(AIError,match='思考开关'):
        ai.save(ProviderConfig(protocol='anthropic',thinking='enabled'))


def test_bad_upstream_shapes_are_safe(ai,monkeypatch):
    ai.save(ProviderConfig(api_key='fixture-key',protocol='chat'))
    mock_http(monkeypatch,{'data':['SECRET']})
    with pytest.raises(AIError,match='模型列表'):asyncio.run(ai.models('api'))
    mock_http(monkeypatch,['SECRET'])
    with pytest.raises(AIError,match='JSON'):asyncio.run(ai._api_generate('m','prompt',{}))

"""Responses transport integration: synthetic streams, no paid AI or real OJ."""
import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest
from pydantic import BaseModel,ConfigDict

from acmweb.ai import AIProvider,ProviderConfig
from acmweb.codex_bridge import AIError
from acmweb.database import Database
from acmweb.settings import Settings
from acmweb.runtime_logs import safe_details


class Answer(BaseModel):
    model_config=ConfigDict(extra='forbid')
    ok:bool


def body(value='{"ok":true}'):
    return {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':value}]}],
            'usage':{'input_tokens':10,'output_tokens':20}}


def event(kind,**value):
    return ('event: '+kind+'\ndata: '+json.dumps({'type':kind,**value})+'\n\n').encode()


@pytest.fixture
def provider(tmp_path):
    settings=Settings(data_dir=tmp_path,enable_codex=False,allow_env_ai_key=False)
    ai=AIProvider(Database(settings.database),settings,None)
    ai.save(ProviderConfig(vendor='openai',protocol='responses',model='synthetic',api_key='synthetic-key'))
    return ai


def transport(monkeypatch,handler):
    real=httpx.AsyncClient
    monkeypatch.setattr('acmweb.ai.httpx.AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))


@pytest.mark.parametrize('task',['hermes_selection','hermes_solution_report'])
def test_actual_completion_path_streams_hermes_and_keeps_schema_usage_and_one_request(provider,monkeypatch,task):
    calls=[];logs=[]
    def handler(request):
        calls.append(request)
        payload=json.loads(request.content)
        assert payload['stream'] is True and payload['store'] is False
        assert payload['model']=='synthetic' and payload['max_output_tokens']==8192
        assert payload['text']['format']['type']=='json_schema'
        wire=event('response.created',response={'status':'in_progress'})
        wire+=event('response.reasoning_text.delta',delta='PRIVATE-REASONING')
        wire+=event('response.output_text.delta',delta='UNFINISHED-DATA')
        wire+=event('response.completed',response=body())
        return httpx.Response(200,content=wire,headers={'content-type':'text/event-stream'})
    transport(monkeypatch,handler)
    monkeypatch.setattr('acmweb.ai.emit_event',lambda _store,event,*args,**kw:logs.append((event,kw)))
    result=asyncio.run(provider.complete(task,'api','synthetic',{'private':'not logged'},Answer))
    assert result.data=={'ok':True} and len(calls)==1
    metrics=logs[-1][1]
    assert metrics['ai_streaming'] and metrics['finish_reason']=='stop' and metrics['output_tokens']==20
    assert 'PRIVATE' not in json.dumps(logs) and 'synthetic-key' not in json.dumps(logs)


def test_other_ai_tasks_still_use_existing_non_stream_protocol(provider,monkeypatch):
    calls=[]
    def handler(request):
        calls.append(request)
        assert 'stream' not in json.loads(request.content)
        return httpx.Response(200,json=body())
    transport(monkeypatch,handler)
    assert asyncio.run(provider.complete('connection_test','api','synthetic',{},Answer)).data=={'ok':True}
    assert len(calls)==1


def test_relay_json_fallback_is_not_a_second_request(provider,monkeypatch):
    calls=[]
    def handler(request):
        calls.append(request)
        assert json.loads(request.content)['stream']
        return httpx.Response(200,json=body())
    transport(monkeypatch,handler)
    assert asyncio.run(provider.complete('hermes_solution_report','api','synthetic',{},Answer)).data=={'ok':True}
    assert len(calls)==1


@pytest.mark.parametrize('wire,reason',[
    (event('response.output_text.delta',delta='{"ok":true}'),'ai_stream_interrupted'),
    (event('response.failed',response={'status':'failed','error':{'message':'PRIVATE-KEY'}}),'ai_response_failed'),
    (event('response.incomplete',response={'status':'incomplete','incomplete_details':{'reason':'max_output_tokens'}}),'ai_output_truncated'),
    (event('response.completed',response=body('{"ok":true,"extra":"wrong"}')),'ai_invalid_output')])
def test_stream_failure_never_accepts_partial_or_retries(provider,monkeypatch,wire,reason):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,content=wire,headers={'content-type':'text/event-stream'})
    transport(monkeypatch,handler)
    with pytest.raises(AIError) as error:asyncio.run(provider.complete('hermes_solution_report','api','synthetic',{},Answer))
    assert error.value.reason==reason and 'PRIVATE' not in str(error.value) and len(calls)==1


def test_gateway_524_has_exact_reason_without_reading_upstream_error_or_retry(provider,monkeypatch):
    calls=[];logs=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(524,text='PRIVATE-UPSTREAM-ERROR')
    transport(monkeypatch,handler)
    monkeypatch.setattr('acmweb.ai.emit_event',lambda _store,event,*args,**kw:logs.append(kw))
    with pytest.raises(AIError,match='中转网关等待模型生成超时') as error:
        asyncio.run(provider.complete('hermes_solution_report','api','synthetic',{},Answer))
    assert error.value.reason=='ai_upstream_http' and len(calls)==1
    assert logs[-1]['upstream_status']==524 and logs[-1]['ai_streaming']
    assert 'PRIVATE' not in str(error.value) and 'PRIVATE' not in json.dumps(logs)


def test_cancel_and_total_timeout_close_the_stream_context(provider,monkeypatch):
    monkeypatch.setattr('acmweb.ai.HERMES_STREAM_MAX_SECONDS',.05)
    async def run(cancel):
        entered=asyncio.Event();closed=[]
        class Client:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            @asynccontextmanager
            async def stream(self,*args,**kwargs):
                class Response:
                    status_code=200
                    headers={'content-type':'text/event-stream'}
                    async def aiter_bytes(self):
                        entered.set()
                        yield b': heartbeat\n\n'
                        await asyncio.Event().wait()
                try:yield Response()
                finally:closed.append(True)
        monkeypatch.setattr('acmweb.ai.httpx.AsyncClient',lambda **kw:Client())
        original=provider.config
        monkeypatch.setattr(provider,'config',lambda:{**original(),'timeout':.05})
        task=asyncio.create_task(provider.complete('hermes_solution_report','api','synthetic',{},Answer))
        await entered.wait()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
        else:
            with pytest.raises(AIError) as error:await task
            assert error.value.reason=='ai_timeout'
        assert closed==[True]
    asyncio.run(run(True))
    asyncio.run(run(False))


def test_active_stream_can_outlive_idle_timeout_without_resetting_total_budget(provider,monkeypatch):
    class Content(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield event('response.created',response={'status':'in_progress'})
            for _ in range(5):
                await asyncio.sleep(.02)
                yield b': heartbeat\n\n'
            yield event('response.completed',response=body())
    def handler(request):
        return httpx.Response(200,stream=Content(),headers={'content-type':'text/event-stream'})
    transport(monkeypatch,handler)
    original=provider.config
    monkeypatch.setattr(provider,'config',lambda:{**original(),'timeout':.04})
    monkeypatch.setattr('acmweb.ai.HERMES_STREAM_MAX_SECONDS',.5)
    result=asyncio.run(provider.complete('hermes_solution_report','api','synthetic',{},Answer))
    assert result.data=={'ok':True}


def test_stream_diagnostics_are_allowlisted_and_content_not_retained():
    details=safe_details({'ai_streaming':True,'reason':'ai_stream_interrupted','body':'PRIVATE','api_key':'PRIVATE'})
    assert details=={'ai_streaming':True,'reason':'ai_stream_interrupted'}

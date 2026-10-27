"""Strict schema regression from the real submission-review rejection; no OJ/AI calls."""
import asyncio
import copy
import json

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from acmweb.ai import AIProvider, ProviderConfig
from acmweb.ai_schema import strict_output_schema
from acmweb.codex_bridge import AIError
from acmweb.database import Database
from acmweb.runtime_logs import RuntimeLogs
from acmweb.settings import Settings
from acmweb.workbench import SubmissionReview


RESULT = {'categories':['unable_to_determine'], 'phenomenon':'判题信息不足',
          'evidence':['仅有合成数据'], 'check_directions':['核对官方判题'],
          'minimal_change_idea':None, 'confidence':'low'}


def test_actual_review_optional_nullable_field_is_required_on_wire_without_mutation():
    original = SubmissionReview.model_json_schema()
    before = copy.deepcopy(original)
    strict = strict_output_schema(original)
    assert 'minimal_change_idea' not in original['required']
    assert set(strict['required']) == set(strict['properties'])
    assert {'type':'null'} in strict['properties']['minimal_change_idea']['anyOf']
    assert 'default' not in strict['properties']['minimal_change_idea']
    assert original == before and strict['additionalProperties'] is False
    assert SubmissionReview.model_validate(RESULT).minimal_change_idea is None
    # Local validation keeps its own optional semantics.
    assert SubmissionReview.model_validate({k:v for k,v in RESULT.items() if k!='minimal_change_idea'}).minimal_change_idea is None


def test_nested_defs_array_union_and_property_named_default_keep_contract():
    class Child(BaseModel):
        model_config = ConfigDict(extra='forbid')
        default: str = 'initial'
        value: str | None = None
    class Parent(BaseModel):
        model_config = ConfigDict(extra='forbid')
        children: list[Child]
        other: Child | None = None
    schema = strict_output_schema(Parent.model_json_schema())
    child = schema['$defs']['Child']
    assert child['required'] == ['default','value']
    assert child['properties']['default']['type'] == 'string'
    assert 'default' not in child['properties']['default']
    assert schema['properties']['children']['items']['$ref'] == '#/$defs/Child'
    assert schema['required'] == ['children','other']


def test_dynamic_mapping_is_rejected_instead_of_silently_erasing_data():
    with pytest.raises(ValueError):
        strict_output_schema({'type':'object','additionalProperties':{'type':'integer'}})


@pytest.fixture
def provider(tmp_path):
    settings = Settings(data_dir=tmp_path, enable_codex=True, allow_env_ai_key=False)
    return AIProvider(Database(settings.database),settings,None)


def test_codex_receives_fixed_real_review_schema_and_validates_result(provider):
    seen=[]
    class Bridge:
        async def generate(self, prompt, model, schema):
            seen.append(schema)
            assert set(schema['required']) == set(schema['properties'])
            assert 'minimal_change_idea' in schema['required']
            return json.dumps(RESULT)
    provider.codex=Bridge()
    result=asyncio.run(provider.complete('submission_review','codex','fixture-model',{},SubmissionReview))
    assert result.data == RESULT and len(seen)==1


@pytest.mark.parametrize('protocol', ['responses','chat','anthropic'])
def test_schema_api_adapters_share_required_field_fix(provider,monkeypatch,protocol):
    vendor,base=('anthropic','https://api.anthropic.com/v1') if protocol=='anthropic' else ('openai','https://api.openai.com/v1')
    provider.save(ProviderConfig(vendor=vendor,base_url=base,protocol=protocol,
                               output_mode='schema',api_key='synthetic-key'))
    calls=[]
    class Fake:
        async def __aenter__(self):return self
        async def __aexit__(self,*_):pass
        async def post(self,url,**kwargs):
            calls.append(kwargs['json'])
            if protocol=='responses':body={'status':'completed','output':[{'content':[{'type':'output_text','text':json.dumps(RESULT)}]}]}
            elif protocol=='anthropic':body={'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(RESULT)}]}
            else:body={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(RESULT)}}]}
            return httpx.Response(200,json=body)
    monkeypatch.setattr('acmweb.ai.httpx.AsyncClient',lambda **_:Fake())
    result=asyncio.run(provider.complete('submission_review','api','fixture-model',{},SubmissionReview))
    body=calls[0]
    schema=(body['text']['format']['schema'] if protocol=='responses' else
            body['output_config']['format']['schema'] if protocol=='anthropic' else
            body['response_format']['json_schema']['schema'])
    assert set(schema['required'])==set(schema['properties'])
    assert result.data == RESULT


def test_official_failure_reason_and_status_survive_without_error_body(provider,tmp_path):
    class Bridge:
        async def generate(self,*_):
            e=AIError('synthetic upstream text must not reach logs')
            e.reason='ai_schema_invalid';e.upstream_status=400
            raise e
    provider.codex=Bridge();provider.event_log=RuntimeLogs(tmp_path/'runtime')
    with pytest.raises(AIError):
        asyncio.run(provider.complete('submission_review','codex','fixture-model',{},SubmissionReview))
    log=(tmp_path/'runtime/runtime.jsonl').read_text();data=json.loads(log.splitlines()[-1])
    assert data['details']['reason']=='ai_schema_invalid'
    assert data['details']['upstream_status']==400
    assert 'synthetic upstream text' not in log and 'source_code' not in log

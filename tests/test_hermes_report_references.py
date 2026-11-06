"""Report wiring checks with synthetic OJ/model materials; no real OJ acceptance."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from acmhermes.service import Hermes
from acmweb.app import create_app
from acmweb.codex_bridge import AIError
from acmweb.settings import Settings
from test_hermes_api import PID, STATEMENT, setup_pipeline, wait

REFERENCE = {'problem_id': PID, 'kind':'accepted_public_code', 'title':'Synthetic public source',
    'url':'https://codeforces.com/contest/4/submission/123',
    'excerpt':'PRIVATE_REFERENCE_SNIPPET int helper() { return 1; }',
    'limitations':['公开合成参考，未执行代码']}
PAYLOAD = {'request':{'score_range':[800,1200]}, 'provider':'api', 'model':'synthetic'}


@pytest.fixture
def pipeline(tmp_path,monkeypatch):
    app = create_app(Settings(data_dir=tmp_path, config_path=tmp_path/'missing.toml',
        enable_codex=False, enable_hermes=True))
    return setup_pipeline(app,monkeypatch)


def test_references_overlap_selection_and_only_metadata_enters_result(pipeline, monkeypatch):
    s, fake = pipeline
    original = fake.side_effect
    pool = s.pool
    statistics = {'submission_count':234, 'accepted_count':123}
    monkeypatch.setattr(s, 'pool', lambda *args, **kwargs: [
        {**row,'brief_statistics':statistics} for row in pool(*args,**kwargs)])
    async def exercise():
        references_started, selection_finished = asyncio.Event(), asyncio.Event()
        async def references(pid, statement):
            assert pid == PID and statement == STATEMENT
            references_started.set()
            await selection_finished.wait()
            return [dict(REFERENCE)]
        async def complete(task, provider, model, data, schema):
            if task == 'hermes_selection':
                await asyncio.wait_for(references_started.wait(), .5)
                selection_finished.set()
            else:
                assert data['statement'] == STATEMENT
                assert data['public_references'] == [REFERENCE]
                assert data['brief_statistics'] == statistics
            return await original(task,provider,model,data,schema)
        monkeypatch.setattr(s, 'public_references', references)
        fake.side_effect = complete
        result = await s.run(PAYLOAD)
        assert result['report_references'] == [
            {key:value for key,value in REFERENCE.items() if key != 'excerpt'}]
        assert 'PRIVATE_REFERENCE_SNIPPET' not in json.dumps(result)
        assert fake.await_count == 2
    asyncio.run(exercise())


def test_public_reference_failure_is_optional_and_uses_full_statement(pipeline,monkeypatch):
    s, fake = pipeline
    gather = AsyncMock(side_effect=RuntimeError('synthetic reader failure'))
    monkeypatch.setattr('acmhermes.references.gather', gather)
    monkeypatch.setattr(s, 'public_references', Hermes.public_references.__get__(s))
    result = asyncio.run(s.run(PAYLOAD))
    assert result['problem']['id'] == PID and result['report_references'] == []
    report_input = fake.await_args_list[-1].args[3]
    assert report_input['statement'] == STATEMENT
    assert report_input['public_references'] == []
    gather.assert_awaited_once_with(PID, STATEMENT)


def test_public_reference_wrapper_rejects_mismatched_problem(pipeline,monkeypatch):
    s, _ = pipeline
    monkeypatch.setattr('acmhermes.references.gather', AsyncMock(return_value=[
        {**REFERENCE,'problem_id':'codeforces:4-B'}, REFERENCE]))
    result = asyncio.run(Hermes.public_references(s,PID,STATEMENT))
    assert result == [REFERENCE]


def test_public_reference_wrapper_does_not_swallow_cancellation(pipeline,monkeypatch):
    s, _ = pipeline
    monkeypatch.setattr('acmhermes.references.gather',AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(Hermes.public_references(s,PID,STATEMENT))


@pytest.mark.parametrize('failure',['selection_error','selection_mismatch','cancel'])
def test_selection_failure_or_stop_cleans_up_reference_task(pipeline,monkeypatch,failure):
    s, fake = pipeline
    original = fake.side_effect
    async def exercise():
        started, cleaned = asyncio.Event(), asyncio.Event()
        async def references(pid, statement):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        async def complete(task,provider,model,data,schema):
            assert task == 'hermes_selection'
            await started.wait()
            if failure == 'selection_error':
                raise AIError('synthetic selection failure')
            if failure == 'selection_mismatch':
                response = await original(task,provider,model,data,schema)
                response.data['problem_id'] = 'codeforces:4-B'
                return response
            await asyncio.Event().wait()
        monkeypatch.setattr(s,'public_references',references)
        fake.side_effect = complete
        task = asyncio.create_task(s.run(PAYLOAD))
        await asyncio.wait_for(started.wait(),.5)
        if failure == 'cancel':
            task.cancel()
        with pytest.raises((AIError,asyncio.CancelledError)):
            await task
        assert cleaned.is_set()
        assert not [item for item in asyncio.all_tasks() if item is not asyncio.current_task() and not item.done()]
        assert fake.await_count == 1
    asyncio.run(exercise())


def test_completed_job_and_same_delivery_never_fetch_references_again(pipeline,monkeypatch):
    s, fake = pipeline
    references = AsyncMock(return_value=[REFERENCE])
    monkeypatch.setattr(s,'public_references',references)
    with TestClient(s.app) as client:
        headers = {'Idempotency-Key':'d'*32}
        response = client.post('/api/hermes/draw',json={'score_range':[800,1200]},headers=headers)
        result = wait(client,response.json()['id'])
        assert result['status'] == 'completed', result
        job = result['id']
        references.assert_awaited_once()
        assert client.get('/api/hermes/draws/'+job).status_code == 200
        assert client.post('/api/hermes/draw',json={'score_range':[800,1200]},headers=headers).json()['id'] == job
        s.control.deliveries.start(job)
        first = s.control.deliveries.claim(job)
        assert first['type'] == 'result'
        with s.db.connect() as connection:
            connection.execute('UPDATE hermes_deliveries SET next_attempt_at=0 WHERE job_id=?',(job,))
        repeated = s.control.deliveries.claim(job)
        assert repeated['result_json'] == first['result_json']
        references.assert_awaited_once()
        assert fake.await_count == 2
        assert 'PRIVATE_REFERENCE_SNIPPET' not in first['result_json']


def test_report_policy_has_reference_and_brief_statistics_boundaries():
    from acmweb.prompts import TASK_POLICIES
    policy = TASK_POLICIES['hermes_solution_report']
    for expected in ('完整题面','public_references','不可信','没有参考','brief_statistics','不等于','不执行','来源URL'):
        assert expected in policy


@pytest.mark.parametrize('url', ['https://evil.invalid/contest/4/submission/123',
    'https://codeforces.com/contest/5/submission/123',
    'https://codeforces.com/contest/4/submission/123?target=evil',
    'https://codeforces.com@evil.invalid/contest/4/submission/123'])
def test_public_reference_wrapper_rejects_untrusted_metadata_url(pipeline,monkeypatch,url):
    s, _ = pipeline
    monkeypatch.setattr('acmhermes.references.gather',AsyncMock(return_value=[{**REFERENCE,'url':url}]))
    assert asyncio.run(Hermes.public_references(s,PID,STATEMENT)) == []


def test_public_reference_wrapper_rejects_mismatched_statement_before_read(pipeline,monkeypatch):
    s, _ = pipeline
    gather = AsyncMock(return_value=[REFERENCE])
    monkeypatch.setattr('acmhermes.references.gather',gather)
    assert asyncio.run(Hermes.public_references(s,PID,{**STATEMENT,'id':'codeforces:4-B'})) == []
    gather.assert_not_awaited()

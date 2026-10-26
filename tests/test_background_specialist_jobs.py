"""Local job/API integration checks; no model or OJ network calls."""
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.settings import Settings


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, config_path=tmp_path/'account.toml',
                              enable_codex=False, allow_env_ai_key=False))
    app.state.ai.selection = lambda: {'ready': True, 'channel': 'api', 'model': 'fixture'}
    with TestClient(app) as result:
        yield result


def payload(key='a'*32, **values):
    return {'topic_id': 'dsu', 'count': 4, 'provider': 'api', 'model': 'fixture',
            'request_key': key, **values}


def wait(client, key='a'*32):
    for _ in range(100):
        job = client.get('/api/training/generation', params={'request_key': key}).json()['job']
        if job and job['status'] in {'completed', 'failed', 'interrupted'}:
            return job
        time.sleep(.01)
    raise AssertionError('background task did not finish')


def test_queue_progress_replay_without_guard_or_second_generation(client):
    calls, guards = [], []
    client.app.state.jobs.guard = lambda: guards.append(True)
    async def generate(request, *, budget_reserved, on_progress):
        calls.append((request.count, budget_reserved))
        on_progress({'stage': 'reading', 'actual_count': 2})
        await asyncio.sleep(.02)
        return {'id': 'fixture-plan', 'items': [{'problem_id': str(i)} for i in range(4)]}
    client.app.state.specialist_training.generate = generate
    first = client.post('/api/training/plans/generate', json=payload())
    assert first.status_code == 200
    second = client.post('/api/training/plans/generate', json=payload())
    assert second.json()['id'] == first.json()['id']
    job = wait(client)
    assert job['status'] == 'completed'
    assert job['result']['plan']['id'] == 'fixture-plan'
    assert job['result']['actual_count'] == 4
    assert calls == [(4, True)] and guards == [True]
    assert client.post('/api/training/plans/generate', json=payload(count=5)).status_code == 409
    assert client.get('/api/training/generation', params={'request_key': 'b'*32}).json() == {'job': None}
    assert client.get('/api/training/generation').json()['job']['id'] == job['id']


def test_default_authority_validation_and_failed_job_restore(client):
    assert client.post('/api/training/plans/generate', json=payload(model='other')).status_code == 409
    assert client.post('/api/training/plans/generate', json=payload(topic_id='unknown')).status_code == 422
    assert client.post('/api/training/plans/generate', json=payload(key='bad')).status_code == 422
    assert client.get('/api/training/generation?request_key=bad').status_code == 422
    async def fail(*args, **kwargs):
        raise RuntimeError('secret-upstream-body')
    client.app.state.specialist_training.generate = fail
    assert client.post('/api/training/plans/generate', json=payload()).status_code == 200
    job = wait(client)
    assert job['status'] == 'failed'
    assert 'secret-upstream-body' not in json.dumps(job)
    assert client.get('/api/training/generation').json()['job']['id'] == job['id']
    client.app.state.ai.selection = lambda: {'ready': False}
    assert client.post('/api/training/plans/generate', json=payload(key='c'*32)).status_code == 400


def test_generation_restore_is_database_scoped(client, tmp_path):
    async def generate(*args, **kwargs):
        return {'id': 'private-plan', 'items': [1]}
    client.app.state.specialist_training.generate = generate
    client.post('/api/training/plans/generate', json=payload())
    assert wait(client)['status'] == 'completed'
    root = tmp_path/'another-user'
    with TestClient(create_app(Settings(data_dir=root, config_path=root/'account.toml', enable_codex=False))) as other:
        assert other.get('/api/training/generation').json() == {'job': None}
        assert other.get('/api/training/generation?request_key='+'a'*32).json() == {'job': None}


def test_generation_requires_auth_and_same_origin(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, config_path=tmp_path/'account.toml',
                             enable_codex=False, access_token='local-fixture-secret-long-enough'))
    with TestClient(app) as c:
        assert c.get('/api/training/generation').status_code == 401
        assert c.post('/api/training/plans/generate', json=payload()).status_code == 401
        c.post('/api/auth/login', json={'token': 'local-fixture-secret-long-enough'})
        assert c.post('/api/training/plans/generate', json=payload(),
                      headers={'Origin': 'https://untrusted.invalid'}).status_code == 403


def test_latest_generation_same_second_uses_insertion_order(client):
    with client.app.state.database.connect() as db:
        for job_id in ('f'*32, 'a'*32):
            db.execute('INSERT INTO web_jobs VALUES(?,?,?,?,?,?,?)',
                       (job_id, 'specialist_generate', 'completed', 100, 100, None, None))
    assert client.get('/api/training/generation').json()['job']['id'] == 'a'*32

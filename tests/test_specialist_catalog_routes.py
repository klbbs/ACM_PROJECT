"""Cache inspection and explicit public discovery never invoke a paid model."""
import asyncio
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.database import Database
from acmweb.jobs import Jobs
from acmweb.settings import Settings
from test_multiuser import site


@pytest.mark.parametrize('states,status', [(['updated', 'cached'], 'completed'),
    (['updated', 'failed'], 'partial'), (['empty'], 'partial'), (['partial'], 'partial'),
    (['failed'], 'failed'), (['skipped'], 'failed')])
def test_specialist_catalog_jobs_preserve_partial_results_and_never_call_ai(tmp_path, monkeypatch, states, status):
    import acmweb.catalog as catalog
    db = Database(tmp_path/'db')
    ai = Mock()
    jobs = Jobs(db, SimpleNamespace(), ai, SimpleNamespace())
    def discover(database, accounts, topic, on_progress=None):
        assert topic['id'] == 'dsu'
        rows = [{'platform': platform, 'status': state, 'count': 2 if state in {'updated', 'cached'} else 0,
            'coverage': 'bounded synthetic topic pages'} for platform, state in zip(catalog.PLATFORMS, states)]
        on_progress({'platforms': rows, 'completed_platforms': 0, 'total_platforms': len(rows)})
        return rows
    monkeypatch.setattr(catalog, 'refresh_specialist_candidates', discover)
    async def run():
        queued = jobs.start('specialist_catalog', {'topic_id': 'dsu'})
        await jobs.tasks[queued['id']][1]
        return jobs.list()[0]
    result = asyncio.run(run())
    assert result['status'] == status
    assert result['result']['topic_id'] == 'dsu'
    assert [row['status'] for row in result['result']['platforms']] == states
    assert not ai.mock_calls
    with db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM ai_reports').fetchone()[0] == 0


def test_cancelled_topic_refresh_cannot_be_resurrected_by_late_thread_progress(tmp_path, monkeypatch):
    import acmweb.catalog as catalog
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    def discover(database, accounts, topic, on_progress=None):
        entered.set()
        release.wait(timeout=3)
        on_progress({'platforms': [], 'count': 0})
        finished.set()
        return []
    monkeypatch.setattr(catalog, 'refresh_specialist_candidates', discover)
    jobs = Jobs(Database(tmp_path/'db'), SimpleNamespace(), None)
    async def run():
        queued = jobs.start('specialist_catalog', {'topic_id': 'dsu'})
        while not entered.is_set():
            await asyncio.sleep(.001)
        try:
            await jobs.close()
        finally:
            release.set()
        while not finished.is_set():
            await asyncio.sleep(.001)
        return jobs.list()[0]
    assert asyncio.run(run())['status'] == 'interrupted'


def test_topic_inspection_needs_auth_and_refresh_validates_before_scheduling(tmp_path, monkeypatch):
    import acmweb.catalog as catalog
    settings = Settings(data_dir=tmp_path, config_path=tmp_path/'absent', enable_codex=False,
                        access_token='synthetic-long-enough-test-token')
    app = create_app(settings)
    token = {'Authorization': 'Bearer ' + settings.access_token}
    start = Mock(return_value={'id': 'synthetic-topic-job', 'status': 'queued'})
    monkeypatch.setattr(app.state.jobs, 'start', start)
    with TestClient(app) as client:
        assert client.get('/api/training/candidates?topic_id=dsu').status_code == 401
        assert client.post('/api/training/catalog/refresh', json={'topic_id': 'dsu'}).status_code == 401
        response = client.get('/api/training/candidates?topic_id=dsu', headers=token)
        assert response.status_code == 200
        assert response.json()['eligible_count'] == 0
        assert len(response.json()['platforms']) == 4
        assert client.get('/api/training/candidates?topic_id=missing', headers=token).status_code == 422
        assert client.get('/api/training/candidates?topic_id=dsu&cf_minimum=1900&cf_maximum=1200', headers=token).status_code == 422
        assert client.post('/api/training/catalog/refresh', headers=token, json={'topic_id': 'missing'}).status_code == 422
        assert client.post('/api/training/catalog/refresh', headers=token, json={'topic_id': 'dsu', 'api_key': 'PRIVATE'}).status_code == 422
        assert client.post('/api/training/catalog/refresh', headers=token, json={'topic_id': 'dsu'}).status_code == 409
        assert not start.called
        monkeypatch.setattr(catalog, 'verified_platforms', lambda accounts: {'codeforces'})
        assert client.post('/api/training/catalog/refresh', headers=token, json={'topic_id': 'dsu'}).status_code == 200
        start.assert_called_once_with('specialist_catalog', {'topic_id': 'dsu'})


def test_multiuser_topic_inspection_is_private_and_refresh_keeps_csrf_and_cost_limit(site, monkeypatch):
    import acmweb.catalog as catalog
    request, owner, other, app, *_ = site
    for who in (owner, other):
        assert request(who, 'GET', '/api/training/topics').status_code == 200
    workspace = app.state.workspaces.apps[owner['user']['id']][0]
    assert request(owner, 'PUT', '/api/accounts', {'items': [{'platform': 'codeforces', 'handle': 'fixture'}]}).status_code == 200
    monkeypatch.setattr(catalog, 'verified_platforms', lambda accounts: {'codeforces'} if accounts is workspace.state.account_config else set())
    # Training imports the same pure verified-platform selector at module load.
    monkeypatch.setattr('acmweb.training.verified_platforms', lambda accounts: {'codeforces'} if accounts is workspace.state.account_config else set())
    problem = {'id': 'codeforces:9000-A', 'platform': 'codeforces', 'problem_id': '9000-A',
        'title': 'Private workspace public metadata', 'difficulty': 1200, 'tags': ['dsu'],
        'published_at': 1609459200, 'published_at_source': 'codeforces.contest.startTimeSeconds'}
    with workspace.state.database.connect() as connection:
        connection.execute('INSERT INTO problem_catalog VALUES(?,?,?)', ('codeforces', problem['id'], json.dumps(problem)))
    own = request(owner, 'GET', '/api/training/candidates?topic_id=dsu')
    peer = request(other, 'GET', '/api/training/candidates?topic_id=dsu')
    assert own.status_code == peer.status_code == 200
    assert own.json()['eligible_count'] == 1 and peer.json()['eligible_count'] == 0
    path = '/api/training/catalog/refresh'
    assert request(None, 'POST', path, {'topic_id': 'dsu'}).status_code == 401
    assert request(owner, 'POST', path, {'topic_id': 'dsu'}, extra={'X-CSRF-Token': 'bad'}).status_code == 403
    assert request(owner, 'POST', path, {'topic_id': 'dsu'}, extra={'Origin': 'https://evil.example'}).status_code == 403
    monkeypatch.setattr(workspace.state.jobs, 'start', lambda kind, payload: {'id': 'synthetic-topic-job', 'status': 'queued'})
    for _ in range(6):
        assert request(owner, 'POST', path, {'topic_id': 'dsu'}).status_code == 200
    assert request(owner, 'POST', path, {'topic_id': 'dsu'}).status_code == 429

"""No live OJ requests: test one-click sync independently of any editor code."""
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.settings import Settings
from ojcrawler.models import Collection, Submission


@pytest.fixture
def site(tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path, config_path=tmp_path/'absent.toml',
                              enable_codex=False, multi_user=False))
    with TestClient(app) as client:
        assert client.put('/api/accounts', json={'items':[{'platform':'codeforces', 'handle':'demo',
                           'cookie':'JSESSIONID=synthetic-only', 'enrich':False}]}).status_code == 200
        service = app.state.problem_submissions
        plan = {'result':Collection('codeforces','demo', submissions=[Submission('12345','4-A',int(time.time()),'WA',language='Python 3')], complete=True)}
        monkeypatch.setattr(service, '_collect', lambda *_: (plan['result'], 'synthetic current problem'))
        yield client, app, service, plan


def test_sync_needs_no_editor_code_and_preserves_watermark_profile(site):
    client, app, service, plan = site
    with app.state.database.connect() as db:
        db.execute('INSERT INTO accounts VALUES(?,?,?)', ('codeforces','demo',456))
    result = client.post('/api/problem-submissions/4-A/sync', json={'handle':'demo'})
    assert result.status_code == 200, result.text
    # Wait for the fixture's local background task, not for a real platform.
    for _ in range(100):
        jobs = client.get('/api/jobs').json()['items']
        job = next(j for j in jobs if j['id'] == result.json()['id'])
        if job['status'] not in {'queued','running'}:
            break
        time.sleep(.01)
    assert job['status'] == 'completed', job
    rows = client.get('/api/problem-submissions/codeforces:4-A?handle=demo').json()
    assert rows['items'][0]['submission_id'] == '12345'
    assert rows['items'][0]['attempt_id'] is None
    assert not rows['items'][0]['has_local_code']
    assert rows['sync_status']['status'] == 'completed'
    assert 'code' not in rows['items'][0]
    with app.state.database.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM wb_drafts').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM wb_attempts').fetchone()[0] == 0
        assert db.execute('SELECT watermark FROM accounts').fetchone()[0] == 456
        assert db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0] == 0
    assert client.post('/api/problem-submissions/4-A/sync', json={'handle':'demo'}).status_code == 429


def test_sync_partial_and_empty_keep_previous_results(site):
    client, app, service, plan = site
    import asyncio
    asyncio.run(service.sync('codeforces:4-A', 'demo'))
    plan['result'] = Collection('codeforces','demo', complete=False, error='Synthetic permission failure')
    result = asyncio.run(service.sync('codeforces:4-A', 'demo'))
    assert result['count'] == 0 and result['complete'] is False
    assert 'error' in result
    rows = service.listing('codeforces:4-A','demo')
    assert len(rows['items']) == 1
    assert rows['sync_status']['status'] == 'partial'


def test_sync_rejects_other_accounts_extra_source_and_wrong_problem(site):
    client, app, service, plan = site
    assert client.post('/api/problem-submissions/4-A/sync', json={'handle':'stranger'}).status_code == 400
    assert client.post('/api/problem-submissions/4-A/sync', json={'handle':'demo','code':'do not require code'}).status_code == 422
    assert client.post('/api/problem-submissions/https%3A%2F%2Fevil.test/sync', json={'handle':'demo'}).status_code >= 400
    plan['result'].submissions[0].problem_id = '5-A'
    import asyncio
    with pytest.raises(Exception, match='题号'):
        asyncio.run(service.sync('codeforces:4-A','demo'))
    assert service.listing('codeforces:4-A')['items'] == []


def test_official_source_creates_review_record_without_changing_draft(site, monkeypatch):
    client, app, service, plan = site
    stamp = int(time.time())
    class Driver:
        def source(self, *_):
            return {'id':'12345','account':'demo','problem_id':'4-A','submitted_at':stamp,
                    'verdict':'WA','language':'Python 3','code':'print("official source")',
                    'url':'https://codeforces.com/contest/4/submission/12345'}
        def close(self):
            pass
    monkeypatch.setattr(service.remote,'_driver',lambda *_:Driver())
    response = client.post('/api/problem-submissions/4-A/source', json={'handle':'demo','submission_id':'12345'})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['code_verified'] and value['provenance'] == 'official'
    attempt = app.state.workbench.attempt(value['attempt_id'])
    assert attempt['code'] == 'print("official source")'
    assert attempt['code_verified']
    assert service.listing('4-A')['items'][0]['code_verified']
    with app.state.database.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM wb_drafts').fetchone()[0] == 0
    # Reading a source twice reuses its immutable record; no upstream refetch.
    monkeypatch.setattr(service.remote,'_driver',lambda *_:pytest.fail('duplicate source request'))
    assert client.post('/api/problem-submissions/4-A/source', json={'handle':'demo','submission_id':'12345'}).json()['attempt_id'] == value['attempt_id']


@pytest.mark.parametrize('changed', [ {'account':'stranger'}, {'problem_id':'5-A'}, {'id':'67890'}, {'submitted_at':int(time.time())+3600} ])
def test_official_source_rejects_wrong_evidence(site, monkeypatch, changed):
    client, app, service, plan = site
    class Driver:
        def source(self, *_):
            return {'id':'12345','account':'demo','problem_id':'4-A','submitted_at':int(time.time()),
                    'verdict':'WA','language':'Python 3','code':'print(1)', **changed}
        def close(self):
            pass
    monkeypatch.setattr(service.remote,'_driver',lambda *_:Driver())
    assert client.post('/api/problem-submissions/4-A/source', json={'handle':'demo','submission_id':'12345'}).status_code == 400
    assert service.listing('4-A')['items'] == []


def test_failed_ai_does_not_undo_synced_records(site, monkeypatch):
    client, app, service, plan = site
    review = AsyncMock(side_effect=RuntimeError('SYNTHETIC SECRET'))
    monkeypatch.setattr(app.state.workbench,'automatic_review',review)
    # Existing immutable local code is associated by the server's send path.
    service.remote.record({'problem_id':'codeforces:4-A','handle':'demo','submission_id':'12345',
        'sent_at':int(time.time()),'created_at':int(time.time()),'language':'python3','code':'print(1)',
        'code_hash':'fixture-hash'}, {'id':'12345','account':'demo','problem_id':'4-A','submitted_at':int(time.time()),'verdict':'WA'})
    import asyncio
    result = asyncio.run(service.sync('codeforces:4-A','demo'))
    assert result['count'] == 1
    assert review.await_count == 1
    assert service.listing('4-A')['items'][0]['verdict'] == 'WA'

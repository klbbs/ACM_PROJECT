import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from acmweb.ai import AIResult
from acmweb.app import create_app
from acmweb.settings import Settings
from acmweb.translations import fingerprint
from acmweb.users import csrf_token
from acmweb.workbench import parse_statement


HTML = '''<div class="problem-statement"><div class="header"><div class="title">A. Translate me</div></div>
<div><p>Find the value of <span>$$x + 1$$</span>.</p><p>Use https://example.test/a.</p></div>
<div class="sample-tests"><div class="sample-test"><div class="input"><pre>1</pre></div><div class="output"><pre>2</pre></div></div></div></div>'''


@pytest.fixture
def workspace(tmp_path):
    config = tmp_path / 'account.toml'
    config.write_text('[[accounts]]\nplatform="codeforces"\nhandle="demo"\n')
    app = create_app(Settings(data_dir=tmp_path, config_path=config, enable_codex=False))
    statement = parse_statement(HTML, 'codeforces:4-A')
    statement['source'] = 'official'
    with app.state.workbench.db.connect() as db:
        db.execute('INSERT INTO wb_problems VALUES(?,?,?)', ('codeforces:4-A', json.dumps(statement), int(time.time())))
    with TestClient(app) as client:
        yield app, client, statement


def test_translation_job_upload_review_and_free_reuse(workspace, monkeypatch):
    app, client, statement = workspace
    async def complete(task, provider, model, payload, schema):
        assert task == 'translation'
        assert provider == 'api' and model == 'mock-model'
        return AIResult(
            data={'blocks': [{'id': block['id'], 'text': '译文 ' + block['text']} for block in payload['blocks']]},
            provenance={'channel': 'api', 'provider': 'api', 'endpoint': 'https://api.example.test/v1',
                        'vendor': 'custom', 'protocol': 'chat', 'model': model,
                        'task': task, 'prompt_version': 'test'},
        )

    monkeypatch.setattr(app.state.ai, 'complete', complete)
    queued = client.post('/api/workbench/problem/4-A/translate', json={
        'engine': 'ai', 'provider': 'api', 'model': 'mock-model', 'consent': True,
    })
    assert queued.status_code == 200, queued.text
    job_id = queued.json()['id']
    job = None
    for _ in range(50):
        job = next(item for item in client.get('/api/jobs').json()['items'] if item['id'] == job_id)
        if job['status'] not in {'queued', 'running'}:
            break
        time.sleep(0.01)
    assert job['status'] == 'completed', job
    tid = job['result']['translation_id']
    listing = client.get('/api/workbench/problem/4-A/translations')
    assert listing.status_code == 200
    item = next(item for item in listing.json()['items'] if item['id'] == tid)
    assert item['scope'] == 'private' and item['shareable'] is True
    # The server accepts only a reference to its own generated result.
    upload = client.post(f'/api/translations/{tid}/upload', json={})
    assert upload.status_code == 200, upload.text
    shared_id = upload.json()['id']
    assert client.post(f'/api/translations/{tid}/upload', json={}).status_code == 409
    assert client.get('/api/translations/review').json()['items'][0]['id'] == shared_id
    assert client.post(f'/api/translations/{shared_id}/review', json={'decision': 'approved', 'note': 'ok'}).status_code == 200
    shared = client.get('/api/workbench/problem/4-A/translations').json()['items']
    approved = next(item for item in shared if item['id'] == shared_id)
    assert approved['scope'] == 'shared' and approved['status'] == 'approved'

    # Any later translation request reuses the approved central result without
    # invoking the AI transport again.
    async def should_not_run(*args, **kwargs):
        raise AssertionError('approved shared translation should be free')

    monkeypatch.setattr(app.state.ai, 'complete', should_not_run)
    second = app.state.translations.generate('4-A', type('Payload', (), {
        'engine': 'ai', 'provider': 'api', 'model': 'another-model', 'consent': True,
    })())
    result = asyncio.run(second)
    assert result == {'translation_id': shared_id, 'reused': True}


def test_translation_fingerprint_excludes_fetch_metadata(workspace):
    _, _, statement = workspace
    before = fingerprint(statement)
    changed = {**statement, 'fetched_at': int(time.time()) + 100, 'diagnostics': {'warnings': ['temporary']}}
    assert fingerprint(changed) == before


def test_official_translation_requests_are_batched(monkeypatch, workspace):
    app, _, _ = workspace
    monkeypatch.setenv('ACM_GOOGLE_TRANSLATE_KEY', 'server-only-key')
    calls = []

    class Response:
        status_code = 200

        def __init__(self, count):
            self.count = count

        def json(self):
            return {'data': {'translations': [{'translatedText': f'译文-{i}'} for i in range(self.count)]}}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, *, headers, json):
            calls.append((url, headers, json))
            return Response(len(json['q']))

    monkeypatch.setattr('acmweb.translations.httpx.AsyncClient', lambda **kwargs: Client())
    blocks = [{'id': str(i), 'text': f'paragraph {i}'} for i in range(130)]
    result = asyncio.run(app.state.translations.ordinary('google', blocks))
    assert [len(call[2]['q']) for call in calls] == [128, 2]
    assert result['blocks'][0] == {'id': '0', 'text': '译文-0'}
    assert result['blocks'][-1] == {'id': '129', 'text': '译文-1'}
    assert all(call[1]['X-Goog-Api-Key'] == 'server-only-key' for call in calls)


def test_multiuser_translation_store_is_central_and_statement_stays_private(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, multi_user=True, enable_codex=False,
                        access_token='initialization-secret-long-enough', allowed_hosts=('testserver',))
    app = create_app(settings)
    html_statement = parse_statement(HTML, 'codeforces:4-A')
    html_statement['source'] = 'official'
    with TestClient(app, base_url='https://testserver') as client:
        def request(who, method, path, body=None):
            headers = {'X-ACM-Request': '1', 'Origin': 'https://testserver'}
            if who:
                headers.update({'Cookie': 'acm_user_session=' + who['cookie'], 'X-CSRF-Token': who['csrf_token']})
            return client.request(method, path, headers=headers, **({'json': body} if body is not None else {}))

        setup = client.post('/api/auth/setup', headers={'X-ACM-Request': '1'}, json={
            'username': 'owner', 'password': 'Correct-password-123', 'setup_token': settings.access_token,
        })
        assert setup.status_code == 200, setup.text
        owner_cookie = setup.cookies['acm_user_session']
        owner = {'user': app.state.identities.resolve(owner_cookie), 'csrf_token': csrf_token(owner_cookie), 'cookie': owner_cookie}
        invite = request(owner, 'POST', '/api/admin/invitations', {}).json()['invitation']
        registered = client.post('/api/auth/register', headers={'X-ACM-Request': '1'}, json={
            'username': 'member', 'password': 'Correct-password-123', 'invitation': invite,
        })
        assert registered.status_code == 200
        member_cookie = registered.cookies['acm_user_session']
        member = {'user': app.state.identities.resolve(member_cookie), 'csrf_token': csrf_token(member_cookie), 'cookie': member_cookie}

        assert request(owner, 'GET', '/api/workbench/problem/4-A').status_code == 200
        owner_child = app.state.workspaces.apps[owner['user']['id']][0]
        owner_child.state.workbench.store_statement('codeforces:4-A', html_statement)

        async def complete(task, provider, model, payload, schema):
            return AIResult(
                data={'blocks': [{'id': block['id'], 'text': '译文 ' + block['text']} for block in payload['blocks']]},
                provenance={'channel': 'api', 'provider': 'api', 'endpoint': 'https://api.example.test/v1',
                            'vendor': 'custom', 'protocol': 'chat', 'model': model,
                            'task': task, 'prompt_version': 'test'},
            )

        monkeypatch.setattr(owner_child.state.ai, 'complete', complete)
        queued = request(owner, 'POST', '/api/workbench/problem/4-A/translate', {
            'engine': 'ai', 'provider': 'api', 'model': 'mock-model', 'consent': True,
        })
        assert queued.status_code == 200, queued.text
        job_id = queued.json()['id']
        for _ in range(50):
            job = next(item for item in request(owner, 'GET', '/api/jobs').json()['items'] if item['id'] == job_id)
            if job['status'] not in {'queued', 'running'}:
                break
            time.sleep(0.01)
        assert job['status'] == 'completed', job
        tid = job['result']['translation_id']
        uploaded = request(owner, 'POST', f'/api/translations/{tid}/upload', {})
        assert uploaded.status_code == 200, uploaded.text
        shared_id = uploaded.json()['id']
        assert request(owner, 'POST', f'/api/translations/{shared_id}/review', {'decision': 'approved'}).status_code == 200

        assert request(member, 'GET', '/api/workbench/problem/4-A').status_code == 200
        member_child = app.state.workspaces.apps[member['user']['id']][0]
        member_child.state.workbench.store_statement('codeforces:4-A', html_statement)
        visible = request(member, 'GET', '/api/workbench/problem/4-A/translations')
        assert visible.status_code == 200, visible.text
        assert visible.json()['can_review'] is False
        assert any(item['id'] == shared_id and item['scope'] == 'shared' for item in visible.json()['items'])
        assert member_child.state.translations.shared.path == tmp_path / 'translations.sqlite3'

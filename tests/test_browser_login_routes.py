"""HTTP boundary tests; synthetic manager responses are not real-login proof."""
from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.settings import Settings


class Manager:
    def __init__(self, *args):
        self.calls = []
        self.closed = False

    async def aclose(self): self.closed = True
    def clear(self, owner=None): self.calls.append(('clear', owner))
    async def start(self, owner, platform, expected):
        self.calls.append(('start', owner, platform, expected))
        return {'login_id': 'fixture-flow-id-long-enough', 'status': 'human_required'}
    async def screenshot(self, owner, login_id):
        self.calls.append(('image', owner, login_id))
        return b'\x89PNG\r\n\x1a\n'
    async def interact(self, owner, login_id, kind, **kwargs):
        self.calls.append(('input', owner, login_id, kind, kwargs))
        return {'status': 'human_required'}
    async def status(self, owner, login_id): return {'status': 'human_required'}
    async def confirm(self, owner, login_id): return {'status': 'human_required'}
    async def cancel(self, owner, login_id): return {'status': 'cancelled'}


def make_app(tmp_path, monkeypatch):
    monkeypatch.setattr('acmweb.browser_login_routes.BrowserLogin', Manager)
    return create_app(Settings(data_dir=tmp_path, config_path=tmp_path/'absent.toml',
                               enable_codex=False, access_token='fixture-access-token'))


def test_browser_routes_require_acm_login_and_never_echo_input(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.post('/api/platform-login/browser/start', json={'platform': 'luogu'}).status_code == 401
        client.headers['Authorization'] = 'Bearer fixture-access-token'
        response = client.post('/api/platform-login/browser/start', json={'platform': 'luogu', 'expected_handle': '123'})
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        flow = response.json()['login_id']
        image = client.post('/api/platform-login/browser/image', json={'login_id': flow})
        assert image.json()['image'].startswith('data:image/png;base64,')
        response = client.post('/api/platform-login/browser/input', json={
            'login_id': flow, 'kind': 'text', 'text': 'SYNTHETIC-secret-only'})
        assert response.status_code == 200 and 'SYNTHETIC' not in response.text
        call = app.state.browser_login.calls[-1]
        assert call[1] == 'local-workspace' and call[-1]['text'] == 'SYNTHETIC-secret-only'
        response = client.post('/api/platform-login/browser/input', json={
            'login_id': flow, 'kind': 'text', 'text': 'SYNTHETIC-' * 100})
        assert response.status_code == 422 and 'SYNTHETIC' not in response.text
        assert client.post('/api/platform-login/browser/start', json={'platform': 'evil', 'url': 'http://127.0.0.1'}).status_code == 422
        assert client.post('/api/platform-login/browser/input', json={'login_id': flow, 'kind': 'click', 'x': True, 'y': 30}).status_code == 422
        assert client.post('/api/platform-login/browser/start', json={'platform': 'atcoder'}, headers={'Origin': 'https://other.invalid'}).status_code == 403
    assert app.state.browser_login.closed


def test_multi_child_requires_forwarded_owner(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    app.state.multi_child = True
    with TestClient(app, headers={'Authorization': 'Bearer fixture-access-token'}) as client:
        response = client.post('/api/platform-login/browser/start', json={'platform': 'codeforces'})
        assert response.status_code == 401
        assert app.state.browser_login.calls == []

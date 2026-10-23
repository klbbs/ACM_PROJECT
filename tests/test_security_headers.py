"""Focused development checks; no external service or production data."""
import time
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from acmweb.app import create_app
from acmweb.security_headers import SECURITY_HEADERS
from acmweb.settings import Settings
from acmweb.users import digest


@pytest.fixture(params=[False, True], ids=['single', 'multi'])
def production_app(tmp_path, request):
    settings = Settings(data_dir=tmp_path, config_path=tmp_path/'missing.toml',
        production=True, multi_user=request.param, enable_codex=False,
        allowed_hosts=('testserver',), public_origin='https://testserver',
        access_token='synthetic-token-for-local-security-checks')
    app = create_app(settings)
    return app, settings


def assert_headers(response):
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get_list(name) == [value]
    assert response.headers.get_list('Strict-Transport-Security') == ['max-age=31536000']
    if response.request.url.path.startswith('/api/'):
        assert response.headers['Cache-Control'] == 'no-store'


def test_all_modes_cover_normal_and_early_rejections(production_app):
    app, _ = production_app
    assert isinstance(app, FastAPI)
    assert app.state is not None and callable(app.add_api_route)
    with TestClient(app, base_url='https://testserver') as client:
        for response in [
            client.get('/api/health'),
            client.get('/api/accounts'),
            client.post('/api/auth/login', headers={'Origin': 'https://attacker.invalid'}, json={}),
            client.get('/api/health', headers={'Host': 'attacker.invalid'}),
        ]:
            assert_headers(response)
        assert client.get('/api/accounts').status_code == 401
        assert client.get('/api/health', headers={'Host': 'attacker.invalid'}).status_code == 400


def test_production_unhandled_route_500_is_generic_and_hardened(production_app):
    app, _ = production_app
    async def broken():
        raise RuntimeError('SYNTHETIC-PRIVATE-CONFIG-MUST-NOT-LEAK')
    app.add_api_route('/broken', broken, methods=['GET'])
    # Multi-user frontend forwarding is a catchall; make this disposable test
    # route reachable without modifying application routing in production.
    app.router.routes.insert(0, app.router.routes.pop())
    with TestClient(app, base_url='https://testserver', raise_server_exceptions=False) as client:
        result = client.get('/broken')
        assert result.status_code == 500
        assert result.json() == {'detail': '服务器暂时无法完成请求，请稍后重试'}
        assert 'SYNTHETIC' not in result.text and 'Traceback' not in result.text
        assert_headers(result)


def test_multiuser_unhandled_auth_middleware_500_gets_the_same_headers(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, config_path=tmp_path/'missing.toml', production=True,
        multi_user=True, enable_codex=False, allowed_hosts=('testserver',),
        access_token='synthetic-token-for-local-security-checks')
    app = create_app(settings)
    def fail(*args):
        raise RuntimeError('SYNTHETIC-PRIVATE-RATE-STATE')
    monkeypatch.setattr(app.state.identities, 'limit', fail)
    with TestClient(app, base_url='https://testserver', raise_server_exceptions=False) as client:
        result = client.get('/api/health')
        assert result.status_code == 500 and 'SYNTHETIC' not in result.text
        assert_headers(result)


def test_nested_workspace_response_has_one_policy_copy(tmp_path):
    settings = Settings(data_dir=tmp_path, config_path=tmp_path/'missing.toml', production=True,
        multi_user=True, enable_codex=False, allowed_hosts=('testserver',),
        access_token='synthetic-token-for-local-security-checks')
    app = create_app(settings)
    uid = app.state.identities.create('kendas', 'Synthetic-password-123', setup=True)
    token = app.state.identities.session(uid)
    with TestClient(app, base_url='https://testserver') as client:
        result = client.get('/api/accounts', headers={'Cookie': 'acm_user_session='+token})
        assert result.status_code == 200
        assert_headers(result)


def test_register_ip_budget_cannot_consume_login_or_setup_budgets(tmp_path):
    settings = Settings(data_dir=tmp_path, multi_user=True, enable_codex=False,
        access_token='synthetic-token-for-local-security-checks')
    app = create_app(settings)
    identities = app.state.identities
    identities.create('kendas', 'Synthetic-password-123', setup=True)
    now = int(time.time())
    with identities.connect() as db:
        db.execute('INSERT OR REPLACE INTO rate_limits VALUES(?,?,?,?)',
            ('auth-ip:register:'+digest('testclient'), now//900, 30, now+900))
    with TestClient(app, base_url='https://testserver') as client:
        headers = {'X-ACM-Request': '1'}
        assert client.post('/api/auth/register', headers=headers, json={
            'username': 'member', 'password': 'Synthetic-password-123', 'invitation': 'invalid'}).status_code == 429
        assert client.post('/api/auth/login', headers=headers, json={
            'username': 'kendas', 'password': 'Synthetic-password-123'}).status_code == 200
        assert client.post('/api/auth/setup', headers=headers, json={
            'username': 'kendas', 'password': 'Synthetic-password-123', 'setup_token': 'invalid'}).status_code == 403
    with identities.connect() as db:
        assert db.execute('SELECT count FROM rate_limits WHERE key=?', ('auth-ip:login:'+digest('testclient'),)).fetchone()[0] == 1
        assert db.execute('SELECT count FROM rate_limits WHERE key=?', ('auth-ip:setup:'+digest('testclient'),)).fetchone()[0] == 1


def test_local_mode_has_no_hsts_but_keeps_security_policy(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, config_path=tmp_path/'missing.toml', enable_codex=False))
    with TestClient(app) as client:
        result = client.get('/api/health')
        assert 'Strict-Transport-Security' not in result.headers
        assert result.headers['Permissions-Policy'] == SECURITY_HEADERS['Permissions-Policy']
        assert "form-action 'self'" in result.headers['Content-Security-Policy']


def test_nested_workspace_unhandled_500_remains_generic_and_hardened(tmp_path):
    settings = Settings(data_dir=tmp_path, config_path=tmp_path/'missing.toml', production=True,
        multi_user=True, enable_codex=False, allowed_hosts=('testserver',),
        access_token='synthetic-token-for-local-security-checks')
    app = create_app(settings)
    uid = app.state.identities.create('kendas', 'Synthetic-password-123', setup=True)
    token = app.state.identities.session(uid)
    headers = {'Cookie': 'acm_user_session='+token}
    with TestClient(app, base_url='https://testserver', raise_server_exceptions=False) as client:
        assert client.get('/api/accounts', headers=headers).status_code == 200
        child = app.state.workspaces.apps[uid][0]
        async def broken():
            raise RuntimeError('SYNTHETIC-PRIVATE-WORKSPACE-CONFIG')
        child.add_api_route('/api/focused-failure', broken)
        child.router.routes.insert(0, child.router.routes.pop())
        result = client.get('/api/focused-failure', headers=headers)
        assert result.status_code == 500
        assert result.json() == {'detail': '服务器暂时无法完成请求，请稍后重试'}
        assert 'SYNTHETIC' not in result.text
        assert_headers(result)

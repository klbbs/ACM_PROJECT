import asyncio
import sqlite3
from contextlib import contextmanager
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.ai_host_allowlist import AIHostAllowlist, normalize_host
from acmweb.ai import AIProvider, ProviderConfig
from acmweb.codex_bridge import AIError
from acmweb.database import Database
from acmweb.settings import Settings
from acmweb.users import Users, csrf_token, digest


@pytest.mark.parametrize('value', [
    ('api.example.com', 'api.example.com'),
    (' API.EXAMPLE.COM. ', 'api.example.com'),
    ('模型.example', 'xn--xgs754b.example'),
])
def test_normalize_public_host(value):
    source, expected = value
    assert normalize_host(source) == expected


@pytest.mark.parametrize('value', [
    '', 'localhost', 'service.local', 'metadata.google.internal', '127.0.0.1',
    'https://api.example.com', 'api.example.com:443', '*.example.com', 'api.example.com/path',
])
def test_reject_non_host_or_private_host(value):
    with pytest.raises(ValueError):
        normalize_host(value)


def test_allowlist_persists_changes_and_can_remove_custom_domains(tmp_path):
    identities = Users(tmp_path / 'users.sqlite3')
    settings = Settings(data_dir=tmp_path, ai_allowed_hosts=('api.openai.com', 'api.deepseek.com'))
    hosts = AIHostAllowlist(identities, settings)
    assert hosts.update(['Gateway.Example.com.']) == {
        'default_hosts': ['api.openai.com', 'api.deepseek.com'],
        'custom_hosts': ['gateway.example.com'],
    }
    assert 'gateway.example.com' in settings.ai_allowed_hosts

    reloaded = AIHostAllowlist(Users(identities.path), Settings(
        data_dir=tmp_path, ai_allowed_hosts=('api.openai.com', 'api.deepseek.com')))
    assert reloaded.custom_hosts == ('gateway.example.com',)
    assert 'gateway.example.com' in reloaded.settings.ai_allowed_hosts
    reloaded.update([])
    assert reloaded.settings.ai_allowed_hosts == ('api.openai.com', 'api.deepseek.com')


def test_settings_copies_and_reinitialization_preserve_live_revocable_custom_hosts(tmp_path):
    identities = Users(tmp_path / 'users.sqlite3')
    settings = Settings(data_dir=tmp_path, ai_allowed_hosts=('api.openai.com',))
    hosts = AIHostAllowlist(identities, settings)
    before = replace(settings, isolated=True)
    hosts.update(['gateway.example.com'])
    after = replace(settings, isolated=True)
    assert 'gateway.example.com' in before.ai_allowed_hosts
    reloaded = AIHostAllowlist(identities, settings)
    assert reloaded.public()['default_hosts'] == ['api.openai.com']
    reloaded.update([])
    assert hosts.custom_hosts == ()
    assert all('gateway.example.com' not in config.ai_allowed_hosts for config in (settings, before, after))


@pytest.mark.parametrize('during_commit', [False, True])
def test_failed_database_write_keeps_previous_live_policy(tmp_path, monkeypatch, during_commit):
    identities = Users(tmp_path / 'users.sqlite3')
    settings = Settings(data_dir=tmp_path, ai_allowed_hosts=('api.openai.com',))
    hosts = AIHostAllowlist(identities, settings)
    hosts.update(['gateway.example.com'])
    connect = identities.connect

    @contextmanager
    def failing_connect():
        if not during_commit:
            raise sqlite3.OperationalError('unavailable')
        with connect() as db:
            yield db
            raise sqlite3.OperationalError('commit unavailable')

    monkeypatch.setattr(identities, 'connect', failing_connect)
    with pytest.raises(sqlite3.OperationalError):
        hosts.update([])
    assert hosts.custom_hosts == ('gateway.example.com',)
    assert 'gateway.example.com' in settings.ai_allowed_hosts
    monkeypatch.setattr(identities, 'connect', connect)
    assert AIHostAllowlist(identities, settings).custom_hosts == ('gateway.example.com',)


@pytest.mark.parametrize('operation', ['generate', 'models', 'second_models_page'])
def test_revoked_host_is_checked_before_each_actual_upstream_request(tmp_path, monkeypatch, operation):
    identities = Users(tmp_path / 'users.sqlite3')
    settings = Settings(data_dir=tmp_path, isolated=True, allow_env_ai_key=False,
                        ai_allowed_hosts=('api.openai.com',))
    hosts = AIHostAllowlist(identities, settings)
    hosts.update(['gateway.example.com'])
    ai = AIProvider(Database(settings.database), settings, None)
    ai.save(ProviderConfig(base_url='https://gateway.example.com/v1', vendor='custom',
                           protocol='anthropic', api_key='fixture-key', model='fixture-model'))
    calls = []

    class FakeClient:
        async def __aenter__(self):
            if operation != 'second_models_page':
                hosts.update([])
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, *_args, **_kwargs):
            calls.append('post')
            raise AssertionError('revoked host was contacted')

        async def get(self, *_args, **_kwargs):
            calls.append('get')
            hosts.update([])
            return httpx.Response(200, json={'data': [{'id': 'first'}], 'has_more': True, 'last_id': 'first'})

    monkeypatch.setattr('acmweb.ai.httpx.AsyncClient', lambda **_kwargs: FakeClient())
    with pytest.raises(AIError, match='白名单'):
        asyncio.run(ai._api_generate('fixture-model', 'prompt', {}) if operation == 'generate' else ai.models('api'))
    assert calls == (['get'] if operation == 'second_models_page' else [])


def test_allowlist_update_rechecks_exact_session_under_write_lock(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, multi_user=True, enable_codex=False,
                        access_token='integration-setup-token-long-enough', allowed_hosts=('testserver',))
    app = create_app(settings)
    with TestClient(app, base_url='https://testserver') as client:
        setup = client.post('/api/auth/setup', headers={'X-ACM-Request': '1'}, json={
            'username': 'kendas', 'password': 'Integration-password-123', 'setup_token': settings.access_token,
        })
        cookie = setup.cookies['acm_user_session']
        identities = app.state.identities
        resolve = identities.resolve
        # The last resolve authorizes the route before its targeted rate limit.
        limit = identities.limit

        def revoke_after_initial_authorization(key, maximum, seconds):
            result = limit(key, maximum, seconds)
            if key.startswith('admin-ai-hosts:'):
                with identities.connect() as db:
                    db.execute('DELETE FROM sessions WHERE hash=?', (digest(cookie),))
            return result

        monkeypatch.setattr(identities, 'limit', revoke_after_initial_authorization)
        response = client.put('/api/admin/ai-hosts', json={'hosts': ['gateway.example.com']}, headers={
            'X-ACM-Request': '1', 'X-CSRF-Token': csrf_token(cookie), 'Origin': 'https://testserver',
        })
        assert response.status_code == 403
        assert '管理员身份已变更' in response.text
        assert resolve(cookie) is None
        with identities.connect() as db:
            assert db.execute('SELECT value FROM site_settings WHERE key=?', ('ai.allowed_hosts.custom',)).fetchone() is None
        assert 'gateway.example.com' not in settings.ai_allowed_hosts


def test_admin_can_manage_allowlist_members_cannot_and_change_updates_loaded_workspaces(tmp_path):
    settings = Settings(data_dir=tmp_path, multi_user=True, enable_codex=False,
                        access_token='integration-setup-token-long-enough',
                        allowed_hosts=('testserver',), ai_allowed_hosts=('api.openai.com',))
    app = create_app(settings)
    with TestClient(app, base_url='https://testserver') as client:
        def request(who, method, path, body=None, csrf=True):
            headers = {'X-ACM-Request': '1', 'Origin': 'https://testserver', 'Cookie': ''}
            if who:
                headers.update({'Cookie': 'acm_user_session=' + who['cookie']})
                if csrf:
                    headers['X-CSRF-Token'] = who['csrf']
            return client.request(method, path, headers=headers, **({'json': body} if body is not None else {}))

        setup = request(None, 'POST', '/api/auth/setup', {
            'username': 'kendas', 'password': 'Integration-password-123',
            'setup_token': settings.access_token,
        })
        assert setup.status_code == 200, setup.text
        cookie = setup.cookies['acm_user_session']
        admin = {'cookie': cookie, 'csrf': csrf_token(cookie)}
        invitation = request(admin, 'POST', '/api/admin/invitations', {}).json()['invitation']
        registered = request(None, 'POST', '/api/auth/register', {
            'username': 'member', 'password': 'Integration-password-123', 'invitation': invitation,
        })
        member_cookie = registered.cookies['acm_user_session']
        member = {'cookie': member_cookie, 'csrf': csrf_token(member_cookie)}

        assert request(None, 'GET', '/api/admin/ai-hosts').status_code == 401
        assert request(member, 'GET', '/api/admin/ai-hosts').status_code == 403
        denied = request(member, 'PUT', '/api/admin/ai-hosts', {'hosts': ['gateway.example.com']})
        assert denied.status_code == 403
        assert request(admin, 'PUT', '/api/admin/ai-hosts', {'hosts': ['gateway.example.com']}, csrf=False).status_code == 403

        child_response = request(admin, 'GET', '/api/ai/settings')
        assert child_response.status_code == 200
        child = app.state.workspaces.apps[app.state.identities.resolve(cookie)['id']][0]
        assert 'gateway.example.com' not in child.state.settings.ai_allowed_hosts
        ai_config = {'base_url': 'https://gateway.example.com/v1', 'protocol': 'chat',
                     'model': 'gateway-model', 'vendor': 'custom', 'output_mode': 'auto',
                     'thinking': 'auto', 'reasoning_effort': 'auto', 'max_output_tokens': 8192,
                     'timeout': 60, 'temperature': None}
        blocked = request(admin, 'PUT', '/api/ai/settings', ai_config)
        assert blocked.status_code == 400 and '联系管理员' in blocked.text

        saved = request(admin, 'PUT', '/api/admin/ai-hosts', {'hosts': ['Gateway.Example.com.']})
        assert saved.status_code == 200, saved.text
        assert saved.json()['custom_hosts'] == ['gateway.example.com']
        assert 'gateway.example.com' in child.state.settings.ai_allowed_hosts
        assert request(admin, 'GET', '/api/admin/ai-hosts').json()['default_hosts'] == ['api.openai.com']
        assert request(admin, 'PUT', '/api/ai/settings', ai_config).status_code == 200

        invalid = request(admin, 'PUT', '/api/admin/ai-hosts', {'hosts': ['https://bad.example.com']})
        assert invalid.status_code == 422
        assert request(admin, 'GET', '/api/admin/ai-hosts').json()['custom_hosts'] == ['gateway.example.com']

        removed = request(admin, 'PUT', '/api/admin/ai-hosts', {'hosts': []})
        assert removed.status_code == 200
        assert 'gateway.example.com' not in child.state.settings.ai_allowed_hosts
        blocked_again = request(admin, 'PUT', '/api/ai/settings', ai_config)
        assert blocked_again.status_code == 400
        assert 'gateway.example.com' not in request(admin, 'GET', '/api/admin/ai-hosts').text

        with app.state.identities.connect() as db:
            db.execute("UPDATE users SET role='member' WHERE id=?", (app.state.identities.resolve(cookie)['id'],))
        assert request(admin, 'GET', '/api/admin/ai-hosts').status_code == 403
        assert request(admin, 'PUT', '/api/admin/ai-hosts', {'hosts': ['gateway.example.com']}).status_code == 403

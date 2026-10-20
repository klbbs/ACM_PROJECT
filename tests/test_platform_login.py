"""Synthetic forms exercise trust boundaries; these are not live-login tests."""
import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
import requests
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from acmweb.accounts import AccountInput, AccountList, Accounts
from acmweb.platform_login import (LOGIN_URLS, MAX_BODY, PasswordInput,
                                  PasswordLogin, URLS, register)
from acmweb.settings import Settings

PASSWORD = 'SYNTHETIC-password-never-return'
COOKIE = 'SYNTHETIC-cookie-never-return'
CSRF = 'SYNTHETIC-csrf-never-return'


def login_page(platform):
    field = 'handleOrEmail' if platform == 'codeforces' else 'username'
    return (f'<form method="POST" action="{LOGIN_URLS[platform]}">'
            f'<input name="{field}"><input name="password" type="password">'
            f'<input name="csrf_token" type="hidden" value="{CSRF}"></form>')


def settings_page(platform, handle='tourist'):
    nav = 'class="lang-chooser"' if platform == 'codeforces' else 'id="navbar-collapse"'
    path = '/profile/' if platform == 'codeforces' else '/users/'
    return (f'<div {nav}><a href="{path}{handle}">current</a></div>'
            '<a href="/logout">logout</a><form action="/settings"><input name="email"></form>')


class Response:
    def __init__(self, body='', status=200, location=None):
        self.body = body.encode() if isinstance(body, str) else body
        self.status_code = status
        self.headers = {'Content-Type': 'text/html'}
        if location is not None:
            self.headers['Location'] = location
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, size):
        for offset in range(0, len(self.body), size):
            yield self.body[offset:offset + size]


class Upstream(requests.Session):
    def __init__(self, platform, responses=None, hook=None):
        super().__init__()
        self.platform, self.calls, self.closed, self.hook = platform, [], False, hook
        self.responses = list(responses or [Response(login_page(platform)),
                                           Response(status=302, location='/'),
                                           Response(settings_page(platform))])
        domain = 'codeforces.com' if platform == 'codeforces' else 'atcoder.jp'
        self.cookies.set('session', COOKIE, domain=domain, path='/')
    def close(self):
        self.closed = True
        super().close()
    def request(self, method, url, **kwargs):
        assert kwargs['allow_redirects'] is False and kwargs['stream'] is True
        assert 0 < kwargs['timeout'][0] <= 5 and 0 < kwargs['timeout'][1] <= 12
        assert (method, url) == [('GET', LOGIN_URLS[self.platform]),
                                 ('POST', LOGIN_URLS[self.platform]),
                                 ('GET', URLS[self.platform])][len(self.calls)]
        if method == 'POST':
            data = kwargs['data']
            assert data['password'] == PASSWORD and data['csrf_token'] == CSRF
            assert data['handleOrEmail' if self.platform == 'codeforces' else 'username'] == 'tourist'
        self.calls.append((method, url))
        if self.hook:
            self.hook(self, len(self.calls))
        value = self.responses.pop(0)
        if isinstance(value, Exception): raise value
        return value


def setup_manager(tmp_path, platform='atcoder', **kwargs):
    accounts = Accounts(Settings(data_dir=tmp_path, config_path=tmp_path/'missing.toml'))
    session = Upstream(platform, **kwargs)
    manager = PasswordLogin(accounts, SimpleNamespace(tasks={}), lambda: session)
    payload = PasswordInput(platform=platform, username='tourist', password=PASSWORD)
    return manager, session, payload


def perform(manager, payload):
    return asyncio.run(manager.login('owner', payload))


@pytest.mark.parametrize('platform', ['atcoder', 'codeforces'])
def test_verified_settings_identity_is_required_and_preserves_other_accounts(tmp_path, platform):
    manager, session, payload = setup_manager(tmp_path, platform)
    manager.accounts.save(AccountList(items=[AccountInput(platform='luogu', handle='123', cookie='old=kept'),
                                           AccountInput(platform=platform, handle='another', cookie='other=kept')]))
    result = perform(manager, payload)
    assert result['status'] == 'authenticated' and result['account']['handle'] == 'tourist'
    assert result['official_url'] == LOGIN_URLS[platform]
    assert len(session.calls) == 3 and session.closed
    stored = manager.accounts.document()['accounts']
    assert stored[0]['cookie'] == 'old=kept' and stored[1]['cookie'] == 'other=kept'
    assert stored[2]['cookie'] == 'session=' + COOKIE
    assert PASSWORD not in manager.accounts.path.read_text()
    for secret in [PASSWORD, COOKIE, CSRF]: assert secret not in json.dumps(result)
    assert PASSWORD not in repr(payload)


@pytest.mark.parametrize('platform', ['atcoder', 'codeforces'])
def test_wrong_password_cannot_bind_even_after_successful_post(tmp_path, platform):
    manager, session, payload = setup_manager(tmp_path, platform, responses=[
        Response(login_page(platform)), Response(login_page(platform)), Response(login_page(platform))])
    result = perform(manager, payload)
    assert result['status'] == 'rejected' and result['reason'] == 'credentials_not_accepted'
    assert not manager.accounts.path.exists() and session.closed


@pytest.mark.parametrize('platform,location', [
    ('atcoder', '/login?continue=https%3A%2F%2Fatcoder.jp%2Fsettings'),
    ('codeforces', 'https://codeforces.com/enter?back=%2Fsettings%2Fgeneral')])
def test_known_official_settings_login_callback_is_rejected_without_following(tmp_path, platform, location):
    manager, session, payload = setup_manager(tmp_path, platform, responses=[
        Response(login_page(platform)), Response(login_page(platform)), Response(status=302, location=location)])
    result = perform(manager, payload)
    assert result['status'] == 'rejected' and result['reason'] == 'credentials_not_accepted'
    assert len(session.calls) == 3 and not manager.accounts.path.exists()


@pytest.mark.parametrize('stage', [0, 1, 2])
@pytest.mark.parametrize('challenge,reason', [('<div class="g-recaptcha"></div>', 'captcha'),
                                            ('<title>Just a moment</title>', 'browser_challenge')])
def test_challenge_at_every_stage_stops_without_persistence(tmp_path, stage, challenge, reason):
    responses = [Response(login_page('atcoder')), Response(status=302, location='/'),
                 Response(settings_page('atcoder'))]
    responses[stage] = Response(challenge, 403)
    manager, session, payload = setup_manager(tmp_path, responses=responses)
    result = perform(manager, payload)
    assert result['status'] == 'blocked' and result['reason'] == reason
    assert '其他浏览器' in result['message']
    assert len(session.calls) == stage + 1 and not manager.accounts.path.exists()


@pytest.mark.parametrize('location', ['https://evil.invalid/', '//evil.invalid/',
    'https://atcoder.jp@evil.invalid/', 'http://atcoder.jp/',
    'https://atcoder.jp:443/', '/login?continue=https://evil.invalid/',
    '/oauth/callback', '/#callback', '/settings?callback=evil',
    '/login?continue=%2Fsettings&continue=https://evil.invalid/',
    '/login?continue=%2Fsettings&other=1'])
def test_post_redirect_attack_is_never_followed(tmp_path, location):
    manager, session, payload = setup_manager(tmp_path, responses=[Response(login_page('atcoder')),
        Response(status=302, location=location)])
    result = perform(manager, payload)
    assert result['reason'] == 'unsafe_redirect' and len(session.calls) == 2
    assert not manager.accounts.path.exists()


@pytest.mark.parametrize('page', [
    login_page('atcoder').replace('https://atcoder.jp/login', 'https://evil.invalid/login'),
    login_page('atcoder').replace('</form>', '<input type="hidden" name="callback" value="https://evil.invalid"></form>'),
    login_page('atcoder').replace('name="csrf_token"', 'name="other"'),
    login_page('atcoder') + login_page('atcoder'),
    login_page('atcoder').replace('value="' + CSRF + '"', 'value=""'),
])
def test_unknown_form_does_not_receive_password(tmp_path, page):
    manager, session, payload = setup_manager(tmp_path, responses=[Response(page)])
    result = perform(manager, payload)
    assert result['reason'] == 'unsupported_form' and len(session.calls) == 1
    assert not manager.accounts.path.exists()


def test_expected_identity_and_public_profile_are_not_authority(tmp_path):
    manager, _, payload = setup_manager(tmp_path)
    payload.expected_handle = 'someone_else'
    assert perform(manager, payload)['reason'] == 'account_mismatch'
    assert not manager.accounts.path.exists()
    manager, _, payload = setup_manager(tmp_path, responses=[Response(login_page('atcoder')),
        Response(status=302, location='/'), Response('<a href="/users/tourist">public profile</a>')])
    assert perform(manager, payload)['reason'] == 'identity_unconfirmed'
    assert not manager.accounts.path.exists()


@pytest.mark.parametrize('stage', [0, 1, 2])
def test_oversized_response_never_binds(tmp_path, stage):
    responses = [Response(login_page('atcoder')), Response(status=302, location='/'),
                 Response(settings_page('atcoder'))]
    responses[stage] = Response(b'x' * (MAX_BODY + 1))
    manager, session, payload = setup_manager(tmp_path, responses=responses)
    assert perform(manager, payload)['reason'] == 'response_too_large'
    assert len(session.calls) == stage + 1 and not manager.accounts.path.exists()


@pytest.mark.parametrize('change', ['revision', 'revoked', 'clear', 'closed', 'crawl'])
def test_inflight_configuration_or_authorization_change_prevents_commit(tmp_path, change):
    manager, session, payload = setup_manager(tmp_path)
    def hook(session, count):
        if count != 3: return
        if change == 'revision':
            manager.accounts.save(AccountList(items=[AccountInput(platform='luogu', handle='456')]))
        elif change == 'revoked': manager.owner_valid = lambda _: False
        elif change == 'clear': manager.clear('owner')
        elif change == 'closed': manager.close()
        elif change == 'crawl': manager.jobs.tasks['active'] = ('crawl', SimpleNamespace(done=lambda: False))
    session.hook = hook
    if change == 'revision':
        assert perform(manager, payload)['reason'] == 'configuration_changed'
        assert manager.accounts.public()['items'][0]['handle'] == '456'
    else:
        with pytest.raises(HTTPException) as error: perform(manager, payload)
        assert error.value.status_code in {401, 409, 503}
    assert all(not a.get('cookie') for a in manager.accounts.document().get('accounts', []))
    assert session.closed and manager.active is None


def test_clear_other_session_does_not_cancel_current_owner(tmp_path):
    manager, session, payload = setup_manager(tmp_path)
    session.hook = lambda session, count: manager.clear('another-owner')
    assert perform(manager, payload)['status'] == 'authenticated'


@pytest.mark.parametrize('failure,reason', [(requests.Timeout(PASSWORD), 'timeout'),
                                         (requests.ConnectionError(PASSWORD), 'network_error')])
def test_network_error_never_echoes_exception_or_secret(tmp_path, failure, reason):
    manager, session, payload = setup_manager(tmp_path, responses=[failure])
    result = perform(manager, payload)
    assert result['reason'] == reason and PASSWORD not in json.dumps(result)
    assert session.closed and not manager.accounts.path.exists()


def test_busy_crawl_and_revoked_owner_do_not_contact_platform(tmp_path):
    manager, session, payload = setup_manager(tmp_path)
    manager.owner_valid = lambda _: False
    with pytest.raises(HTTPException) as error: perform(manager, payload)
    assert error.value.status_code == 401 and not session.calls
    manager.owner_valid = lambda _: True
    manager.jobs.tasks['active'] = ('crawl', SimpleNamespace(done=lambda: False))
    with pytest.raises(HTTPException) as error: perform(manager, payload)
    assert error.value.status_code == 409 and not session.calls


def test_request_fields_are_bounded():
    for changes in [dict(password=''), dict(password='x' * 257), dict(username='x' * 151),
                    dict(username='bad\nuser'), dict(platform='nowcoder'),
                    dict(expected_handle='https://evil.invalid'), dict(callback='evil')]:
        values = dict(platform='atcoder', username='tourist', password=PASSWORD)
        values.update(changes)
        with pytest.raises(ValidationError): PasswordInput(**values)


def test_route_uses_mounted_owner_and_public_result(tmp_path):
    manager, session, payload = setup_manager(tmp_path)
    app = FastAPI()
    app.state.jobs = manager.jobs
    register(app, manager.accounts)
    app.state.platform_login.session_factory = lambda: session
    app.state.multi_child = True
    authorized = [False]
    @app.middleware('http')
    async def mounted_owner(request, call_next):
        if authorized[0]: request.state.platform_login_owner = 'trusted-session-hash'
        return await call_next(request)
    with TestClient(app) as client:
        response = client.post('/api/platform-login/password', json=payload.model_dump(mode='json') | {'password': PASSWORD})
        assert response.status_code == 401 and not session.calls
    owners = []
    authorized[0] = True
    app.state.platform_login.owner_valid = lambda owner: owners.append(owner) or True
    with TestClient(app) as client:
        response = client.post('/api/platform-login/password', json={
            'platform': 'atcoder', 'username': 'tourist', 'password': PASSWORD})
    assert response.status_code == 200 and response.json()['status'] == 'authenticated'
    assert set(owners) == {'trusted-session-hash'}
    for secret in [PASSWORD, COOKIE, CSRF]: assert secret not in response.text


def test_cf_expected_handle_is_case_insensitive_and_atcoder_is_exact(tmp_path):
    manager, _, payload = setup_manager(tmp_path, 'codeforces')
    payload.expected_handle = 'TOURIST'
    assert perform(manager, payload)['status'] == 'authenticated'
    manager, _, payload = setup_manager(tmp_path, 'atcoder')
    payload.expected_handle = 'TOURIST'
    assert perform(manager, payload)['reason'] == 'account_mismatch'


def test_repeated_attempt_cooldown_and_upstream_rate_limit(tmp_path):
    manager, session, payload = setup_manager(tmp_path, responses=[Response(status=429)])
    assert perform(manager, payload)['reason'] == 'rate_limited'
    with pytest.raises(HTTPException) as error: perform(manager, payload)
    assert error.value.status_code == 429
    assert int(error.value.headers['Retry-After']) >= 59
    assert len(session.calls) == 1


def test_cookie_for_another_origin_is_never_saved(tmp_path):
    manager, session, payload = setup_manager(tmp_path)
    session.cookies.clear()
    session.cookies.set('session', COOKIE, domain='evil.invalid', path='/')
    assert perform(manager, payload)['reason'] == 'identity_unconfirmed'
    assert not manager.accounts.path.exists()


def test_cancelled_request_cannot_commit_and_does_not_close_busy_session(tmp_path):
    async def run():
        manager, session, payload = setup_manager(tmp_path)
        started = asyncio.Event()
        release = threading.Event()
        finished = threading.Event()
        loop = asyncio.get_running_loop()
        original_close = session.close
        def close():
            original_close()
            finished.set()
        session.close = close
        def hook(session, count):
            if count == 1:
                loop.call_soon_threadsafe(started.set)
                assert release.wait(3)
        session.hook = hook
        task = asyncio.create_task(manager.login('owner', payload))
        await asyncio.wait_for(started.wait(), 3)
        try:
            with pytest.raises(HTTPException) as error:
                await manager.login('another-owner', payload)
            assert error.value.status_code == 429
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
            assert not session.closed and not manager.accounts.path.exists()
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 3)
        assert session.closed and not manager.accounts.path.exists()
    asyncio.run(run())


def test_integrated_validation_response_never_echoes_password(tmp_path):
    from acmweb.app import create_app
    app = create_app(Settings(data_dir=tmp_path, config_path=tmp_path/'missing.toml', enable_codex=False))
    with TestClient(app) as client:
        for values in [dict(platform='atcoder', username='tourist', password=PASSWORD * 20),
                       dict(platform='atcoder', password=PASSWORD)]:
            response = client.post('/api/platform-login/password', json=values)
            assert response.status_code == 422 and PASSWORD not in response.text
            assert response.headers['Cache-Control'] == 'no-store'
    assert not (tmp_path/'accounts.web.toml').exists()


def test_javascript_rendered_verification_stops_before_sending_password(tmp_path):
    page = login_page('atcoder').replace('</form>', '<div class="challenge" data-sitekey="synthetic-public-key" data-theme="auto"></div></form>')
    page += '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>'
    accounts = Accounts(Settings(data_dir=tmp_path, config_path=tmp_path/'missing.toml'))
    upstream = Upstream('atcoder', [Response(page)])
    manager = PasswordLogin(accounts, SimpleNamespace(tasks={}), session_factory=lambda: upstream)
    result = asyncio.run(manager.login('owner', PasswordInput(platform='atcoder', username='tourist', password=PASSWORD)))
    assert result['status'] == 'blocked' and result['reason'] == 'browser_challenge'
    assert upstream.calls == [('GET', LOGIN_URLS['atcoder'])]
    assert not accounts.public()['configured']
    assert PASSWORD not in str(result) and COOKIE not in str(result) and CSRF not in str(result)


@pytest.mark.parametrize('suffix', [
    '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>',
    '<script src="https://untrusted.invalid/turnstile/v0/api.js"></script>',
])
def test_passive_script_alone_is_not_login_verification(tmp_path, suffix):
    from acmweb.platform_login import _form
    from bs4 import BeautifulSoup
    form, user_field = _form('atcoder', BeautifulSoup(login_page('atcoder') + suffix, 'html.parser'))
    assert form['csrf_token'] == CSRF and user_field == 'username'

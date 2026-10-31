"""Saved connection → verified identity regressions using synthetic upstream pages only."""
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from acmweb.accounts import AccountInput, AccountList, Accounts
from acmweb.credential_check import CredentialChecks
from acmweb.settings import Settings
from test_multiuser import site


class Response:
    status_code = 200
    headers = {}
    encoding = 'utf-8'

    def __init__(self, handle='Owner'):
        self.handle = handle

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, chunk_size):
        yield (f'<div class="lang-chooser"><a href="/profile/{self.handle}">{self.handle}</a>'
               '<a href="/logout">exit</a></div><form action="/settings/general"></form>').encode()


class Session:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def get(self, *args, **kwargs): return Response()


def social_rows(app, uid):
    # Read central storage directly: visiting /social/me could lazily claim and
    # conceal a missing callback in the original connect flow.
    with app.state.identities.connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM social_accounts WHERE user_id=?', (uid,))]


def wait_checks(request, who):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = request(who, 'GET', '/api/accounts/checks').json()
        if result.get('connections') and all(item['status'] not in {'queued', 'checking'} for item in result['connections']):
            return result
        time.sleep(.01)
    pytest.fail('background check did not finish')


def test_save_returns_before_network_and_background_claims_without_social_visit(site, monkeypatch):
    request, admin, member, app, *_ = site
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Delayed(Session):
        def get(self, *args, **kwargs):
            calls.append(kwargs['headers']['Cookie'])
            entered.set()
            assert release.wait(3)
            return Response()

    monkeypatch.setattr('acmweb.credential_check.requests.Session', Delayed)
    payload = {'items': [{'platform': 'codeforces', 'handle': 'Owner', 'cookie': 'session=SYNTHETIC-PRIVATE'}], 'auto_check': True}
    try:
        saved = request(admin, 'PUT', '/api/accounts', payload)
        assert saved.status_code == 200
        assert saved.json()['connection_checks'][0]['status'] == 'queued'
        assert entered.wait(2) and not release.is_set()
        assert social_rows(app, admin['user']['id']) == []
        assert request(member, 'GET', '/api/accounts/checks').json()['items'] == []
        # Repeated saves while pending share the same check.
        assert request(admin, 'PUT', '/api/accounts', payload).status_code == 200
    finally:
        release.set()
    result = wait_checks(request, admin)
    assert result['items'][0]['login_verified'] is True
    assert result['items'][0]['identity_claim']['status'] == 'claimed'
    assert len(social_rows(app, admin['user']['id'])) == 1
    assert calls == ['session=SYNTHETIC-PRIVATE']
    assert 'SYNTHETIC-PRIVATE' not in str(result) and 'credential_hash' not in str(result)
    # Connecting does not implicitly publish the user's profile/leaderboard.
    with app.state.identities.connect() as db:
        row = db.execute('SELECT * FROM social_profiles WHERE user_id=?', (admin['user']['id'],)).fetchone()
        assert row is None or not row['leaderboard']


def test_legacy_save_and_public_handle_do_not_check_or_claim(site, monkeypatch):
    request, admin, _, app, *_ = site
    def unexpected(*args, **kwargs): pytest.fail('configuration-only save contacted a platform')
    monkeypatch.setattr('acmweb.credential_check.requests.Session', unexpected)
    assert request(admin, 'PUT', '/api/accounts', {'items': [
        {'platform': 'codeforces', 'handle': 'Owner', 'cookie': 'session=SYNTHETIC'}]}).status_code == 200
    saved = request(admin, 'PUT', '/api/accounts', {'items': [
        {'platform': 'codeforces', 'handle': 'PublicOnly'}], 'auto_check': True})
    assert saved.json()['connection_checks'] == []
    assert social_rows(app, admin['user']['id']) == []


def test_manual_verified_check_immediately_claims_and_respects_optout_conflict(site, monkeypatch):
    request, admin, member, app, *_ = site
    monkeypatch.setattr('acmweb.credential_check.requests.Session', Session)
    for who in (admin, member):
        assert request(who, 'PUT', '/api/accounts', {'items': [
            {'platform': 'codeforces', 'handle': 'Owner', 'cookie': 'session=SYNTHETIC'}]}).status_code == 200
    result = request(admin, 'POST', '/api/accounts/check', {'platform': 'codeforces', 'handle': 'Owner'})
    assert result.json()['identity_claim']['status'] == 'claimed'
    assert len(social_rows(app, admin['user']['id'])) == 1
    conflict = request(member, 'POST', '/api/accounts/check', {'platform': 'codeforces', 'handle': 'Owner'})
    assert conflict.json()['login_verified'] is True
    assert conflict.json()['identity_claim']['status'] == 'conflict'
    assert social_rows(app, member['user']['id']) == []
    assert request(admin, 'DELETE', '/api/social/accounts/codeforces/Owner', {}).status_code == 200
    opted_out = request(admin, 'POST', '/api/accounts/check', {'platform': 'codeforces', 'handle': 'Owner'})
    assert opted_out.json()['identity_claim']['status'] == 'opted_out'
    assert social_rows(app, admin['user']['id']) == []


def configured(tmp_path):
    accounts = Accounts(Settings(data_dir=tmp_path, config_path=tmp_path/'missing.toml'))
    accounts.save(AccountList(items=[AccountInput(platform='codeforces', handle='Owner', cookie='session=old')]))
    return accounts


def test_changed_credential_supersedes_pending_check_and_deduplicates(tmp_path, monkeypatch):
    accounts = configured(tmp_path)
    checker = CredentialChecks(accounts)
    entered, release = threading.Event(), threading.Event()
    calls, claims = [], []
    checker.on_verified = lambda platform, handle: claims.append(checker.credential(platform, handle)[0])

    class Delayed(Session):
        def get(self, *args, **kwargs):
            cookie = kwargs['headers']['Cookie']
            calls.append(cookie)
            if cookie == 'session=old':
                entered.set()
                assert release.wait(3)
            return Response()
    monkeypatch.setattr('acmweb.credential_check.requests.Session', Delayed)

    async def run():
        checker.schedule_connections()
        assert await asyncio.to_thread(entered.wait, 2)
        first = checker.connection_tasks[('codeforces', 'Owner')]
        accounts.save(AccountList(items=[AccountInput(platform='codeforces', handle='Owner', cookie='session=new')]))
        checker.schedule_connections(); checker.schedule_connections()
        assert checker.connection_tasks[('codeforces', 'Owner')] is first
        assert checker.statuses()['items'] == []
        assert checker.statuses()['connections'][0]['status'] == 'queued'
        # Avoid waiting ten real seconds; the first check still uses old data.
        checker.last.clear()
        release.set()
        for _ in range(100):
            await asyncio.sleep(.01)
            if not checker.connection_tasks: break
        assert checker.connection_tasks == {}
        assert calls == ['session=old', 'session=new'] and claims == ['session=new']
        assert checker.statuses()['items'][0]['login_verified'] is True
        checker.schedule_connections()
        assert checker.connection_tasks == {}  # fresh completed credential is deduplicated
        await checker.close()
        assert checker.schedule_connections() == []
    try:
        asyncio.run(run())
    finally:
        release.set()


def test_pending_check_shutdown_cannot_claim_late_response(tmp_path, monkeypatch):
    checker = CredentialChecks(configured(tmp_path))
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    claims = []
    checker.on_verified = lambda *args: claims.append(args)
    class Delayed(Session):
        def get(self, *args, **kwargs):
            entered.set()
            assert release.wait(3)
            returned.set()
            return Response()
    monkeypatch.setattr('acmweb.credential_check.requests.Session', Delayed)
    async def run():
        checker.schedule_connections()
        assert await asyncio.to_thread(entered.wait, 2)
        await checker.close()
        assert not checker.connection_tasks
        release.set()
        assert await asyncio.to_thread(returned.wait, 2)
    try:
        asyncio.run(run())
    finally:
        release.set()
    assert claims == [] and checker.statuses()['items'] == []


def test_verified_callback_failures_are_safe_and_stale_credentials_cannot_trigger_it(tmp_path, monkeypatch):
    accounts = configured(tmp_path)
    checker = CredentialChecks(accounts)
    monkeypatch.setattr('acmweb.credential_check.requests.Session', Session)
    def unavailable(*args): raise RuntimeError('session=PRIVATE-ERROR')
    checker.on_verified = unavailable
    result = checker.check('codeforces', 'Owner')
    assert result['login_verified'] and result['identity_claim']['status'] == 'unavailable'
    assert 'PRIVATE-ERROR' not in str(checker.statuses())
    _, old = checker.credential('codeforces', 'Owner')
    accounts.save(AccountList(items=[AccountInput(platform='codeforces', handle='Owner', cookie='session=new')]))
    calls = []
    checker.on_verified = lambda *args: calls.append(args)
    checker.remember_result(result, old)
    assert checker.remember_verified('codeforces', 'Owner', 'session=old') is False
    with pytest.raises(HTTPException) as error:
        checker.check('codeforces', 'Owner', _expected_digest=old)
    assert error.value.status_code == 409 and calls == [] and checker.statuses()['items'] == []


@pytest.mark.parametrize('value', ['true', 'false', 1, 0, None])
def test_auto_check_requires_boolean(value):
    with pytest.raises(ValidationError): AccountList(items=[], auto_check=value)


def test_acm_login_resumes_saved_connections_without_extra_manual_steps(site, monkeypatch):
    from test_multiuser import PASSWORD
    request, admin, _, app, *_ = site
    calls = []
    class Verified(Session):
        def get(self, *args, **kwargs):
            calls.append(kwargs['headers']['Cookie'])
            return Response()
    monkeypatch.setattr('acmweb.credential_check.requests.Session', Verified)
    assert request(admin, 'PUT', '/api/accounts', {'items': [
        {'platform': 'codeforces', 'handle': 'Owner', 'cookie': 'session=SYNTHETIC'}]}).status_code == 200
    assert request(admin, 'GET', '/api/accounts/checks').json()['items'] == []
    assert calls == []  # ordinary GET does not silently access an external platform
    logged_in = request(None, 'POST', '/api/auth/login', {'username': 'kendas', 'password': PASSWORD})
    assert logged_in.status_code == 200 and logged_in.json()['connection_checks']
    checked = wait_checks(request, admin)
    assert checked['items'][0]['identity_claim']['status'] == 'claimed'
    assert len(social_rows(app, admin['user']['id'])) == 1
    assert request(None, 'POST', '/api/auth/login', {'username': 'kendas', 'password': PASSWORD}).status_code == 200
    assert calls == ['session=SYNTHETIC']


@pytest.mark.parametrize('page', [
    '<form action="/enter"><input name="password"></form>',
    '<a href="/profile/Owner">public profile</a>',
    '<div class="lang-chooser"><a href="/profile/Other">Other</a><a href="/logout">exit</a></div><form action="/settings/general"></form>',
])
def test_unverified_or_mismatched_result_never_calls_claim(tmp_path, monkeypatch, page):
    checker = CredentialChecks(configured(tmp_path))
    class Unverified(Response):
        def iter_content(self, chunk_size): yield page.encode()
    class Upstream(Session):
        def get(self, *args, **kwargs): return Unverified()
    monkeypatch.setattr('acmweb.credential_check.requests.Session', Upstream)
    claims = []
    checker.on_verified = lambda *args: claims.append(args)
    assert checker.check('codeforces', 'Owner')['login_verified'] is False
    assert claims == []


def test_background_exception_is_observable_without_secret_error_text(tmp_path, monkeypatch):
    checker = CredentialChecks(configured(tmp_path))
    def fail(*args, **kwargs): raise RuntimeError('session=PRIVATE-ERROR')
    monkeypatch.setattr(checker, 'check', fail)
    async def run():
        checker.schedule_connections()
        await asyncio.gather(*list(checker.connection_tasks.values()))
        await asyncio.sleep(0)
        assert checker.connection_tasks == {}
        result = checker.statuses()
        assert result['connections'][0]['status'] == 'failed'
        assert result['items'] == [] and 'PRIVATE-ERROR' not in str(result)
        await checker.close()
    asyncio.run(run())


def test_background_reconnect_waits_for_upstream_retry_after(tmp_path, monkeypatch):
    checker = CredentialChecks(configured(tmp_path))
    key = ('codeforces', 'Owner')
    _, fingerprint = checker.credential(*key)
    checker.last[key] = 1000
    checker.cache[key] = (fingerprint, {'platform': key[0], 'handle': key[1], 'checked_at': 1000,
        'reason': 'rate_limited', 'retry_after_seconds': 120, 'login_verified': False})
    delays, checks = [], []
    monkeypatch.setattr('acmweb.credential_check.time.time', lambda: 1005)
    async def sleep(delay): delays.append(delay)
    async def connect(key, digest): checks.append((key, digest))
    monkeypatch.setattr('acmweb.credential_check.asyncio.sleep', sleep)
    monkeypatch.setattr(checker, '_connect', connect)
    async def run():
        checker.schedule_connections()
        await asyncio.gather(*list(checker.connection_tasks.values()))
        assert delays == [115] and checks == [(key, fingerprint)]
        await checker.close()
    asyncio.run(run())

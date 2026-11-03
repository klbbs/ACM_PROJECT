"""Session timing regressions with the real detector and synthetic HTTP only."""
import asyncio
import json
from types import SimpleNamespace

import pytest
import requests
from fastapi import HTTPException

from acmweb import catalog
from acmweb.credential_check import CredentialChecks


AUTHENTICATED = ('<div id="navbar-collapse"><a href="/users/owner">owner</a>'
                 '<a href="/logout">logout</a></div><form action="/settings"></form>')


@pytest.fixture
def detector(monkeypatch):
    now = [2000000000]
    calls = []
    upstream = {'status': 200, 'html': AUTHENTICATED, 'error': None}
    monkeypatch.setattr(catalog.time, 'time', lambda: now[0])
    account = {'platform': 'atcoder', 'handle': 'owner', 'cookie': 'session=private-value'}
    accounts = SimpleNamespace(document=lambda: {'accounts': [account]})
    checker = CredentialChecks(accounts)
    accounts.credential_checks = checker

    class Response:
        encoding = 'utf-8'
        headers = {}

        def __enter__(self):
            self.status_code = upstream['status']
            return self

        def __exit__(self, *args):
            pass

        def iter_content(self, chunk_size):
            yield upstream['html'].encode()

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, **kwargs):
            assert url == 'https://atcoder.jp/settings'
            assert kwargs['allow_redirects'] is False
            calls.append(url)
            if upstream['error']:
                raise upstream['error']
            return Response()

    monkeypatch.setattr('acmweb.credential_check.requests.Session', Session)

    def seed(age, *, status='authenticated', reason='identity_verified', retry_after=10):
        _, digest = checker.credential('atcoder', 'owner')
        checker.cache[('atcoder', 'owner')] = (digest, dict(
            platform='atcoder', handle='owner', checked_at=now[0] - age,
            status=status, reason=reason, login_verified=status == 'authenticated',
            retry_after_seconds=retry_after))

    return SimpleNamespace(accounts=accounts, checker=checker, now=now, calls=calls,
                           upstream=upstream, account=account, seed=seed)


@pytest.mark.parametrize('age', [0, 59, 230, 299])
def test_default_keeps_authentication_until_real_ttl(detector, age):
    detector.seed(age)
    summary = asyncio.run(catalog.refresh_recommendation_sessions(detector.accounts))
    assert summary['skipped_fresh'] == 1 and summary['checked'] == 0
    assert not detector.calls


def test_minimum_validity_renews_nearly_expired_authentication(detector):
    detector.seed(250)
    summary = asyncio.run(catalog.refresh_recommendation_sessions(
        detector.accounts, minimum_validity=90))
    assert summary['checked'] == 1 and len(detector.calls) == 1
    item = detector.checker.statuses()['items'][0]
    assert item['checked_at'] == detector.now[0] and item['login_verified']


def test_minimum_validity_does_not_refresh_more_than_needed(detector):
    detector.seed(200)
    summary = asyncio.run(catalog.refresh_recommendation_sessions(
        detector.accounts, minimum_validity=90))
    assert summary['skipped_fresh'] == 1 and not detector.calls


@pytest.mark.parametrize('status,reason', [
    ('unknown', 'timeout'), ('blocked', 'browser_challenge'),
    ('expired', 'not_logged_in'), ('account_mismatch', 'identity_mismatch'),
])
@pytest.mark.parametrize('age,requests_expected', [(59, 0), (60, 1), (200, 1)])
def test_negative_cache_uses_detector_reuse_window_not_authentication_ttl(
        detector, status, reason, age, requests_expected):
    detector.seed(age, status=status, reason=reason)
    summary = asyncio.run(catalog.refresh_recommendation_sessions(detector.accounts))
    assert len(detector.calls) == requests_expected
    assert summary['skipped_cooldown'] == 1 - requests_expected
    assert summary['checked'] == requests_expected


@pytest.mark.parametrize('age,requests_expected', [(60, 0), (119, 0), (120, 1)])
def test_negative_cache_honors_upstream_retry_after(detector, age, requests_expected):
    detector.seed(age, status='blocked', reason='rate_limited', retry_after=120)
    summary = asyncio.run(catalog.refresh_recommendation_sessions(detector.accounts))
    assert len(detector.calls) == requests_expected
    assert summary['skipped_cooldown'] == 1 - requests_expected


def test_expired_cache_is_renewed_without_force_refresh(detector, monkeypatch):
    detector.seed(301)
    original = detector.checker.check
    def check(*args, **kwargs):
        assert args == ('atcoder', 'owner') and not kwargs
        return original(*args, **kwargs)
    monkeypatch.setattr(detector.checker, 'check', check)
    summary = asyncio.run(catalog.refresh_recommendation_sessions(detector.accounts))
    assert summary['checked'] == 1 and len(detector.calls) == 1


def test_failed_renewal_never_inherits_old_authentication(detector):
    detector.seed(290)
    detector.upstream['error'] = requests.Timeout('private-upstream-diagnostics')
    summary = asyncio.run(catalog.refresh_recommendation_sessions(
        detector.accounts, minimum_validity=90))
    assert summary['checked'] == 1
    assert catalog.verified_platforms(detector.accounts) == []
    item = detector.checker.statuses()['items'][0]
    assert item['status'] == 'unknown' and not item['login_verified']
    encoded = json.dumps(summary)
    assert 'private' not in encoded and 'owner' not in encoded
    assert all(type(count) is int for count in summary.values())


def test_public_accounts_are_not_checked(detector):
    detector.account['cookie'] = ''
    summary = asyncio.run(catalog.refresh_recommendation_sessions(detector.accounts))
    assert summary['skipped_public'] == 1 and not detector.calls


def test_existing_busy_cooldown_is_not_bypassed(detector):
    detector.checker.last[('atcoder', 'owner')] = detector.now[0]
    summary = asyncio.run(catalog.refresh_recommendation_sessions(detector.accounts))
    assert summary['busy'] == 1 and not detector.calls
    assert catalog.verified_platforms(detector.accounts) == []


def test_non_rate_limit_error_still_propagates(detector, monkeypatch):
    def check(*args):
        raise HTTPException(409, 'account changed')
    monkeypatch.setattr(detector.checker, 'check', check)
    with pytest.raises(HTTPException) as error:
        asyncio.run(catalog.refresh_recommendation_sessions(detector.accounts))
    assert error.value.status_code == 409


def test_missing_detector_produces_safe_zero_summary():
    summary = asyncio.run(catalog.refresh_recommendation_sessions(SimpleNamespace()))
    assert all(type(count) is int and count == 0 for count in summary.values())

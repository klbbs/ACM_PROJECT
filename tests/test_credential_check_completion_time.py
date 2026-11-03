"""Credential timestamps use synthetic HTTP responses and a controlled clock."""
from types import SimpleNamespace

import pytest
import requests
from fastapi import HTTPException

from acmweb.credential_check import CredentialChecks


IDENTITY = ('<div class="lang-chooser"><a href="/profile/demo">demo</a>'
            '<a href="/logout">logout</a></div>'
            '<form action="/settings/general"></form>')


@pytest.fixture
def harness(monkeypatch):
    now = [1000.0]
    calls = []
    account = {'platform': 'codeforces', 'handle': 'demo', 'cookie': 'session=SYNTHETIC'}
    accounts = SimpleNamespace(document=lambda: {'accounts': [account]})
    monkeypatch.setattr('acmweb.credential_check.time.time', lambda: now[0])

    def install(*, status=200, body=IDENTITY, headers=None, failure=None, on_request=None):
        class Response:
            status_code = status
            encoding = 'utf-8'

            def __enter__(self): return self
            def __exit__(self, *args): pass
            def iter_content(self, chunk_size): yield body.encode()

        Response.headers = headers or {}

        class Session:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def get(self, *args, **kwargs):
                calls.append(now[0])
                now[0] += 12
                if on_request:
                    on_request()
                if failure:
                    raise failure
                return Response()

        monkeypatch.setattr('acmweb.credential_check.requests.Session', Session)

    install()
    return now, calls, account, CredentialChecks(accounts), install


def test_success_timestamp_starts_when_identity_evidence_finishes(harness):
    now, calls, _, checker, _ = harness
    result = checker.check('codeforces', 'demo')
    assert result['login_verified'] is True
    assert result['checked_at'] == 1012
    assert checker.last[('codeforces', 'demo')] == 1000  # Request cooldown unchanged.
    now[0] = 1300
    assert checker.statuses()['items'][0]['checked_at'] == 1012
    now[0] = 1312
    assert checker.statuses()['items'] == []
    assert calls == [1000]


@pytest.mark.parametrize('failure,reason', [
    (requests.Timeout('SYNTHETIC-PRIVATE'), 'timeout'),
    (requests.ConnectionError('SYNTHETIC-PRIVATE'), 'network_error'),
])
def test_failure_timestamp_and_cached_reuse_do_not_refresh_evidence(harness, failure, reason):
    now, calls, _, checker, install = harness
    install(failure=failure)
    first = checker.check('codeforces', 'demo')
    assert first['checked_at'] == 1012 and first['reason'] == reason
    assert not first['login_verified']
    now[0] = 1050
    cached = checker.check('codeforces', 'demo')
    assert cached['cached'] is True and cached['checked_at'] == 1012
    assert calls == [1000]
    assert 'SYNTHETIC-PRIVATE' not in str(checker.statuses())


def test_success_reuse_keeps_original_stamp_and_force_still_checks(harness):
    now, calls, _, checker, _ = harness
    first = checker.check('codeforces', 'demo')
    now[0] = 1030
    reused = checker.check('codeforces', 'demo')
    assert reused['cached'] and reused['checked_at'] == first['checked_at'] == 1012
    forced = checker.check('codeforces', 'demo', force_refresh=True)
    assert not forced['cached'] and forced['checked_at'] == 1042
    assert calls == [1000, 1030]


def test_retry_after_remains_enforced_after_response_completes(harness):
    now, calls, _, checker, install = harness
    install(status=429, body='', headers={'Retry-After': '120'})
    result = checker.check('codeforces', 'demo')
    assert result['checked_at'] == 1012 and result['retry_after_seconds'] == 120
    now[0] = 1131
    with pytest.raises(HTTPException) as error:
        checker.check('codeforces', 'demo', force_refresh=True)
    assert error.value.status_code == 429
    assert error.value.headers['Retry-After'] == '1'
    assert calls == [1000]
    now[0] = 1132
    assert checker.check('codeforces', 'demo', force_refresh=True)['checked_at'] == 1144
    assert calls == [1000, 1132]


def test_credential_changed_during_request_is_never_cached_or_claimed(harness):
    _, calls, account, checker, install = harness
    claims = []
    checker.on_verified = lambda *args: claims.append(args)
    install(on_request=lambda: account.update(cookie='session=NEW-SYNTHETIC'))
    result = checker.check('codeforces', 'demo')
    assert result['checked_at'] == 1012
    assert checker.statuses()['items'] == []
    assert claims == [] and calls == [1000]


def test_wrong_identity_stays_unverified_with_completed_timestamp(harness):
    _, _, _, checker, install = harness
    install(body=IDENTITY.replace('demo', 'other'))
    result = checker.check('codeforces', 'demo')
    assert result['checked_at'] == 1012
    assert result['status'] == 'account_mismatch' and not result['login_verified']

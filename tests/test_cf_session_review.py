"""Independent synthetic review: no real Firefox profile or upstream requests."""
from types import SimpleNamespace
import json
import pytest
from acmweb.credential_check import CredentialChecks, URLS

PRINCIPAL = '<div class="lang-chooser"><a href="/profile/Demo">Demo</a><a href="/logout">Exit</a></div><form action="/settings/general"></form>'

class Response:
    encoding = 'utf-8'
    def __init__(self, status=200, body='', headers=None):
        self.status_code, self.body, self.headers = status, body, headers or {}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, chunk_size): yield self.body.encode()

def harness(monkeypatch, cookie, response):
    state = {'cookie': cookie, 'calls': 0, 'now': 1000.0, 'response': response}
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url, **kwargs):
            assert url == URLS['codeforces']
            assert kwargs['allow_redirects'] is False
            assert kwargs['stream'] is True
            state['calls'] += 1
            return state['response']
    monkeypatch.setattr('acmweb.credential_check.requests.Session', Session)
    monkeypatch.setattr('acmweb.credential_check.time.time', lambda: state['now'])
    accounts = SimpleNamespace(document=lambda: {'accounts': [{'platform': 'codeforces', 'handle': 'demo', 'cookie': state['cookie']}]})
    return CredentialChecks(accounts), state

@pytest.mark.parametrize('response', [
    Response(200, '<a href="/profile/demo">public profile</a>'),
    Response(200, '<form action="/enter"><input type="password"></form>'),
    Response(302, headers={'Location': '/enter'}),
    Response(403, '<div class="g-recaptcha"></div>'),
    Response(418, 'restricted'),
])
def test_http_only_named_session_never_proves_identity(monkeypatch, response):
    checker, _ = harness(monkeypatch, 'JSESSIONID=synthetic-private-value', response)
    result = checker.check('codeforces', 'demo')
    assert result['cookie_diagnostics']['cf_session_cookie_present'] is True
    assert result['login_verified'] is False
    assert result['status'] != 'authenticated'
    assert result['auto_submit_supported'] is False
    assert 'synthetic-private-value' not in json.dumps(result)

def test_cookie_replacement_discards_success_before_reverification(monkeypatch):
    checker, state = harness(monkeypatch, 'JSESSIONID=synthetic-old', Response(200, PRINCIPAL))
    assert checker.check('codeforces', 'demo')['login_verified'] is True
    state.update(cookie='JSESSIONID=synthetic-new', now=1011.0, response=Response(302, headers={'Location': '/enter'}))
    assert checker.statuses() == {'items': []}
    result = checker.check('codeforces', 'demo')
    assert state['calls'] == 2
    assert result['status'] == 'expired' and result['login_verified'] is False
    assert not result['cached']

def test_mismatched_principal_never_becomes_green_with_session_cookie(monkeypatch):
    checker, _ = harness(monkeypatch, 'JSESSIONID=synthetic-private', Response(200, PRINCIPAL.replace('/profile/Demo', '/profile/Other')))
    result = checker.check('codeforces', 'demo')
    assert result['status'] == 'account_mismatch'
    assert result['matched_account'] == 'Other'
    assert result['login_verified'] is False

def test_no_session_cookie_still_allows_matched_official_identity(monkeypatch):
    checker, _ = harness(monkeypatch, 'other=synthetic-private', Response(200, PRINCIPAL))
    result = checker.check('codeforces', 'demo')
    assert result['cookie_diagnostics']['cf_session_cookie_present'] is False
    assert result['login_verified'] is True
    assert result['status'] == 'authenticated'
    assert not any('未发现' in item for item in result['guidance'])

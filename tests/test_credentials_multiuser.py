"""Credential routes retain the outer session boundary and per-user credentials."""
from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.settings import Settings


def test_credential_routes_require_session_csrf_and_isolate_workspace_cache(tmp_path, monkeypatch):
    calls = []

    class Response:
        status_code = 200
        headers = {}
        encoding = 'utf-8'
        def __init__(self, cookie): self.cookie = cookie
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, chunk_size):
            handle = 'same_user' if self.cookie == 'session=OWNER-SECRET' else 'other_user'
            yield (f'<div class="lang-chooser"><a href="/profile/{handle}">{handle}</a>'
                   '<a href="/logout">logout</a></div><form action="/settings/general"></form>').encode()

    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url, **kwargs):
            assert url == 'https://codeforces.com/settings/general'
            assert kwargs['stream'] is True and kwargs['allow_redirects'] is False
            calls.append(kwargs['headers']['Cookie'])
            return Response(kwargs['headers']['Cookie'])

    monkeypatch.setattr('acmweb.credential_check.requests.Session', Session)
    settings = Settings(data_dir=tmp_path, config_path=tmp_path/'unused.toml', multi_user=True,
                        access_token='integration-setup-token-long-enough', enable_codex=False,
                        allowed_hosts=('testserver',))
    app = create_app(settings)
    with TestClient(app, base_url='https://testserver') as client:
        def request(who, method, path, body=None, csrf=True):
            headers = {'Cookie':'', 'Origin':'https://testserver', 'X-ACM-Request':'1'}
            if who:
                headers['Cookie'] = 'acm_user_session=' + who['cookie']
                if csrf: headers['X-CSRF-Token'] = who['csrf_token']
            return client.request(method, path, headers=headers, **({'json':body} if body is not None else {}))
        def capture(response):
            assert response.status_code == 200, response.text
            cookie = response.cookies['acm_user_session']
            status = client.get('/api/auth/status', headers={'Cookie':'acm_user_session='+cookie}).json()
            return dict(status, cookie=cookie)
        admin = capture(request(None,'POST','/api/auth/setup', {
            'username':'kendas','password':'Test-password-for-integration-123','setup_token':settings.access_token}))
        invite = request(admin,'POST','/api/admin/invitations',{}).json()['invitation']
        member = capture(request(None,'POST','/api/auth/register', {
            'username':'member','password':'Test-password-for-integration-123','invitation':invite}))

        payload = {'platform':'codeforces','handle':'same_user'}
        assert request(None,'GET','/api/accounts/checks').status_code == 401
        assert request(None,'POST','/api/accounts/check',payload).status_code == 401
        assert request(member,'POST','/api/accounts/check',payload,csrf=False).status_code == 403
        assert calls == []

        for who, handle, cookie in ((admin,'same_user','session=OWNER-SECRET'),
                                    (member,'member_only','session=MEMBER-SECRET')):
            response = request(who,'PUT','/api/accounts',{'items':[dict(platform='codeforces',handle=handle,cookie=cookie)]})
            assert response.status_code == 200, response.text
            assert 'SECRET' not in response.text
        # Guessing another user's handle must neither resolve its configuration nor touch the platform.
        response = request(member,'POST','/api/accounts/check',payload)
        assert response.status_code == 404 and calls == []
        owner = request(admin,'POST','/api/accounts/check',payload)
        assert owner.status_code == 200 and owner.json()['status'] == 'authenticated'
        assert calls == ['session=OWNER-SECRET']
        assert request(member,'GET','/api/accounts/checks').json()['items'] == []

        # Even equal platform handles use the member's own credential and cache.
        assert request(member,'PUT','/api/accounts',{'items':[dict(payload,cookie='session=MEMBER-SECRET')]}).status_code == 200
        other = request(member,'POST','/api/accounts/check',payload)
        assert other.status_code == 200 and other.json()['status'] == 'account_mismatch'
        assert calls == ['session=OWNER-SECRET','session=MEMBER-SECRET']
        own_cached = request(admin,'GET','/api/accounts/checks')
        member_cached = request(member,'GET','/api/accounts/checks')
        assert own_cached.json()['items'][0]['status'] == 'authenticated'
        assert member_cached.json()['items'][0]['status'] == 'account_mismatch'
        for response in (owner, other, own_cached, member_cached):
            assert 'SECRET' not in response.text and 'session=' not in response.text
        assert request(admin,'POST','/api/accounts/check',payload).json()['cached'] is True
        assert len(calls) == 2
        # An explicit browser-recovery retry must reach the authenticated owner's
        # own workspace and perform a new request, without evading the cooldown.
        retry_payload = dict(payload, force_refresh=True)
        assert request(None,'POST','/api/accounts/check',retry_payload).status_code == 401
        assert request(member,'POST','/api/accounts/check',retry_payload,csrf=False).status_code == 403
        too_soon = request(admin,'POST','/api/accounts/check',retry_payload)
        assert too_soon.status_code == 429 and int(too_soon.headers['Retry-After']) > 0
        assert len(calls) == 2
        owner_checker = app.state.workspaces.apps[admin['user']['id']][0].state.credential_checks
        owner_checker.last[('codeforces','same_user')] -= 11
        rechecked = request(admin,'POST','/api/accounts/check',retry_payload)
        assert rechecked.status_code == 200 and rechecked.json()['cached'] is False
        assert rechecked.json()['status'] == 'authenticated'
        assert calls == ['session=OWNER-SECRET','session=MEMBER-SECRET','session=OWNER-SECRET']
        assert request(member,'GET','/api/accounts/checks').json()['items'][0]['status'] == 'account_mismatch'
        assert 'SECRET' not in rechecked.text and 'session=' not in rechecked.text

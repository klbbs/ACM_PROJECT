"""Independent route-level review: synthetic credentials, no upstream requests."""
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.settings import Settings


def test_password_login_outer_csrf_isolation_and_inflight_logout(tmp_path, monkeypatch):
    calls = []
    started, release = threading.Event(), threading.Event()

    def fake_login(session, platform, username, password):
        calls.append((platform, username))
        if username == 'pending':
            started.set()
            assert release.wait(5)
        return username, 'session=synthetic-' + username

    monkeypatch.setattr('acmweb.platform_login._login', fake_login)
    settings = Settings(data_dir=tmp_path, config_path=tmp_path/'unused.toml', multi_user=True,
                        access_token='independent-setup-token-long-enough', enable_codex=False,
                        allowed_hosts=('testserver',))
    app = create_app(settings)
    with TestClient(app, base_url='https://testserver') as client:
        def request(who, path, body, csrf=True):
            headers = {'Cookie': '', 'Origin': 'https://testserver', 'X-ACM-Request': '1'}
            if who:
                headers['Cookie'] = 'acm_user_session=' + who['cookie']
                if csrf:
                    headers['X-CSRF-Token'] = who['csrf_token']
            return client.post(path, headers=headers, json=body)

        def capture(response):
            assert response.status_code == 200, response.text
            cookie = response.cookies['acm_user_session']
            info = client.get('/api/auth/status', headers={'Cookie': 'acm_user_session='+cookie}).json()
            return dict(info, cookie=cookie)

        admin = capture(request(None, '/api/auth/setup', dict(username='kendas',
            password='Synthetic-local-password-123', setup_token=settings.access_token)))
        invite = request(admin, '/api/admin/invitations', {}).json()['invitation']
        member = capture(request(None, '/api/auth/register', dict(username='member',
            password='Synthetic-local-password-123', invitation=invite)))
        payload = dict(platform='atcoder', username='owner', password='synthetic-platform-password')
        assert request(None, '/api/platform-login/password', payload).status_code == 401
        assert request(member, '/api/platform-login/password', payload, csrf=False).status_code == 403
        assert not calls
        for who, username in [(admin, 'owner'), (member, 'member')]:
            response = request(who, '/api/platform-login/password', dict(payload, username=username))
            assert response.status_code == 200 and response.json()['status'] == 'authenticated'
            assert 'synthetic-platform-password' not in response.text
            assert 'synthetic-' not in response.text
        admin_child = app.state.workspaces.apps[admin['user']['id']][0]
        member_child = app.state.workspaces.apps[member['user']['id']][0]
        assert admin_child.state.platform_login.accounts.document()['accounts'][0]['handle'] == 'owner'
        assert member_child.state.platform_login.accounts.document()['accounts'][0]['handle'] == 'member'

        manager = admin_child.state.platform_login
        manager.retry_at = 0
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(request, admin, '/api/platform-login/password', dict(payload, username='pending'))
            try:
                assert started.wait(5)
                assert request(admin, '/api/auth/logout', {}).status_code == 200
            finally:
                release.set()
            response = pending.result(timeout=5)
        assert response.status_code in {401, 409}
        assert manager.accounts.document()['accounts'][0]['handle'] == 'owner'
        assert len(manager.accounts.document()['accounts']) == 1

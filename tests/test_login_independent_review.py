"""Independent route-level browser-login ownership checks with synthetic flows."""
from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.settings import Settings


class BrowserManager:
    instances = []

    def __init__(self, *args):
        self.calls = []
        self.owner_valid = lambda owner: True
        self.closed = False
        self.instances.append(self)

    def clear(self, owner=None):
        self.calls.append(('clear', owner))

    async def aclose(self):
        self.closed = True

    async def start(self, owner, platform, expected_handle):
        assert self.owner_valid(owner)
        self.calls.append(('start', owner, platform, expected_handle))
        return {'login_id': 'synthetic-browser-flow-id', 'status': 'human_required'}


def test_multiuser_browser_login_owner_isolation_and_logout_revocation(tmp_path, monkeypatch):
    BrowserManager.instances.clear()
    monkeypatch.setattr('acmweb.browser_login_routes.BrowserLogin', BrowserManager)
    settings = Settings(data_dir=tmp_path, config_path=tmp_path/'unused.toml', multi_user=True,
                        access_token='independent-setup-token-long-enough', enable_codex=False,
                        allowed_hosts=('testserver',))
    app = create_app(settings)
    with TestClient(app, base_url='https://testserver') as client:
        def request(who, path, body=None, csrf=True):
            headers = {'Cookie': '', 'Origin': 'https://testserver', 'X-ACM-Request': '1'}
            if who:
                headers['Cookie'] = 'acm_user_session=' + who['cookie']
                if csrf:
                    headers['X-CSRF-Token'] = who['csrf_token']
            return client.post(path, headers=headers, json=body or {})

        def capture(response):
            assert response.status_code == 200, response.text
            cookie = response.cookies['acm_user_session']
            status = client.get('/api/auth/status', headers={'Cookie': 'acm_user_session=' + cookie}).json()
            return dict(status, cookie=cookie)

        admin = capture(request(None, '/api/auth/setup', {
            'username': 'kendas', 'password': 'Synthetic-local-password-123',
            'setup_token': settings.access_token,
        }))
        invitation = request(admin, '/api/admin/invitations').json()['invitation']
        member = capture(request(None, '/api/auth/register', {
            'username': 'member', 'password': 'Synthetic-local-password-123',
            'invitation': invitation,
        }))

        assert request(None, '/api/platform-login/browser/start', {'platform': 'luogu'}).status_code == 401
        assert request(admin, '/api/platform-login/browser/start',
                       {'platform': 'luogu'}, csrf=False).status_code == 403
        assert request(admin, '/api/platform-login/password', {}).status_code in {404, 405}
        for who, expected in ((admin, '123'), (member, '456')):
            response = request(who, '/api/platform-login/browser/start', {
                'platform': 'luogu', 'expected_handle': expected,
            })
            assert response.status_code == 200, response.text

        admin_manager = app.state.workspaces.apps[admin['user']['id']][0].state.browser_login
        member_manager = app.state.workspaces.apps[member['user']['id']][0].state.browser_login
        admin_owner = admin_manager.calls[-1][1]
        member_owner = member_manager.calls[-1][1]
        assert admin_manager.calls[-1] == ('start', admin_owner, 'luogu', '123')
        assert member_manager.calls[-1] == ('start', member_owner, 'luogu', '456')
        assert admin_owner != member_owner

        assert request(admin, '/api/auth/logout').status_code == 200
        assert admin_manager.calls[-1] == ('clear', admin_owner)
        assert member_manager.calls[-1] == ('start', member_owner, 'luogu', '456')

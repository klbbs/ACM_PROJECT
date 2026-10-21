"""Exercise actual multi-user dispatch, authentication and private record ownership."""
from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.settings import Settings


def test_member_records_stay_private_and_require_csrf(tmp_path):
    settings = Settings(data_dir=tmp_path, config_path=tmp_path/'absent.toml', multi_user=True,
                        enable_codex=False, allow_env_ai_key=False,
                        access_token='synthetic-records-setup-token', allowed_hosts=('testserver',))
    with TestClient(create_app(settings), base_url='https://testserver') as client:
        def capture(response):
            assert response.status_code == 200
            cookie = response.cookies['acm_user_session']
            status = client.get('/api/auth/status', headers={'Cookie': 'acm_user_session='+cookie}).json()
            return {'cookie': cookie, 'csrf': status['csrf_token']}

        def request(who, method, path, body=None, csrf=True):
            headers = {'Cookie': '', 'Origin': 'https://testserver', 'X-ACM-Request': '1'}
            if who:
                headers['Cookie'] = 'acm_user_session='+who['cookie']
                if csrf:
                    headers['X-CSRF-Token'] = who['csrf']
            return client.request(method, path, headers=headers, **({'json': body} if body is not None else {}))

        admin = capture(request(None, 'POST', '/api/auth/setup', {
            'username': 'kendas', 'password': 'Synthetic-password-123', 'setup_token': settings.access_token,
        }))
        invite = request(admin, 'POST', '/api/admin/invitations', {}).json()['invitation']
        member = capture(request(None, 'POST', '/api/auth/register', {
            'username': 'member', 'password': 'Synthetic-password-123', 'invitation': invite,
        }))
        path = '/api/problem-records/codeforces:4-A'
        assert request(None, 'GET', path).status_code == 401
        assert request(member, 'PUT', path, {'revision': 0, 'note': 'private'}, csrf=False).status_code == 403
        saved = request(admin, 'PUT', path, {'revision': 0, 'favorite': True, 'note': '<img src=x> owner note'})
        assert saved.status_code == 200
        assert request(member, 'GET', path).json()['note'] == ''
        assert request(member, 'GET', '/api/problem-records').json()['items'] == []
        assert request(member, 'PUT', path, {'revision': 0, 'note': 'member note'}).status_code == 200
        assert request(admin, 'GET', path).json()['note'] == '<img src=x> owner note'
        assert request(member, 'GET', path).json()['note'] == 'member note'
        assert request(member, 'PUT', path, {'revision': 1, 'note': 'overwrite', 'user_id': 'other'}).status_code == 422
        assert request(admin, 'POST', '/api/auth/logout', {}).status_code == 200
        assert request(admin, 'GET', path).status_code == 401
        assert request(member, 'GET', path).status_code == 200

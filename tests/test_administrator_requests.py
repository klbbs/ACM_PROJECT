"""Development checks using disposable identity databases, never live users."""
import time
import pytest
from fastapi.testclient import TestClient
from acmweb.app import create_app
from acmweb.settings import Settings
from acmweb.users import Users, csrf_token


@pytest.fixture
def site(tmp_path):
    settings = Settings(data_dir=tmp_path, multi_user=True, enable_codex=False,
                        access_token='development-setup-token-long-enough')
    app = create_app(settings)
    store = app.state.identities
    admin = store.create('kendas', 'Development-password-123', setup=True)
    with store.connect() as db:
        hashed = db.execute('SELECT password_hash FROM users WHERE id=?', (admin,)).fetchone()[0]
        for uid in ['member-one', 'member-two']:
            db.execute('INSERT INTO users VALUES(?,?,?,?,1,?)', (uid, uid.replace('-', '_'), hashed, 'member', int(time.time())))
    tokens = {uid: store.session(uid) for uid in [admin, 'member-one', 'member-two']}
    with TestClient(app) as client:
        def request(uid, method, path, body=None, csrf=True):
            token = tokens.get(uid, '')
            headers = {'Cookie': 'acm_user_session='+token, 'X-ACM-Request': '1'}
            if csrf:
                headers['X-CSRF-Token'] = csrf_token(token)
            return client.request(method, path, headers=headers, **({'json': body} if body is not None else {}))
        yield app, store, tokens, admin, request


def apply(request, who='member-one'):
    response = request(who, 'POST', '/api/auth/administrator-requests', {'reason': '愿意维护共享题面和社区内容'})
    assert response.status_code == 200, response.text
    return response.json()['id']


def test_member_cannot_grant_admin_or_self_approve(site):
    _, store, _, admin, request = site
    rid = apply(request)
    assert request('member-one', 'GET', '/api/admin/administrator-requests').status_code == 403
    assert request('member-one', 'POST', f'/api/admin/administrator-requests/{rid}/review', {'decision': 'approved'}).status_code == 403
    assert request(admin, 'PUT', '/api/admin/users/member-one', {'role': 'admin', 'active': True}).status_code == 409
    assert request('member-one', 'POST', '/api/auth/administrator-requests', {'reason': 'duplicate'}).status_code == 409
    assert request('member-two', 'GET', '/api/auth/administrator-requests').json() == {'items': []}
    with store.connect() as db:
        assert db.execute("SELECT role FROM users WHERE id='member-one'").fetchone()[0] == 'member'


def test_approval_requires_csrf_revokes_all_sessions_and_survives_restart(site):
    _, store, tokens, admin, request = site
    second_session = store.session('member-one')
    rid = apply(request)
    url = f'/api/admin/administrator-requests/{rid}/review'
    assert request(admin, 'POST', url, {'decision': 'approved'}, csrf=False).status_code == 403
    result = request(admin, 'POST', url, {'decision': 'approved', 'note': '已核实身份'})
    assert result.status_code == 200, result.text
    assert result.json()['reauthenticate'] is True
    assert store.resolve(tokens['member-one']) is None
    assert store.resolve(second_session) is None
    assert request(admin, 'POST', url, {'decision': 'approved'}).status_code == 409
    tokens['member-one'] = store.session('member-one')
    assert request('member-one', 'GET', '/api/admin/users').status_code == 200
    restarted = Users(store.path)
    restarted.normalize_administrator()
    assert restarted.resolve(tokens['member-one'])['role'] == 'admin'
    with store.connect() as db:
        row = db.execute('SELECT reviewed_by,status,review_note FROM administrator_requests WHERE id=?', (rid,)).fetchone()
        assert tuple(row) == (admin, 'approved', '已核实身份')
        assert db.execute("SELECT 1 FROM audit WHERE actor=? AND event='administrator.approved' AND target='member-one'", (admin,)).fetchone()


def test_reject_does_not_promote_or_revoke_session(site):
    _, store, tokens, admin, request = site
    rid = apply(request)
    assert request(admin, 'POST', f'/api/admin/administrator-requests/{rid}/review', {'decision': 'rejected', 'note': '暂不需要'}).status_code == 200
    assert store.resolve(tokens['member-one'])['role'] == 'member'
    mine = request('member-one', 'GET', '/api/auth/administrator-requests').json()['items'][0]
    assert mine['status'] == 'rejected' and mine['reviewer'] == 'kendas'
    assert mine['review_note'] == '暂不需要'


def test_disabled_members_cannot_be_promoted_and_admins_cannot_review_self(site):
    _, store, _, admin, request = site
    rid = apply(request)
    assert request(admin, 'PUT', '/api/admin/users/member-one', {'role': 'member', 'active': False}).status_code == 200
    assert request(admin, 'POST', f'/api/admin/administrator-requests/{rid}/review', {'decision': 'approved'}).status_code == 409
    with store.connect() as db:
        db.execute("INSERT INTO administrator_requests(id,user_id,reason,status,created_at) VALUES(?,?,?,'pending',?)", ('self', admin, 'synthetic legacy request', int(time.time())))
    assert request(admin, 'POST', '/api/admin/administrator-requests/self/review', {'decision': 'approved'}).status_code == 403
    assert request(admin, 'PUT', '/api/admin/users/'+admin, {'role': 'member', 'active': False}).status_code == 400


def test_additional_admin_can_be_revoked_without_affecting_original(site):
    _, store, tokens, admin, request = site
    rid = apply(request)
    assert request(admin, 'POST', f'/api/admin/administrator-requests/{rid}/review', {'decision': 'approved'}).status_code == 200
    tokens['member-one'] = store.session('member-one')
    assert request(admin, 'PUT', '/api/admin/users/member-one', {'role': 'member', 'active': True}).status_code == 200
    assert request('member-one', 'GET', '/api/admin/users').status_code == 401
    assert store.resolve(tokens[admin])['role'] == 'admin'


@pytest.mark.parametrize('revoke', ['session', 'role'])
def test_review_rechecks_actor_after_an_earlier_authorization_snapshot(site, monkeypatch, revoke):
    _, store, tokens, admin, request = site
    rid = apply(request)
    snapshot = store.resolve(tokens[admin])
    resolve = store.resolve
    with store.connect() as db:
        if revoke == 'session':
            db.execute('DELETE FROM sessions WHERE user_id=?', (admin,))
        else:
            db.execute("UPDATE users SET role='member' WHERE id=?", (admin,))
    # Simulate a request that passed authentication immediately before another
    # transaction revoked its session/role. Approval must recheck under its lock.
    monkeypatch.setattr(store, 'resolve', lambda token: snapshot if token == tokens[admin] else resolve(token))
    response = request(admin, 'POST', f'/api/admin/administrator-requests/{rid}/review', {'decision': 'approved'})
    assert response.status_code == 403
    with store.connect() as db:
        assert db.execute("SELECT role FROM users WHERE id='member-one'").fetchone()[0] == 'member'
        assert db.execute('SELECT status FROM administrator_requests WHERE id=?', (rid,)).fetchone()[0] == 'pending'

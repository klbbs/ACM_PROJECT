"""Synthetic identity databases: free first-admin choice and safe upgrades."""
import time

import pytest
from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.settings import Settings
from acmweb.users import Users, csrf_token, digest, instance_lock

PASSWORD = 'Synthetic-owner-password-123'


def test_upgrade_preserves_all_existing_identities_roles_passwords_sessions_and_data(tmp_path):
    users = Users(tmp_path / 'users.sqlite3')
    original = users.create('admin', PASSWORD, setup=True)
    with users.connect() as db:
        old_hash = db.execute('SELECT password_hash FROM users WHERE id=?', (original,)).fetchone()[0]
        # These names exercise legacy migration triggers; none may be rewritten.
        for uid, name, role in [('legacy-member', 'kds', 'member'), ('other-owner', 'kendas', 'admin'),
                                ('approved-owner', 'another_owner', 'admin')]:
            db.execute('INSERT INTO users VALUES(?,?,?,?,1,?)', (uid, name, old_hash, role, int(time.time())))
        db.execute('INSERT INTO shared_entries VALUES(?,?,?,?,?,1,0,1,1)', ('entry', 'legacy-member', 'shared', 'body', 'shared'))
        db.execute('INSERT INTO invitations VALUES(?,?,?,?)', (digest('invite'), original, int(time.time()) + 3600, 'legacy-member'))
        db.execute('INSERT INTO social_profiles(user_id,display_name) VALUES(?,?)', (original, '自选旧昵称'))
        before = [tuple(row) for row in db.execute('SELECT * FROM users ORDER BY id')]
    tokens = {uid: users.session(uid) for uid in [original, 'legacy-member', 'other-owner', 'approved-owner']}
    workspace = tmp_path / 'users' / original
    workspace.mkdir(parents=True)
    (workspace / 'private.txt').write_text('synthetic private data')
    for _ in range(2):
        restarted = Users(users.path)
        with instance_lock(tmp_path / '.multiuser.lock'):
            restarted.normalize_administrator()
        with restarted.connect() as db:
            assert [tuple(row) for row in db.execute('SELECT * FROM users ORDER BY id')] == before
            assert db.execute('SELECT owner_id FROM shared_entries').fetchone()[0] == 'legacy-member'
            assert db.execute('SELECT used_by FROM invitations').fetchone()[0] == 'legacy-member'
            assert db.execute('SELECT display_name FROM social_profiles WHERE user_id=?', (original,)).fetchone()[0] == '自选旧昵称'
            assert not db.execute('PRAGMA foreign_key_check').fetchall()
        for uid, token in tokens.items():
            assert restarted.resolve(token)['id'] == uid
    assert restarted.authenticate('admin', PASSWORD)['id'] == original
    assert (workspace / 'private.txt').read_text() == 'synthetic private data'
    assert not users.path.with_name('users.before-canonical-admin.sqlite3').exists()


@pytest.mark.parametrize('username', ['site_owner', 'admin', 'kendas', 'kds'])
def test_any_preexisting_administrator_name_survives_restart(tmp_path, username):
    users = Users(tmp_path / 'users.sqlite3')
    uid = users.create(username, PASSWORD, setup=True)
    token = users.session(uid)
    with instance_lock(tmp_path / '.multiuser.lock'):
        Users(users.path).normalize_administrator()
    assert users.resolve(token)['username'] == username
    with users.connect() as db:
        assert db.execute('SELECT count(*) FROM users').fetchone()[0] == 1
        assert db.execute('SELECT role FROM users WHERE id=?', (uid,)).fetchone()[0] == 'admin'


def test_second_server_requires_existing_instance_lock_and_never_changes_users(tmp_path):
    users = Users(tmp_path / 'users.sqlite3')
    original = users.create('custom_owner', PASSWORD, setup=True)
    settings = Settings(data_dir=tmp_path, multi_user=True, enable_codex=False,
                        access_token='synthetic-initialization-secret-long-enough')
    with instance_lock(tmp_path / '.multiuser.lock'):
        with pytest.raises(RuntimeError, match='正在运行'):
            with TestClient(create_app(settings)):
                pass
    with users.connect() as db:
        assert db.execute('SELECT username FROM users WHERE id=?', (original,)).fetchone()[0] == 'custom_owner'
    assert not users.path.with_name('users.before-canonical-admin.sqlite3').exists()


def test_first_deployer_chooses_username_nickname_and_password_once(tmp_path):
    settings = Settings(data_dir=tmp_path, multi_user=True, enable_codex=False,
                        access_token='synthetic-initialization-secret-long-enough')
    app = create_app(settings)
    with TestClient(app) as client:
        headers = {'X-ACM-Request': '1'}
        body = {'username': 'MyOwner', 'display_name': '自选昵称', 'password': 'short', 'setup_token': settings.access_token}
        assert client.post('/api/auth/setup', headers=headers, json=body).status_code == 422
        assert app.state.identities.count() == 0
        body['password'] = PASSWORD
        assert client.post('/api/auth/setup', headers=headers, json={**body, 'setup_token': 'wrong'}).status_code == 403
        assert client.post('/api/auth/setup', headers=headers, json=body).status_code == 200
        status = client.get('/api/auth/status').json()
        assert status['user']['username'] == 'myowner' and status['user']['role'] == 'admin'
        me = client.get('/api/social/me').json()['profile']
        assert me['display_name'] == '自选昵称'
        assert not me['public_profile'] and not me['leaderboard']
        assert client.post('/api/auth/setup', headers=headers, json={**body, 'username': 'replacement'}).status_code == 409
        assert app.state.identities.count() == 1
        # The setup token cannot replace an account's chosen password.
        assert client.post('/api/auth/login', headers=headers,
            json={'username': 'myowner', 'password': settings.access_token}).status_code == 401
        assert client.post('/api/auth/login', headers=headers,
            json={'username': 'myowner', 'password': PASSWORD}).status_code == 200
        token = client.cookies['acm_user_session']
        headers['X-CSRF-Token'] = csrf_token(token)
        invite = client.post('/api/admin/invitations', headers=headers, json={}).json()['invitation']
        registered = client.post('/api/auth/register', headers=headers,
            json={'username': 'new_member', 'password': PASSWORD, 'invitation': invite})
        assert registered.status_code == 200
        member = client.get('/api/auth/status').json()['user']
        headers['Cookie'] = 'acm_user_session=' + token
        assert client.put('/api/admin/users/' + member['id'], headers=headers,
                          json={'role': 'admin', 'active': True}).status_code == 409


def test_setup_validates_nickname_before_creating_identity_and_allows_omission(tmp_path):
    settings = Settings(data_dir=tmp_path, multi_user=True, enable_codex=False,
                        access_token='synthetic-initialization-secret-long-enough')
    app = create_app(settings)
    with TestClient(app) as client:
        headers = {'X-ACM-Request': '1'}
        body = {'username': 'owner_one', 'password': PASSWORD, 'setup_token': settings.access_token}
        assert client.post('/api/auth/setup', headers=headers, json={**body, 'display_name': '名' * 41}).status_code == 422
        assert app.state.identities.count() == 0
        assert client.post('/api/auth/setup', headers=headers, json=body).status_code == 200
        assert client.get('/api/social/me').json()['profile']['display_name'] == 'owner_one'


def test_old_migration_markers_cannot_rewrite_later_users_or_roles(tmp_path):
    users = Users(tmp_path / 'users.sqlite3')
    uid = users.create('custom_owner', PASSWORD, setup=True)
    with users.connect() as db:
        db.execute("INSERT INTO identity_migrations VALUES('canonical-admin-v1',?)", (int(time.time()),))
        db.execute("UPDATE users SET active=0 WHERE id=?", (uid,))
    users.normalize_administrator()
    with users.connect() as db:
        assert tuple(db.execute('SELECT username,role,active FROM users WHERE id=?', (uid,)).fetchone()) == ('custom_owner', 'admin', 0)

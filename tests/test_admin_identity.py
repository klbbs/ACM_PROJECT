"""Migration keeps the real administrator, not the deprecated demo identity."""
import sqlite3
import time

from acmweb.users import Users, digest, instance_lock
import pytest


def test_original_admin_retains_identity_password_and_data_when_demo_removed(tmp_path):
    users = Users(tmp_path / 'users.sqlite3')
    original = users.create('admin', 'Original-password-123', setup=True)
    with users.connect() as db:
        old_hash = db.execute('SELECT password_hash FROM users WHERE id=?', (original,)).fetchone()[0]
        for uid, name in [('demo', 'kds'), ('previous-demo', 'kendas')]:
            db.execute('INSERT INTO users VALUES(?,?,?,?,1,?)', (uid, name, old_hash, 'admin', int(time.time())))
        db.execute('INSERT INTO shared_entries VALUES(?,?,?,?,?,1,0,1,1)', ('entry', 'demo', 'shared', 'body', 'shared'))
        db.execute('INSERT INTO invitations VALUES(?,?,?,?)', (digest('invite'), original, int(time.time())+3600, 'demo'))
    token = users.session(original)
    demo_token = users.session('demo')
    workspace = tmp_path / 'users' / original
    workspace.mkdir(parents=True)
    (workspace / 'private.txt').write_text('private data')
    migrated = Users(users.path)
    with instance_lock(tmp_path / ".multiuser.lock"):
        migrated.normalize_administrator()
    with migrated.connect() as db:
        rows = db.execute('SELECT id,username,role,password_hash FROM users').fetchall()
        assert len(rows) == 1
        assert tuple(rows[0]) == (original, 'kendas', 'admin', old_hash)
        assert db.execute('SELECT owner_id FROM shared_entries').fetchone()[0] == original
        assert db.execute('SELECT used_by FROM invitations').fetchone()[0] is None
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    assert migrated.authenticate('kendas', 'Original-password-123')['id'] == original
    assert migrated.resolve(token) is None and migrated.resolve(demo_token) is None
    assert (workspace / 'private.txt').read_text() == 'private data'
    backup = users.path.with_name('users.before-canonical-admin.sqlite3')
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT username FROM users WHERE id=?", (original,)).fetchone()[0] == 'admin'
    # Repeat startups do not duplicate migration events or recreate demo accounts.
    with migrated.connect() as db:
        count = db.execute('SELECT COUNT(*) FROM audit').fetchone()[0]
    with instance_lock(tmp_path / ".multiuser.lock"):
        Users(users.path).normalize_administrator()
    with migrated.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM audit').fetchone()[0] == count


def test_noncanonical_demo_is_never_promoted_to_original_admin(tmp_path):
    users = Users(tmp_path / 'users.sqlite3')
    uid = users.create('kds', 'Old-demo-password', setup=True)
    with instance_lock(tmp_path / ".multiuser.lock"), pytest.raises(RuntimeError, match="演示账号"):
        Users(users.path).normalize_administrator()
    with users.connect() as db:
        assert db.execute('SELECT username FROM users WHERE id=?', (uid,)).fetchone()[0] == 'kds'


def test_second_server_cannot_migrate_before_instance_lock(tmp_path):
    from fastapi.testclient import TestClient
    from acmweb.app import create_app
    from acmweb.settings import Settings
    users = Users(tmp_path / 'users.sqlite3')
    original = users.create('admin', 'Original-password-123', setup=True)
    settings = Settings(data_dir=tmp_path, multi_user=True, enable_codex=False,
                        access_token='initialization-secret-long-enough')
    with instance_lock(tmp_path / '.multiuser.lock'):
        app = create_app(settings)
        with pytest.raises(RuntimeError, match='正在运行'):
            with TestClient(app):
                pass
    with users.connect() as db:
        assert db.execute('SELECT username FROM users WHERE id=?', (original,)).fetchone()[0] == 'admin'
    assert not users.path.with_name('users.before-canonical-admin.sqlite3').exists()


def test_setup_has_no_demo_password_and_admin_cannot_be_duplicated(tmp_path):
    from fastapi.testclient import TestClient
    from acmweb.app import create_app
    from acmweb.settings import Settings
    from acmweb.users import csrf_token
    settings = Settings(data_dir=tmp_path, multi_user=True, enable_codex=False,
                        access_token='initialization-secret-long-enough')
    with TestClient(create_app(settings)) as client:
        header = {'X-ACM-Request': '1'}
        body = {'username': 'kendas', 'password': 'kds', 'setup_token': settings.access_token}
        assert client.post('/api/auth/setup', headers=header, json=body).status_code == 422
        body['password'] = 'Original-password-123'
        assert client.post('/api/auth/setup', headers=header, json=body).status_code == 200
        token = client.cookies['acm_user_session']
        header['X-CSRF-Token'] = csrf_token(token)
        invite = client.post('/api/admin/invitations', headers=header, json={}).json()['invitation']
        reserved = {'username': 'kds', 'password': 'Original-password-123', 'invitation': invite}
        assert client.post('/api/auth/register', headers=header, json=reserved).status_code == 409
        reserved['username'] = 'member'
        registered = client.post('/api/auth/register', headers=header, json=reserved)
        assert registered.status_code == 200
        member = client.get('/api/auth/status').json()['user']
        header['Cookie'] = 'acm_user_session=' + token
        assert client.put('/api/admin/users/'+member['id'], headers=header,
                          json={'role': 'admin', 'active': True}).status_code == 409

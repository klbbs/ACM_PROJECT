"""Disposable development checks; these are not version acceptance."""
import pytest
from fastapi.testclient import TestClient
from acmweb.app import create_app
from acmweb.database import Database
from acmweb.settings import Settings
from acmweb.users import csrf_token


def item(**changes):
    return {'id': 'template-001', 'title': '树状数组', 'trigger': '树状数组', 'language': 'cpp', 'body': 'int ${name};\n${}', **changes}


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, enable_codex=False))
    with TestClient(app) as client:
        yield client, app


def test_template_crud_revision_restart_and_inert_body(client):
    client, app = client
    assert client.get('/api/workbench/code-templates').json() == {'revision': 0, 'items': []}
    body = {'revision': 0, 'items': [item(body='print("not executed")\n${name}') ]}
    saved = client.put('/api/workbench/code-templates', json=body)
    assert saved.status_code == 200 and saved.json()['revision'] == 1
    assert Database(app.state.database.path).get('editor_code_templates') == saved.json()
    assert client.put('/api/workbench/code-templates', json=body).status_code == 409
    edit = {'revision': 1, 'items': [item(title='修改标题'), item(id='template-002', trigger='main', language='python3')]}
    assert client.put('/api/workbench/code-templates', json=edit).json()['revision'] == 2
    assert client.put('/api/workbench/code-templates', json={'revision': 2, 'items': []}).json() == {'revision': 3, 'items': []}


@pytest.mark.parametrize('changes', [
    {'trigger': 'has space'}, {'trigger': '9start'}, {'trigger': 'x' * 41},
    {'title': ' '}, {'body': ' '}, {'body': 'a' * 8193}, {'body': '字' * 6000},
    {'language': 'bash'}, {'id': '../outside'}, {'untrusted_user_id': 'other'},
])
def test_template_rejects_bad_input_without_writes(client, changes):
    client, _ = client
    assert client.put('/api/workbench/code-templates', json={'revision': 0, 'items': [item(**changes)]}).status_code == 422
    assert client.get('/api/workbench/code-templates').json()['revision'] == 0


def test_template_overlap_count_and_total_limits(client):
    client, _ = client
    first = item(language='cpp')
    overlap = item(id='template-002', language='cpp20')
    assert client.put('/api/workbench/code-templates', json={'revision': 0, 'items': [first, overlap]}).status_code == 400
    separated = item(id='template-002', language='python3')
    assert client.put('/api/workbench/code-templates', json={'revision': 0, 'items': [first, separated]}).status_code == 200
    items = [item(id=f'template-{n:03}', trigger=f'template_{n}') for n in range(41)]
    assert client.put('/api/workbench/code-templates', json={'revision': 1, 'items': items}).status_code == 422
    items = [item(id=f'template-{n:03}', trigger=f'template_{n}', body='a' * 8192) for n in range(20)]
    assert client.put('/api/workbench/code-templates', json={'revision': 1, 'items': items}).status_code == 400


def test_private_templates_require_session_csrf_and_do_not_cross_users(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, multi_user=True, enable_codex=False, access_token='setup-token-development-only'))
    users = app.state.identities
    first = users.create('kendas', 'Development-password-123', setup=True)
    # Use an existing admin-generated hash for a synthetic second identity.
    with users.connect() as conn:
        hashed = conn.execute('SELECT password_hash FROM users WHERE id=?', (first,)).fetchone()[0]
        conn.execute('INSERT INTO users VALUES(?,?,?,?,1,0)', ('second-user', 'member_two', hashed, 'member'))
    tokens = {user: users.session(user) for user in [first, 'second-user']}
    def headers(user, csrf=True):
        token = tokens[user]
        return {'Cookie': 'acm_user_session=' + token, 'X-ACM-Request': '1', **({'X-CSRF-Token': csrf_token(token)} if csrf else {})}
    with TestClient(app) as client:
        assert client.get('/api/workbench/code-templates').status_code == 401
        payload = {'revision': 0, 'items': [item()]}
        assert client.put('/api/workbench/code-templates', json=payload, headers=headers(first, False)).status_code == 403
        assert client.put('/api/workbench/code-templates', json=payload, headers=headers(first)).status_code == 200
        assert client.get('/api/workbench/code-templates', headers=headers('second-user')).json() == {'revision': 0, 'items': []}
        assert client.get('/api/workbench/code-templates', headers=headers(first)).json()['items'][0]['title'] == '树状数组'


def test_template_edits_do_not_consume_ai_operation_budget(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, multi_user=True, enable_codex=False,
                            access_token='setup-token-development-only'))
    users = app.state.identities
    uid = users.create('kendas', 'Development-password-123', setup=True)
    token = users.session(uid)
    headers = {'Cookie': 'acm_user_session=' + token, 'X-ACM-Request': '1',
               'X-CSRF-Token': csrf_token(token)}
    with TestClient(app) as client:
        for revision in range(8):
            result = client.put('/api/workbench/code-templates', headers=headers,
                                json={'revision': revision, 'items': [item(title=f'模板{revision}')]})
            assert result.status_code == 200
        with users.connect() as conn:
            assert conn.execute('SELECT count FROM rate_limits WHERE key=?',
                                ('cost:' + uid,)).fetchone() is None

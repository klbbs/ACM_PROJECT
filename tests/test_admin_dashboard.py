import json
import sqlite3
import time

from fastapi.testclient import TestClient
import pytest

from acmweb.app import create_app
from acmweb.settings import Settings
from acmweb.translations import fingerprint
from acmweb.users import csrf_token
from acmweb.workbench import parse_statement


HTML = '''<div class="problem-statement"><div class="header"><div class="title">A. Translate me</div></div>
<div><p>Find the value of x plus one.</p></div>
<div class="sample-tests"><div class="sample-test"><div class="input"><pre>1</pre></div><div class="output"><pre>2</pre></div></div></div></div>'''


def test_admin_translation_moderation_and_initial_credentials(tmp_path):
    settings = Settings(data_dir=tmp_path, multi_user=True, enable_codex=False,
                        access_token='initialization-secret-long-enough', allowed_hosts=('testserver',))
    app = create_app(settings)
    with TestClient(app, base_url='https://testserver') as client:
        def request(who, method, path, body=None, extra=None):
            headers = {'X-ACM-Request': '1', 'Origin': 'https://testserver'}
            if who:
                headers.update({'Cookie': 'acm_user_session=' + who['cookie'], 'X-CSRF-Token': who['csrf_token']})
            else:
                headers['Cookie'] = ''
            headers.update(extra or {})
            return client.request(method, path, headers=headers, **({'json': body} if body is not None else {}))

        def login(username, password):
            response = request(None, 'POST', '/api/auth/login', {'username': username, 'password': password})
            assert response.status_code == 200, response.text
            cookie = response.cookies['acm_user_session']
            return {'cookie': cookie, 'csrf_token': csrf_token(cookie), 'user': app.state.identities.resolve(cookie)}

        setup = client.post('/api/auth/setup', headers={'X-ACM-Request': '1'}, json={
            'username': 'kendas', 'password': 'Correct-password-123', 'setup_token': settings.access_token,
        })
        assert setup.status_code == 200, setup.text
        admin = login('kendas', 'Correct-password-123')
        invite = request(admin, 'POST', '/api/admin/invitations', {}).json()['invitation']
        registration = request(None, 'POST', '/api/auth/register', {
            'username': 'member', 'password': 'Correct-password-123', 'invitation': invite,
        })
        assert registration.status_code == 200, registration.text
        member = login('member', 'Correct-password-123')

        statement = parse_statement(HTML, 'codeforces:4-A')
        statement['source'] = 'official'
        assert request(admin, 'GET', '/api/workbench/problem/4-A').status_code == 200
        assert request(member, 'GET', '/api/workbench/problem/4-A').status_code == 200
        owner_child = app.state.workspaces.apps[admin['user']['id']][0]
        member_child = app.state.workspaces.apps[member['user']['id']][0]
        owner_child.state.workbench.store_statement('codeforces:4-A', statement)
        member_child.state.workbench.store_statement('codeforces:4-A', statement)
        store = app.state.translation_store

        def insert_translation(tid, status):
            translated = {**statement, 'title': 'A. 翻译题目', 'sections': ['翻译后的题面。']}
            now = int(time.time())
            with store.connect() as db:
                db.execute('INSERT INTO translations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                    tid, 'owner-id', 'private-' + tid, 'codeforces:4-A', fingerprint(statement), 'zh-CN',
                    'model-' + tid, status, json.dumps({'statement': translated, 'problem_id': 'codeforces:4-A',
                    'engine': 'ai', 'model': 'test-model', 'provenance': {'vendor': 'test', 'model': 'test-model'}}),
                    json.dumps(statement), now, now if status != 'pending' else None,
                    'admin-id' if status != 'pending' else None, '',
                ))
            return tid

        approved_id = insert_translation('approved-id', 'pending')
        rejected_id = insert_translation('rejected-id', 'pending')
        assert {item['id'] for item in request(admin, 'GET', '/api/translations/review?status=pending').json()['items']} >= {approved_id, rejected_id}
        assert request(None, 'GET', '/api/translations/review').status_code == 401
        assert request(member, 'GET', '/api/translations/review').status_code == 403
        assert request(member, 'GET', '/api/translations/audit').status_code == 403
        assert request(member, 'POST', f'/api/translations/{approved_id}/review', {'decision': 'approved'}).status_code == 403
        assert request(member, 'POST', f'/api/translations/{approved_id}/revoke', {'note': 'issue'}).status_code == 403
        assert request(admin, 'POST', f'/api/translations/{approved_id}/review', {'decision': 'approved'}).status_code == 200
        assert request(admin, 'POST', f'/api/translations/{approved_id}/review', {'decision': 'approved'}).status_code == 409
        assert request(admin, 'POST', f'/api/translations/{rejected_id}/review', {'decision': 'rejected', 'note': '公式不符'}).status_code == 200
        visible = request(member, 'GET', '/api/workbench/problem/4-A/translations').json()['items']
        assert any(item.get('id') == approved_id for item in visible)
        assert not any(item.get('id') == rejected_id for item in visible)

        assert request(admin, 'POST', f'/api/translations/{approved_id}/revoke', {'note': ''}).status_code == 422
        assert request(admin, 'POST', f'/api/translations/{approved_id}/revoke', {'note': '译文内容错误'},
                       extra={'X-CSRF-Token': 'invalid'}).status_code == 403
        assert request(admin, 'POST', f'/api/translations/{approved_id}/revoke', {'note': '译文内容错误'}).status_code == 200
        assert request(admin, 'POST', f'/api/translations/{approved_id}/revoke', {'note': '重复撤下'}).status_code == 409
        assert request(admin, 'GET', '/api/translations/review?status=revoked').json()['items'][0]['id'] == approved_id
        visible = request(member, 'GET', '/api/workbench/problem/4-A/translations').json()['items']
        assert not any(item.get('id') == approved_id for item in visible)
        assert not any(item.get('id') == rejected_id for item in visible)
        with store.connect() as db:
            row = db.execute('SELECT * FROM translations WHERE id=?', (approved_id,)).fetchone()
            with pytest.raises(sqlite3.IntegrityError):
                db.execute('INSERT INTO translations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                    'duplicate-approved', 'new-owner', 'new-private', row['problem_id'], row['fingerprint'],
                    row['target'], row['model_key'], 'pending', row['data'], row['source_statement'],
                    int(time.time()), None, None, '',
                ))
        actions = [event['action'] for event in request(admin, 'GET', '/api/translations/audit').json()['items']]
        assert {'approved', 'rejected', 'revoked'} <= set(actions)

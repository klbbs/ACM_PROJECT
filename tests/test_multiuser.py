import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.settings import Settings
from acmweb.users import digest
from ojcrawler.storage import Store
from ojcrawler.models import Collection, Submission

PASSWORD = 'Correct-password-123'


@pytest.fixture
def site(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','GLOBAL-SECRET-MUST-NOT-LEAK')
    monkeypatch.setattr('acmweb.jobs.Jobs.crawl',lambda self: {'complete':True,'run_id':'test','accounts':[]})
    settings = Settings(data_dir=tmp_path,config_path=tmp_path/'legacy.toml',multi_user=True,enable_codex=False,
                        access_token='initialization-secret-long-enough',allowed_hosts=('testserver',))
    settings.config_path.write_text('[[accounts]]\nplatform="luogu"\nhandle="123"\ncookie="legacy=PRIVATE"\n')
    app = create_app(settings)
    with TestClient(app,base_url='https://testserver') as client:
        def request(who,method,path,body=None,extra=None):
            headers = {'X-ACM-Request':'1','Origin':'https://testserver'}
            if who:
                headers.update({'Cookie':'acm_user_session='+who['cookie'],'X-CSRF-Token':who['csrf_token']})
            else:
                headers['Cookie']=''
            headers.update(extra or {})
            return client.request(method,path,headers=headers,**({'json':body} if body is not None else {}))
        def capture(response):
            assert response.status_code == 200,response.text
            cookie = response.cookies['acm_user_session']
            status = client.get('/api/auth/status',headers={'Cookie':'acm_user_session='+cookie}).json()
            return {**status,'cookie':cookie}
        admin = capture(request(None,'POST','/api/auth/setup',{'username':'kendas','password':PASSWORD,'setup_token':settings.access_token}))
        invite = request(admin,'POST','/api/admin/invitations',{}).json()['invitation']
        member = capture(request(None,'POST','/api/auth/register',{'username':'member','password':PASSWORD,'invitation':invite}))
        yield request,admin,member,app,settings,client


def test_private_workspace_isolation_and_no_global_credentials(site):
    request,admin,member,app,settings,_ = site
    for who in (admin,member):
        assert request(who,'GET','/api/accounts').json()['items'] == []
        assert request(who,'GET','/api/ai/settings').json()['has_key'] is False
    result = request(admin,'PUT','/api/accounts',{'items':[{'platform':'codeforces','handle':'owner_cf','cookie':'session=OWNER-ONLY'}]})
    assert result.status_code == 200,result.text
    assert 'OWNER-ONLY' not in result.text
    assert request(member,'GET','/api/accounts').json()['items'] == []
    assert request(admin,'GET','/api/accounts').json()['items'][0]['handle'] == 'owner_cf'
    assert request(admin,'PUT','/api/ai/settings',{'api_key':'sk-owner-only'}).status_code == 200
    assert not request(member,'GET','/api/ai/settings').json()['has_key']
    assert request(member,'PUT','/api/ai/settings',{'base_url':'https://attacker.example/v1'}).status_code == 400
    runtimes = app.state.workspaces.apps
    own_db = runtimes[admin['user']['id']][0].state.database
    with own_db.connect() as db:
        db.execute("INSERT INTO ai_reports VALUES('private-report',1,'analysis','api','test',?)",(json.dumps({'title':'Private title','summary':'only owner'}),))
    assert request(admin,'GET','/api/reports/private-report').status_code == 200
    assert request(member,'GET','/api/reports/private-report').status_code == 404
    assert request(member,'POST','/api/shared/report/private-report',{}).status_code == 404
    assert request(admin,'POST','/api/shared/report/private-report',{}).status_code == 200
    assert request(member,'GET','/api/shared').json()['items'][0]['body'] == 'only owner'
    assert own_db.path.parent != runtimes[member['user']['id']][0].state.database.path.parent
    store = Store(own_db.path)
    store.save('owner-run',int(time.time()),Collection('codeforces','owner_cf',submissions=[Submission('owner-sub','1-A',int(time.time()),'AC')],complete=True))
    store.close()
    assert request(admin,'GET','/api/submissions').json()['total'] == 1
    assert request(member,'GET','/api/submissions').json()['total'] == 0
    assert request(member,'GET',f"/api/reports?user_id={admin['user']['id']}").json()['items'] == []
    assert request(admin,'PUT','/api/daily/settings',{'daily_goal':3,'duration_days':14}).json()['settings']['daily_goal'] == 3
    assert request(member,'GET','/api/daily').json()['settings']['daily_goal'] == 1
    assert 'legacy=PRIVATE' in settings.config_path.read_text()


def test_shared_acl_revisions_and_recycle_bin(site):
    request,admin,member,*_ = site
    payload = {'title':'Private draft','body':'<script>alert(1)</script>','visibility':'private'}
    created = request(admin,'POST','/api/shared',payload).json()
    eid = created['id']
    assert request(member,'GET','/api/shared').json()['total'] == 0
    assert request(member,'PUT',f'/api/shared/{eid}',payload).status_code == 404
    assert request(member,'DELETE',f'/api/shared/{eid}',{}).status_code == 404
    payload.update(visibility='shared',version=1)
    assert request(admin,'PUT',f'/api/shared/{eid}',payload).json()['version'] == 2
    assert request(admin,'PUT',f'/api/shared/{eid}',payload).status_code == 409
    assert request(member,'GET','/api/shared').json()['total'] == 1
    assert request(member,'PUT',f'/api/shared/{eid}',payload).status_code == 403
    assert request(admin,'DELETE',f'/api/shared/{eid}',{}).status_code == 200
    assert request(member,'GET','/api/shared').json()['total'] == 0
    assert request(admin,'GET','/api/shared?trashed=true').json()['total'] == 1
    assert request(member,'POST',f'/api/shared/{eid}/restore',{}).status_code == 404
    assert request(admin,'POST',f'/api/shared/{eid}/restore',{}).status_code == 200


def test_admin_actions_revoke_sessions_and_keep_private_drafts(site):
    request,admin,member,app,*_ = site
    assert request(member,'GET','/api/admin/users').status_code == 403
    assert request(member,'POST','/api/admin/invitations',{}).status_code == 403
    assert request(admin,'PUT',f"/api/admin/users/{admin['user']['id']}",{'role':'member','active':False}).status_code == 400
    eid = request(member,'POST','/api/shared',{'title':'Member private','body':'draft'}).json()['id']
    assert request(admin,'GET','/api/shared').json()['total'] == 0
    assert request(admin,'DELETE',f'/api/shared/{eid}',{}).status_code == 404
    assert request(admin,'PUT',f"/api/admin/users/{member['user']['id']}",{'role':'member','active':False}).status_code == 200
    assert request(member,'GET','/api/accounts').status_code == 401
    assert request(None,'POST','/api/auth/login',{'username':'member','password':PASSWORD}).status_code == 401
    assert request(admin,'GET','/api/admin/audit').json()['items']


def test_cookie_csrf_host_origin_and_setup_guards(site):
    request,admin,member,app,settings,client = site
    assert request(None,'GET','/api/accounts').status_code == 401
    assert request(None,'GET','/api/accounts',extra={'Authorization':'Bearer '+settings.access_token}).status_code == 401
    assert request(admin,'PUT','/api/training',{},extra={'X-CSRF-Token':'wrong'}).status_code == 403
    assert request(admin,'POST','/api/auth/logout',{},extra={'Origin':'https://evil.example'}).status_code == 403
    assert request(admin,'POST','/api/auth/logout',{},extra={'Sec-Fetch-Site':'cross-site'}).status_code == 403
    assert request(admin,'GET','/api/accounts',extra={'Host':'evil.example'}).status_code == 400
    assert request(None,'POST','/api/auth/setup',{'username':'kendas','password':PASSWORD,'setup_token':settings.access_token}).status_code == 409
    response = request(None,'POST','/api/auth/login',{'username':'KENDAS','password':PASSWORD})
    assert response.status_code == 200
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
    assert request(admin,'POST','/api/shared',{'title':'x','body':'x'*530000}).status_code == 413
    result = request(None,'POST','/api/auth/login',{'username':'owner','password':'PRIVATE'})
    assert result.status_code == 401 and 'PRIVATE' not in result.text


def test_sessions_logout_and_password_change(site):
    request,admin,member,app,settings,client = site
    assert request(member,'POST','/api/auth/password',{'current_password':PASSWORD,'new_password':'New-correct-password-456'}).status_code == 200
    assert request(member,'GET','/api/accounts').status_code == 401
    assert request(None,'POST','/api/auth/login',{'username':'member','password':PASSWORD}).status_code == 401
    result = request(None,'POST','/api/auth/login',{'username':'member','password':'New-correct-password-456'})
    assert result.status_code == 200
    token = result.cookies['acm_user_session']
    current = client.get('/api/auth/status',headers={'Cookie':'acm_user_session='+token}).json()
    current['cookie']=token
    assert request(current,'POST','/api/auth/logout',{}).status_code == 200
    assert request(current,'GET','/api/overview').status_code == 401
    with app.state.identities.connect() as db:
        rows = db.execute('SELECT password_hash FROM users').fetchall()
        assert all(row[0].startswith('scrypt$131072$8$1$') and PASSWORD not in row[0] for row in rows)
        assert len({row[0] for row in rows}) == len(rows)
        assert not db.execute('SELECT 1 FROM sessions WHERE hash=?',(token,)).fetchone()


def test_invitation_single_use_expiry_and_limits(site):
    request,admin,member,app,*_ = site
    invite = request(admin,'POST','/api/admin/invitations',{}).json()['invitation']
    payload = {'username':'third','password':PASSWORD,'invitation':invite}
    assert request(None,'POST','/api/auth/register',payload).status_code == 200
    payload['username']='fourth'
    assert request(None,'POST','/api/auth/register',payload).status_code == 400
    another = request(admin,'POST','/api/admin/invitations',{}).json()['invitation']
    with app.state.identities.connect() as db:
        db.execute('UPDATE invitations SET expires_at=0 WHERE hash=?',(digest(another),))
    payload['invitation']=another
    assert request(None,'POST','/api/auth/register',payload).status_code == 400
    with app.state.identities.connect() as db:
        db.execute('INSERT INTO rate_limits VALUES(?,?,?,?)',('login-name:'+digest('blocked'),int(time.time())//900,10,int(time.time())+900))
    response = request(None,'POST','/api/auth/login',{'username':'blocked','password':PASSWORD})
    # Legacy username-only counters no longer let someone lock another peer out.
    assert response.status_code == 401


def test_session_expiration_logout_all_and_missing_csrf(site):
    request,admin,member,app,settings,client = site
    assert request(member,'POST','/api/shared',{'title':'X','body':'Y'},extra={'X-CSRF-Token':''}).status_code == 403
    assert request(member,'POST','/api/shared',{'title':'X','body':'Y'},extra={'X-ACM-Request':''}).status_code == 403
    assert request(admin,'POST','/api/auth/logout-all',{}).status_code == 200
    assert request(admin,'GET','/api/accounts').status_code == 401
    assert request(member,'GET','/api/accounts').status_code == 200
    with app.state.identities.connect() as db:
        db.execute('UPDATE sessions SET expires_at=0 WHERE user_id=?',(member['user']['id'],))
    assert request(member,'GET','/api/accounts').status_code == 401


def test_single_serving_process_and_explicit_legacy_import(site):
    import importlib.util
    request,admin,member,app,settings,_ = site
    with pytest.raises(RuntimeError,match='正在运行'):
        with TestClient(create_app(settings)):
            pass
    root = settings.data_dir
    store = Store(root/'accounts.sqlite3')
    store.save('legacy',1,Collection('luogu','123',submissions=[Submission('legacy-sub','P1001',1,'AC')],complete=True))
    store.close()
    spec = importlib.util.spec_from_file_location('manage_users',Path(__file__).resolve().parents[1]/'scripts/manage_users.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError):
        module.import_legacy(root,member['user'],settings.config_path)
    module.import_legacy(root,admin['user'],settings.config_path)
    assert request(admin,'GET','/api/submissions').json()['total'] == 1
    assert request(member,'GET','/api/submissions').json()['total'] == 0
    assert request(admin,'GET','/api/accounts').json()['items'][0]['has_cookie']
    with pytest.raises(ValueError):
        module.import_legacy(root,admin['user'],settings.config_path)


def test_production_secure_cookie_and_security_headers(tmp_path):
    settings = Settings(data_dir=tmp_path,multi_user=True,production=True,
                        access_token='production-setup-secret-long-enough',allowed_hosts=('acm.example.test',))
    with TestClient(create_app(settings),base_url='https://acm.example.test') as client:
        bad = client.post('/api/auth/setup',headers={'X-ACM-Request':'1'},json={'username':'kendas','password':PASSWORD,'setup_token':'错误初始化令牌'})
        assert bad.status_code == 403
        result = client.post('/api/auth/setup',headers={'X-ACM-Request':'1'},json={'username':'kendas','password':PASSWORD,'setup_token':settings.access_token})
        assert result.status_code == 200
        assert 'Secure' in result.headers['set-cookie']
        assert 'HttpOnly' in result.headers['set-cookie']
        assert result.headers['strict-transport-security'] == 'max-age=31536000'
        assert "form-action 'self'" in result.headers['content-security-policy']
        assert result.headers['cache-control'] == 'no-store'


def test_canonical_origin_behind_https_proxy(tmp_path):
    settings = Settings(data_dir=tmp_path,multi_user=True,production=True,
                        access_token='production-setup-secret-long-enough',allowed_hosts=('acm.example.test',),
                        public_origin='https://acm.example.test')
    with TestClient(create_app(settings),base_url='http://acm.example.test') as client:
        result = client.post('/api/auth/setup',headers={'X-ACM-Request':'1','Origin':'https://acm.example.test'},json={'username':'kendas','password':PASSWORD,'setup_token':settings.access_token})
        assert result.status_code == 200
        cookie = result.cookies['acm_user_session']
        headers = {'Cookie':'acm_user_session='+cookie,'X-ACM-Request':'1','Origin':'https://acm.example.test'}
        status = client.get('/api/auth/status',headers=headers).json()
        headers['X-CSRF-Token']=status['csrf_token']
        assert client.post('/api/shared',headers=headers,json={'title':'Proxy works','body':'HTTPS terminates at proxy'}).status_code == 200
        headers['Origin']='https://other.example.test'
        assert client.post('/api/shared',headers=headers,json={'title':'x','body':'x'}).status_code == 403


def test_workbench_user_isolation_and_csrf(site):
    request,admin,member,app,*_ = site
    path = '/api/workbench/problem/4-A/draft'
    draft = {'code':'OWNER-PRIVATE-CODE','language':'cpp17','revision':0}
    assert request(admin,'PUT',path,draft).status_code == 200
    assert request(member,'GET','/api/workbench/problem/4-A').json()['draft']['code'] == ''
    assert request(member,'PUT',path,draft,{'X-CSRF-Token':'wrong'}).status_code == 403
    saved = {**draft,'revision':1}
    service = app.state.workspaces.apps[admin['user']['id']][0].state.workbench
    aid = service.snapshot('4-A', 1)['id']
    assert request(member,'GET',f'/api/workbench/attempt/{aid}').status_code == 404
    assert request(member,'GET','/api/workbench/problem/4-A/history').json()['items'] == []
    assert request(member,'POST',f'/api/workbench/attempt/{aid}/sync',{'handle':'demo','submission_id':1}).status_code == 404
    assert request(member,'POST',f'/api/workbench/attempt/{aid}/review',{'provider':'api','model':'mock'}).status_code == 404
    assert request(member,'GET','/api/workbench').json()['profile']['local_snapshots'] == 0
    assert request(admin,'GET','/api/workbench').json()['profile']['local_snapshots'] == 1


def test_editor_preferences_isolated_csrf_and_not_charged_as_ai(site):
    request, admin, member, *_ = site
    path = '/api/workbench/editor-settings'
    settings = {'keymap':'vim','font_size':20,'line_wrapping':True}
    for _ in range(8):
        assert request(admin,'PUT',path,settings).status_code == 200
    assert request(admin,'GET',path).json() == settings
    assert request(member,'GET',path).json()['keymap'] == 'standard'
    assert request(member,'PUT',path,settings,{'X-CSRF-Token':'wrong'}).status_code == 403
    assert request(None,'GET',path).status_code == 401


def test_ai_connection_test_is_private_csrf_protected_and_does_not_create_report(site,monkeypatch):
    request,admin,member,app,*_ = site
    async def synthetic(self):
        return {'ok':True,'model':'synthetic','vendor':'deepseek','latency_ms':1,'note':'simulated'}
    monkeypatch.setattr('acmweb.ai.AIProvider.test_connection',synthetic)
    assert request(None,'POST','/api/ai/test',{}).status_code==401
    assert request(admin,'POST','/api/ai/test',{},extra={'X-CSRF-Token':'wrong'}).status_code==403
    assert request(admin,'GET','/api/ai/presets').status_code==200
    response=request(admin,'POST','/api/ai/test',{})
    assert response.status_code==200,response.text
    job_id=response.json()['id']
    for _ in range(100):
        jobs=request(admin,'GET','/api/jobs').json()['items']
        job=next(j for j in jobs if j['id']==job_id)
        if job['status'] in {'completed','failed'}:break
        time.sleep(0.01)
    assert job['status']=='completed' and job['result']['model']=='synthetic'
    assert request(admin,'GET','/api/reports').json()['items']==[]
    assert request(member,'GET','/api/jobs').json()['items']==[]


def test_ten_character_password_registration_login_and_change(site):
    request, admin, *_ = site
    invitation = request(admin,'POST','/api/admin/invitations',{}).json()['invitation']
    payload = {'username':'ten_chars','password':'Abcdef123','invitation':invitation}
    assert request(None,'POST','/api/auth/register',payload).status_code == 422
    payload['password'] = 'Abcdef1234'
    response = request(None,'POST','/api/auth/register',payload)
    assert response.status_code == 200,response.text
    response = request(None,'POST','/api/auth/login',{'username':'ten_chars','password':'Abcdef1234'})
    assert response.status_code == 200,response.text
    cookie = response.cookies['acm_user_session']
    who = request(None,'GET','/api/auth/status',extra={'Cookie':'acm_user_session='+cookie}).json()
    who['cookie'] = cookie
    change = {'current_password':'Abcdef1234','new_password':'Xyzabcd12'}
    assert request(who,'POST','/api/auth/password',change).status_code == 422
    change['new_password'] = 'Xyzabcd123'
    assert request(who,'POST','/api/auth/password',change).status_code == 200
    assert request(who,'GET','/api/accounts').status_code == 401
    assert request(None,'POST','/api/auth/login',{'username':'ten_chars','password':'Xyzabcd123'}).status_code == 200


def test_layout_preferences_are_private_csrf_protected_and_not_ai_limited(site):
    request, admin, member, *_ = site
    path = '/api/workbench/layout-settings'
    value = {'mode': 'stacked', 'statement_percent': 60}
    for _ in range(8):
        assert request(admin, 'PUT', path, value).status_code == 200
    assert request(admin, 'GET', path).json() == value
    assert request(member, 'GET', path).json() == {'mode': 'auto', 'statement_percent': 50}
    assert request(member, 'PUT', path, value, {'X-CSRF-Token': 'wrong'}).status_code == 403
    assert request(None, 'GET', path).status_code == 401

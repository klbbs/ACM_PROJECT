"""Independent QR security regression checks; all credentials are synthetic."""
import pytest
from acmweb.accounts import Accounts, AccountList
from acmweb.codex_bridge import AIError
from acmweb.settings import Settings


def test_verified_binding_revision_rejects_stale_cookie_and_preserves_other_accounts(tmp_path):
    settings = Settings(data_dir=tmp_path, config_path=tmp_path/'absent.toml')
    accounts = Accounts(settings)
    accounts.save(AccountList(items=[
        {'platform':'codeforces','handle':'fixture_cf','cookie':'sid=CF-SYNTHETIC'},
        {'platform':'nowcoder','handle':'123','cookie':'sid=OLD-SYNTHETIC'},
    ]))
    stale_revision = accounts.revision()
    accounts.bind_verified_account('nowcoder','123','sid=NEW-SYNTHETIC',stale_revision)
    with pytest.raises(AIError):
        accounts.bind_verified_account('nowcoder','123','sid=STALE-SYNTHETIC',stale_revision)
    saved = accounts.document()['accounts']
    assert [(a['platform'],a['handle'],a['cookie']) for a in saved] == [
        ('codeforces','fixture_cf','sid=CF-SYNTHETIC'),
        ('nowcoder','123','sid=NEW-SYNTHETIC'),
    ]
    assert 'SYNTHETIC' not in str(accounts.public())


def test_verified_binding_invalid_cookie_does_not_mutate_config(tmp_path):
    accounts = Accounts(Settings(data_dir=tmp_path, config_path=tmp_path/'absent.toml'))
    accounts.save(AccountList(items=[{'platform':'atcoder','handle':'fixture','cookie':'sid=KEEP-SYNTHETIC'}]))
    before = accounts.path.read_bytes()
    with pytest.raises(AIError):
        accounts.bind_verified_account('nowcoder','123','sid=bad\r\nHost: attacker.invalid',accounts.revision())
    assert accounts.path.read_bytes() == before


@pytest.mark.parametrize('image_url',[
    'https://attacker.invalid/cgi-bin/showqrcode?ticket=synthetic-ticket',
    'https://mp.weixin.qq.com.attacker.invalid/cgi-bin/showqrcode?ticket=synthetic-ticket',
    'https://user@mp.weixin.qq.com/cgi-bin/showqrcode?ticket=synthetic-ticket',
    'https://mp.weixin.qq.com:443/cgi-bin/showqrcode?ticket=synthetic-ticket',
    'https://mp.weixin.qq.com/cgi-bin/showqrcode?ticket=synthetic-ticket&callback=https://attacker.invalid',
    'https://mp.weixin.qq.com/cgi-bin/showqrcode?ticket=other-ticket',
])
def test_qr_image_url_is_not_an_upstream_controlled_proxy(monkeypatch,image_url):
    import acmweb.nowcoder_qr as qr
    calls=[]
    monkeypatch.setattr(qr,'_json',lambda *a,**k: {'code':0,'data':{
        'ticket':'synthetic-ticket','imageUrl':image_url,'expireSecond':120}})
    def body(session,url,*a,**k):
        calls.append(url)
        return b'login', 'text/html'
    monkeypatch.setattr(qr,'_body',body)
    with pytest.raises(ValueError):
        qr._create(object())
    assert calls == ['https://ac.nowcoder.com/login?callBack=/']


@pytest.mark.parametrize('page',[
    b'<html><a href="/profile/123">public account</a></html>',
    b'<script>window.isLogin = false; window.globalInfo = {ownerId:123};</script>',
    b'<script type="application/json">window.isLogin = true; window.globalInfo = {ownerId:123};</script>',
])
def test_successful_scan_code_does_not_prove_current_identity(monkeypatch,page):
    import acmweb.nowcoder_qr as qr
    monkeypatch.setattr(qr,'_json',lambda *a,**k: {'code':0,'data':{'callBack':'https://attacker.invalid/'}})
    calls=[]
    def body(session,url,*a,**k):
        calls.append(url)
        return page, 'text/html'
    monkeypatch.setattr(qr,'_body',body)
    assert qr._poll(object(),'synthetic-ticket') == ('unverified',None,None)
    assert calls == ['https://ac.nowcoder.com/']


@pytest.fixture
def qr_site(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from acmweb.app import create_app
    import acmweb.nowcoder_qr as qr
    monkeypatch.setattr(qr,'_create',lambda session: ('PRIVATE-TICKET','data:image/jpeg;base64,/9j/',120))
    monkeypatch.setattr(qr,'_poll',lambda session,ticket: ('verified','123','sid=PRIVATE-COOKIE'))
    settings=Settings(data_dir=tmp_path,config_path=tmp_path/'absent.toml',multi_user=True,
        access_token='synthetic-setup-token-long-enough',enable_codex=False,allowed_hosts=('testserver',))
    app=create_app(settings)
    with TestClient(app,base_url='https://testserver') as client:
        def request(who,path,body=None,csrf=True,extra=None):
            headers={'Cookie':'','Origin':'https://testserver','X-ACM-Request':'1'}
            if who:
                headers['Cookie']='acm_user_session='+who['cookie']
                if csrf: headers['X-CSRF-Token']=who['csrf_token']
            headers.update(extra or {})
            return client.post(path,headers=headers,json=body or {})
        def capture(response):
            assert response.status_code == 200,response.text
            cookie=response.cookies['acm_user_session']
            status=client.get('/api/auth/status',headers={'Cookie':'acm_user_session='+cookie}).json()
            return dict(status,cookie=cookie)
        password='Independent-fixture-password-123'
        admin=capture(request(None,'/api/auth/setup',{'username':'kendas','password':password,'setup_token':settings.access_token}))
        other_session=capture(request(None,'/api/auth/login',{'username':'kendas','password':password}))
        invitation=request(admin,'/api/admin/invitations').json()['invitation']
        member=capture(request(None,'/api/auth/register',{'username':'member','password':password,'invitation':invitation}))
        yield app,request,admin,other_session,member,monkeypatch


def test_qr_routes_require_real_session_csrf_and_isolate_site_sessions(qr_site):
    app,request,admin,other_session,member,_=qr_site
    start='/api/platform-login/nowcoder/start'
    poll='/api/platform-login/nowcoder/poll'
    assert request(None,start,{'consent':True}).status_code == 401
    assert request(admin,start,{'consent':True},csrf=False).status_code == 403
    response=request(admin,start,{'consent':True})
    assert response.status_code == 200,response.text
    login_id=response.json()['login_id']
    body={'login_id':login_id}
    assert 'PRIVATE' not in response.text
    for actor in (other_session,member):
        response=request(actor,poll,body,extra={'X-Platform-Login-Owner':admin['cookie'],'X-User-Id':admin['user']['id']})
        assert response.status_code == 404,response.text
    response=request(admin,poll,body)
    assert response.status_code == 200,response.text
    assert response.json()['status'] == 'verified'
    assert response.json()['verified_uid'] == '123'
    assert 'PRIVATE' not in response.text


@pytest.mark.parametrize('operation', ['logout','logout-all','password','expire'])
def test_qr_pending_invalidated_by_session_revocation(qr_site,operation):
    from acmweb.users import digest
    app,request,admin,other_session,member,_=qr_site
    response=request(admin,'/api/platform-login/nowcoder/start',{'consent':True})
    assert response.status_code == 200,response.text
    login_id=response.json()['login_id']
    if operation == 'expire':
        with app.state.identities.connect() as db:
            db.execute('UPDATE sessions SET expires_at=0 WHERE hash=?',(digest(admin['cookie']),))
    else:
        payload={'current_password':'Independent-fixture-password-123','new_password':'Changed-fixture-password-456'} if operation == 'password' else {}
        response=request(admin,'/api/auth/'+operation,payload)
        assert response.status_code == 200,response.text
    response=request(admin,'/api/platform-login/nowcoder/poll',{'login_id':login_id})
    assert response.status_code == 401,response.text
    manager=app.state.workspaces.apps[admin['user']['id']][0].state.nowcoder_qr
    assert manager.accounts.public()['items'] == []


def test_inflight_poll_after_logout_cannot_persist_credentials(qr_site):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import acmweb.nowcoder_qr as qr
    app,request,admin,other_session,member,monkeypatch=qr_site
    entered=threading.Event(); release=threading.Event()
    def delayed_poll(session,ticket):
        entered.set()
        assert release.wait(10)
        return 'verified','123','sid=PRIVATE-COOKIE'
    monkeypatch.setattr(qr,'_poll',delayed_poll)
    started=request(admin,'/api/platform-login/nowcoder/start',{'consent':True})
    assert started.status_code == 200,started.text
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(request,admin,'/api/platform-login/nowcoder/poll',{'login_id':started.json()['login_id']})
        assert entered.wait(10)
        try:
            response=request(admin,'/api/auth/logout')
            assert response.status_code == 200,response.text
        finally:
            release.set()
        response=pending.result(timeout=10)
    assert response.status_code in {401,404},response.text
    manager=app.state.workspaces.apps[admin['user']['id']][0].state.nowcoder_qr
    assert manager.accounts.public()['items'] == []


@pytest.mark.parametrize('code',[False,True,0.0,1.0,'0',None])
def test_unknown_status_code_types_never_trigger_identity_request(monkeypatch,code):
    import acmweb.nowcoder_qr as qr
    monkeypatch.setattr(qr,'_json',lambda *a,**k: {'code':code,'msg':'二维码未被扫描','data':{}})
    def unexpected(*a,**k):
        pytest.fail('malformed status must not trigger identity request')
    monkeypatch.setattr(qr,'_body',unexpected)
    assert qr._poll(object(),'synthetic-ticket') == ('failed',None,None)


@pytest.mark.parametrize('invalidate',['revision','expire'])
def test_inflight_poll_cannot_overwrite_changed_config_or_pass_deadline(qr_site,invalidate):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time
    import acmweb.nowcoder_qr as qr
    app,request,admin,_,_,monkeypatch=qr_site
    assert request(admin,'/api/platform-login/nowcoder/start',{'consent':False}).status_code == 400
    manager=app.state.workspaces.apps[admin['user']['id']][0].state.nowcoder_qr
    manager.accounts.save(AccountList(items=[{'platform':'atcoder','handle':'fixture','cookie':'sid=KEEP-SYNTHETIC'}]))
    entered=threading.Event(); release=threading.Event()
    def delayed_poll(session,ticket):
        entered.set(); assert release.wait(10)
        return 'verified','123','sid=PRIVATE-COOKIE'
    monkeypatch.setattr(qr,'_poll',delayed_poll)
    started=request(admin,'/api/platform-login/nowcoder/start',{'consent':True})
    assert started.status_code == 200,started.text
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(request,admin,'/api/platform-login/nowcoder/poll',{'login_id':started.json()['login_id']})
        assert entered.wait(10)
        try:
            if invalidate == 'revision':
                manager.accounts.save(AccountList(items=[{'platform':'atcoder','handle':'fixture','cookie':'sid=NEWER-SYNTHETIC'}]))
            else:
                with manager.lock: manager.flow['deadline']=time.monotonic()-1
        finally:
            release.set()
        response=pending.result(timeout=10)
    if invalidate == 'revision':
        assert response.status_code == 409,response.text
        expected='sid=NEWER-SYNTHETIC'
    else:
        assert response.status_code == 200 and response.json()['status'] == 'expired',response.text
        expected='sid=KEEP-SYNTHETIC'
    assert manager.accounts.document()['accounts'] == [{'platform':'atcoder','handle':'fixture','enrich':False,'cookie':expected,'login_method':'cookie'}]

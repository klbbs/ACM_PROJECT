from types import SimpleNamespace

class Response(SimpleNamespace):
 def __enter__(self):return self
 def __exit__(self,*args):pass
 encoding='utf-8'
 def iter_content(self,chunk_size):yield self.text.encode()
import pytest
from fastapi import HTTPException
import requests
from acmweb.credential_check import CredentialChecks,inspect_identity,URLS

@pytest.mark.parametrize('platform,body,expected',[
 ('codeforces','<a href="/profile/demo">demo</a>',('unknown',None)),
 ('codeforces','<form action="/enter"><input name="password"></form>',('expired',None)),
 ('codeforces','<div class="lang-chooser"><a href="/profile/demo">demo</a><a href="/logout">logout</a></div><form action="/settings/general"><input name="csrf_token"></form>',('authenticated','demo')),
 ('atcoder','<div id="navbar-collapse"><a href="/users/demo">demo</a><a href="/logout">logout</a></div><form action="/settings"><input name="csrf_token"></form>',('authenticated','demo')),
 ('luogu','<script id="lentille-context">{"data":{"user":{"uid":42}}}</script>',('unknown',None)),
 ('luogu','<script id="lentille-context">{"data":{"currentUser":{"uid":42}}}</script>',('authenticated','42')),
 ('luogu','<script id="lentille-context">{"data":{"currentUser":null}}</script>',('expired',None)),
 ('nowcoder','<a href="/profile/123">user</a>',('unknown',None)),
 ('codeforces','<title>Just a moment</title>',('blocked',None)),
])
def test_authentication_needs_principal_not_public_profile(platform,body,expected):assert inspect_identity(platform,body)==expected

class Accounts:
 cookie='secret=DO-NOT-LEAK'
 def document(self):return {'accounts':[{'platform':'codeforces','handle':'demo','cookie':self.cookie}]}

class Session:
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def get(self,url,**kwargs):
  assert url==URLS['codeforces'] and kwargs['allow_redirects'] is False
  assert kwargs['headers']['User-Agent'].startswith('Mozilla/5.0')
  return Response(status_code=200,headers={},text='<div class="lang-chooser"><a href="/profile/other">other</a><a href="/logout">exit</a></div><form action="/settings/general"></form>')

def test_mismatch_cache_and_credential_change(monkeypatch):
 monkeypatch.setattr('acmweb.credential_check.requests.Session',Session)
 accounts=Accounts();checker=CredentialChecks(accounts)
 result=checker.check('codeforces','demo')
 assert result['status']=='account_mismatch' and not result['login_verified'] and result['public_reachable']
 assert 'DO-NOT-LEAK' not in str(result) and 'digest' not in result
 assert checker.check('codeforces','demo')['cached']
 accounts.cookie='changed=secret'
 assert checker.statuses()=={'items':[]}
 with pytest.raises(HTTPException) as exc:checker.check('codeforces','demo')
 assert exc.value.status_code==429

def test_redirect_never_followed(monkeypatch):
 class Redirect(Session):
  def get(self,url,**kwargs):
   assert not kwargs['allow_redirects'];return Response(status_code=302,headers={'Location':'https://evil.com/login'},text='secret')
 monkeypatch.setattr('acmweb.credential_check.requests.Session',Redirect)
 result=CredentialChecks(Accounts()).check('codeforces','demo')
 assert result['status']=='unknown' and 'secret' not in str(result)

@pytest.mark.parametrize('status,expected',[(403,'blocked'),(418,'blocked'),(429,'blocked'),(503,'unknown'),(401,'expired'),(500,'unknown')])
def test_http_status_never_implies_login(monkeypatch,status,expected):
 class Upstream(Session):
  def get(self,*args,**kwargs):return Response(status_code=status,headers={},text='')
 monkeypatch.setattr('acmweb.credential_check.requests.Session',Upstream)
 result=CredentialChecks(Accounts()).check('codeforces','demo')
 assert result['status']==expected and result['login_verified'] is False
 assert result['auto_submit_supported'] is False

def test_response_body_is_bounded_and_not_classified_from_truncated_html(monkeypatch):
 class Large(Response):
  def iter_content(self,chunk_size):
   for i in range(1000):
    assert i<65, 'must stop reading after limit'
    yield b'x'*16384
 class Upstream(Session):
  def get(self,*args,**kwargs):
   assert kwargs['stream'] is True
   return Large(status_code=200,headers={},text='')
 monkeypatch.setattr('acmweb.credential_check.requests.Session',Upstream)
 result=CredentialChecks(Accounts()).check('codeforces','demo')
 assert result['status']=='unknown' and not result['login_verified']

def test_isolated_workspace_cannot_use_server_environment_cookie(monkeypatch):
 monkeypatch.setenv('SERVER_COOKIE','session=ADMIN-SECRET')
 accounts=SimpleNamespace(settings=SimpleNamespace(isolated=True),document=lambda:{'accounts':[{'platform':'codeforces','handle':'demo','cookie_env':'SERVER_COOKIE'}]})
 result=CredentialChecks(accounts).check('codeforces','demo')
 assert result['status']=='missing' and 'ADMIN-SECRET' not in str(result)

def test_profile_link_outside_principal_nav_is_not_authentication():
 html='<a href="/profile/demo">demo</a><a href="/logout">logout</a><form action="/settings/general"></form>'
 assert inspect_identity('codeforces',html)==('unknown',None)

@pytest.mark.parametrize('platform',['codeforces','atcoder','luogu','nowcoder'])
@pytest.mark.parametrize('status,body,reason',[
 (418,'Access denied','access_restricted'),
 (403,'<div class="g-recaptcha"></div><input value="SECRET-CSRF">','captcha'),
 (418,'<p>请完成验证码</p>','captcha'),
 (200,'<title>Just a moment</title>','browser_challenge'),
 (503,'<script src="/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1"></script>','browser_challenge'),
])
def test_challenge_has_fixed_official_recovery_entry_and_never_leaks_body(monkeypatch,platform,status,body,reason):
 account=SimpleNamespace(document=lambda:{'accounts':[{'platform':platform,'handle':'42','cookie':'session=PRIVATE-COOKIE'}]})
 class Upstream(Session):
  def get(self,url,**kwargs):
   assert url==URLS[platform] and kwargs['allow_redirects'] is False
   return Response(status_code=status,headers={'Location':'https://attacker.test/captcha?SECRET'},text=body)
 monkeypatch.setattr('acmweb.credential_check.requests.Session',Upstream)
 result=CredentialChecks(account).check(platform,'42')
 assert result['status']=='blocked' and result['reason']==reason
 assert result['upstream_status']==status
 assert not result['login_verified'] and result['browser_action_required']
 assert result['browser_action_url']==URLS[platform]
 assert any('出口 IP' in value for value in result['guidance'])
 assert not any(secret in str(result) for secret in ('SECRET','PRIVATE-COOKIE','attacker.test','<script','<input'))


def test_force_refresh_bypasses_cache_after_cooldown_but_never_bypasses_rate_limit(monkeypatch):
 now=[1000.0]; calls=[]
 monkeypatch.setattr('acmweb.credential_check.time.time',lambda:now[0])
 class Upstream(Session):
  def get(self,*args,**kwargs):
   calls.append(1)
   if len(calls)==1:return Response(status_code=418,headers={},text='Access denied')
   return Response(status_code=200,headers={},text='<div class="lang-chooser"><a href="/profile/demo">demo</a><a href="/logout">exit</a></div><form action="/settings/general"></form>')
 monkeypatch.setattr('acmweb.credential_check.requests.Session',Upstream)
 checker=CredentialChecks(Accounts())
 first=checker.check('codeforces','demo')
 assert first['reason']=='access_restricted'
 assert checker.check('codeforces','demo')['cached'] is True
 with pytest.raises(HTTPException) as error:checker.check('codeforces','demo',force_refresh=True)
 assert error.value.status_code==429 and error.value.headers['Retry-After']=='10'
 assert len(calls)==1
 now[0]+=11
 assert checker.check('codeforces','demo')['status']=='blocked'
 fresh=checker.check('codeforces','demo',force_refresh=True)
 assert fresh['status']=='authenticated' and fresh['cached'] is False and len(calls)==2
 assert checker.statuses()['items'][0]['status']=='authenticated'


@pytest.mark.parametrize('delay,expected',[('120',120),('9999999',3600),('1',10),('evil',60),('99999999999999999999999999999',60)])
def test_upstream_rate_limit_has_safe_bounded_retry_and_is_enforced(monkeypatch,delay,expected):
 now=[1000.0];calls=[]
 monkeypatch.setattr('acmweb.credential_check.time.time',lambda:now[0])
 class Upstream(Session):
  def get(self,*args,**kwargs):
   calls.append(1)
   return Response(status_code=429,headers={'Retry-After':delay},text='https://attacker.test SECRET')
 monkeypatch.setattr('acmweb.credential_check.requests.Session',Upstream)
 checker=CredentialChecks(Accounts());result=checker.check('codeforces','demo')
 assert result['reason']=='rate_limited' and result['retry_after_seconds']==expected
 assert not result['browser_action_required'] and 'SECRET' not in str(result)
 now[0]+=expected-1
 with pytest.raises(HTTPException) as error:checker.check('codeforces','demo',force_refresh=True)
 assert error.value.status_code==429 and len(calls)==1
 now[0]+=2
 assert checker.check('codeforces','demo',force_refresh=True)['cached'] is False
 assert len(calls)==2


@pytest.mark.parametrize('error,reason',[(requests.Timeout('PRIVATE-COOKIE'),'timeout'),(requests.ConnectionError('PRIVATE-COOKIE'),'network_error')])
def test_network_failure_distinct_from_login_expiry(monkeypatch,error,reason):
 class Upstream(Session):
  def get(self,*args,**kwargs):raise error
 monkeypatch.setattr('acmweb.credential_check.requests.Session',Upstream)
 result=CredentialChecks(Accounts()).check('codeforces','demo')
 assert result['status']=='unknown' and result['reason']==reason
 assert not result['login_verified'] and not result['browser_action_required']
 assert 'PRIVATE-COOKIE' not in str(result)


@pytest.mark.parametrize('platform,path', [('codeforces','https://codeforces.com/enter?back=%2Fsettings%2Fgeneral'),('atcoder','/login?continue=%2Fsettings')])
def test_real_anonymous_redirect_shapes_are_expired(monkeypatch,platform,path):
 account=SimpleNamespace(document=lambda:{'accounts':[{'platform':platform,'handle':'demo','cookie':'test=synthetic'}]})
 class Upstream(Session):
  def get(self,*args,**kwargs):return Response(status_code=302,headers={'location':path},text='')
 monkeypatch.setattr('acmweb.credential_check.requests.Session',Upstream)
 result=CredentialChecks(account).check(platform,'demo')
 assert result['status']=='expired' and result['reason']=='not_logged_in'
 assert result['browser_action_required'] and result['browser_action_url']==URLS[platform]


def test_nowcoder_public_home_never_becomes_authenticated_from_cookie_or_public_user(monkeypatch):
 account=SimpleNamespace(document=lambda:{'accounts':[{'platform':'nowcoder','handle':'42','cookie':'t=synthetic'}]})
 class Upstream(Session):
  def get(self,*args,**kwargs):return Response(status_code=200,headers={},text='<a href="/profile/42">42</a><a href="/logout">退出</a>')
 monkeypatch.setattr('acmweb.credential_check.requests.Session',Upstream)
 result=CredentialChecks(account).check('nowcoder','42')
 assert result['status']=='unknown' and result['reason']=='page_changed'
 assert not result['login_verified'] and '当前会话身份' in result['message']

@pytest.mark.parametrize('bootstrap,expected',[
 ('window.isLogin = false; window.globalInfo = {canLoadInIframe: \'true\'};',('expired',None)),
 ('window.isLogin = true; window.globalInfo = {ownerId: 42, ownerName: \'demo\', status: 0};',('authenticated','42')),
 ('window.isLogin = true; window.globalInfo = {"ownerId": "42", canLoadInIframe: \'true\'};',('authenticated','42')),
 ('window.isLogin = true; window.globalInfo = {userId: 42};',('unknown',None)),
 ('window.isLogin = true; window.globalInfo = {userInfo: {ownerId: 42}};',('unknown',None)),
 ('window.isLogin = true; window.globalInfo = {ownerId: 42, ownerId: 43};',('unknown',None)),
 ('window.isLogin = true; window.globalInfo = {ownerId: -1};',('unknown',None)),
 ('window.isLogin = true; window.globalInfo = {ownerId: (function(){return 42})()};',('unknown',None)),
 ('window.isLogin = true; window.globalInfo = {ownerName: "demo,ownerId:42,"};',('unknown',None)),
 ('window.isLogin = true; window.globalInfo = {ownerId: `42`};',('unknown',None)),
 ('/*\nwindow.isLogin = true; window.globalInfo = {ownerId: 42};\n*/',('unknown',None)),
 ('const text = `\nwindow.isLogin = true; window.globalInfo = {ownerId: 42};`;',('unknown',None)),
 ('window.globalInfo = {ownerId: 42};',('unknown',None)),
])
def test_nowcoder_requires_actual_server_bootstrap_current_principal(bootstrap,expected):
 assert inspect_identity('nowcoder','<script>'+bootstrap+'</script><a href="/profile/999">viewed user</a>')==expected


def test_nowcoder_conflicting_bootstraps_are_not_trusted():
 assert inspect_identity('nowcoder','<script>window.isLogin=true;window.globalInfo={ownerId:42};</script><script>window.isLogin=false;window.globalInfo={};</script>')==('unknown',None)


@pytest.mark.parametrize('value',['false','true',0,1,None])
def test_force_refresh_requires_actual_boolean(value):
 from acmweb.credential_check import CheckInput
 from pydantic import ValidationError
 with pytest.raises(ValidationError):CheckInput(platform='luogu',handle='42',force_refresh=value)


def test_nowcoder_non_executing_script_data_is_not_a_principal():
 assert inspect_identity('nowcoder','<script type="text/plain">window.isLogin=true;window.globalInfo={ownerId:42};</script>')==('unknown',None)


@pytest.mark.parametrize('cookie,present',[
 ('only_public=synthetic',False),
 ('JSESSIONID=private-session; RCPC=private-challenge',True),
 ('JSESSIONID=',False),
 ('other=JSESSIONID=private',False),
])
def test_cf_cookie_session_diagnostic_never_substitutes_for_identity(monkeypatch,cookie,present):
 class Login(Session):
  def get(self,*args,**kwargs):
   return Response(status_code=302,headers={'Location':'/enter'},text='')
 monkeypatch.setattr('acmweb.credential_check.requests.Session',Login)
 account=SimpleNamespace(document=lambda:{'accounts':[{'platform':'codeforces','handle':'demo','cookie':cookie}]})
 result=CredentialChecks(account).check('codeforces','demo')
 assert result['cookie_diagnostics']=={'cf_session_cookie_present':present}
 assert result['status']=='expired' and not result['login_verified']
 assert 'private-session' not in str(result) and 'private-challenge' not in str(result)
 if not present:assert '常见会话字段' in ' '.join(result['guidance'])


def test_cf_identity_evidence_overrides_advisory_cookie_field_absence(monkeypatch):
 class Verified(Session):
  def get(self,*args,**kwargs):
   return Response(status_code=200,headers={},text='<div class="lang-chooser"><a href="/profile/demo">demo</a><a href="/logout">exit</a></div><form action="/settings/general"></form>')
 monkeypatch.setattr('acmweb.credential_check.requests.Session',Verified)
 result=CredentialChecks(Accounts()).check('codeforces','demo')
 assert result['status']=='authenticated' and result['login_verified']
 assert not result['cookie_diagnostics']['cf_session_cookie_present']
 assert not any('未发现 CF 常见' in item for item in result['guidance'])

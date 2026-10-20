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
  assert kwargs['headers']['User-Agent']==requests.utils.default_user_agent()
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

@pytest.mark.parametrize('status,expected',[(403,'blocked'),(418,'blocked'),(429,'blocked'),(503,'blocked'),(401,'expired'),(500,'unknown')])
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

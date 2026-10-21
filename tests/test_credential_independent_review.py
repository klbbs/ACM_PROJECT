"""Independent authentication evidence regressions, using no real credentials."""
import json
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from acmweb.credential_check import CredentialChecks, challenge_reason, inspect_identity


@pytest.mark.parametrize('script', [
    '/cdn-cgi/challenge-platform/scripts/jsd/api.js',
    '/cdn-cgi/challenge-platform/h/b/scripts/jsd/012345/main.js',
])
def test_passive_cloudflare_js_detection_does_not_hide_verified_luogu_identity(script):
    # Cloudflare documents JSD injection into otherwise normal HTML responses:
    # https://developers.cloudflare.com/cloudflare-challenges/challenge-types/javascript-detections/
    html = ('<title>洛谷</title><script id="lentille-context" type="application/json">'
            '{"data":{"currentUser":{"uid":42}}}</script>'
            f'<script src="{script}"></script>')
    assert challenge_reason(BeautifulSoup(html, 'html.parser')) is None
    assert inspect_identity('luogu', html) == ('authenticated', '42')


def test_actual_challenge_still_overrides_identity_like_markup():
    html = ('<title>Just a moment</title><div id="challenge-running"></div>'
            '<script id="lentille-context">{"data":{"currentUser":{"uid":42}}}</script>')
    assert inspect_identity('luogu', html) == ('blocked', None)


def test_only_current_user_not_viewed_profile_can_validate_luogu():
    for html in [
        '<script id="lentille-context">{"data":{"user":{"uid":42}}}</script>',
        '<a href="/user/42">42</a><a href="/auth/logout">退出</a>',
    ]:
        assert inspect_identity('luogu', html) == ('unknown', None)


@pytest.mark.parametrize('context,expected', [
    ({'instance':'main','template':'user.show','status':200,'data':{'user':{'uid':999}},'user':{'uid':42}}, ('authenticated','42')),
    ({'instance':'auth','template':'login','status':200,'data':{'webauthn':None},'user':None}, ('expired',None)),
    ({'user':None,'data':{'user':{'uid':42}}}, ('expired',None)),
    ({'user':{'uid':'42'},'currentUser':{'uid':42},'data':{'currentUser':{'uid':'42'}}}, ('authenticated','42')),
    ({'user':{'uid':42},'data':{'currentUser':{'uid':999}}}, ('unknown',None)),
    ({'user':None,'currentUser':{'uid':42}}, ('unknown',None)),
    ({'currentUser':{'uid':42},'data':{'currentUser':None}}, ('unknown',None)),
    ({'user':{'uid':42},'currentUser':False}, ('unknown',None)),
])
def test_luogu_current_and_legacy_session_fields_agree(context, expected):
    html='<script id="lentille-context" type="application/json">'+json.dumps(context)+'</script>'
    assert inspect_identity('luogu',html)==expected


@pytest.mark.parametrize('uid', [True,False,0,-1,42.0,'0','042','٤٢','42.0','42x',None,{}])
def test_luogu_session_uid_must_be_positive_ascii_integer(uid):
    html='<script id="lentille-context">'+json.dumps({'user':{'uid':uid}})+'</script>'
    assert inspect_identity('luogu',html)==('unknown',None)


@pytest.mark.parametrize('html', [
    '<script id="lentille-context">{"user":{"uid":42},"user":null}</script>',
    '<script id="lentille-context">{"user":{"uid":42,"uid":999}}</script>',
    '<script id="lentille-context">{"user":{"uid":42}}</script><script id="lentille-context">{"user":null}</script>',
    '<template><script id="lentille-context">{"user":{"uid":42}}</script></template>',
    '<script id="lentille-context" type="text/template">{"user":{"uid":42}}</script>',
    '<script id="lentille-context" src="/public.js">{"user":{"uid":42}}</script>',
])
def test_luogu_ambiguous_or_inert_session_envelopes_fail_closed(html):
    assert inspect_identity('luogu',html)==('unknown',None)


def test_luogu_current_identity_cannot_override_active_challenge():
    html='<title>Just a moment</title><script id="lentille-context">{"user":{"uid":42}}</script>'
    assert inspect_identity('luogu',html)==('blocked',None)


def test_human_challenge_does_not_forward_external_location_or_hidden_credentials(monkeypatch):
    class Response:
        status_code = 403
        encoding = 'utf-8'
        headers = {'Location': 'https://attacker.invalid/captcha?session=private'}

        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, chunk_size):
            yield b'<div class="h-captcha"></div><input name="csrf" value="private-nonce">'

    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url, **kwargs):
            assert url == 'https://www.luogu.com.cn/'
            assert kwargs['allow_redirects'] is False
            return Response()

    monkeypatch.setattr('acmweb.credential_check.requests.Session', Session)
    accounts = SimpleNamespace(document=lambda: {'accounts': [{
        'platform': 'luogu', 'handle': '42', 'cookie': 'session=private-cookie',
    }]})
    result = CredentialChecks(accounts).check('luogu', '42')
    assert result['reason'] == 'captcha'
    assert result['login_verified'] is False
    assert result['browser_action_url'] == 'https://www.luogu.com.cn/'
    for secret in ['private-cookie', 'private-nonce', 'attacker.invalid', '<input']:
        assert secret not in str(result)


@pytest.mark.parametrize('html', [
    '<script type="text/template">window.isLogin=true;window.globalInfo={ownerId:42};</script>',
    '<script type="application/json">window.isLogin=true;window.globalInfo={ownerId:42};</script>',
    '<template><script>window.isLogin=true;window.globalInfo={ownerId:42};</script></template>',
    '<noscript><script>window.isLogin=true;window.globalInfo={ownerId:42};</script></noscript>',
    '<script>window.isLogin=true;window.globalInfo={ownerId:42};window.isLogin=false;</script>',
    '<script>window.isLogin=true;window.globalInfo={ownerId:42};window.globalInfo={ownerId:999};</script>',
])
def test_nowcoder_inert_templates_and_conflicting_same_block_are_not_session_evidence(html):
    assert inspect_identity('nowcoder', html) == ('unknown', None)


@pytest.mark.parametrize('source', [
    '// window.isLogin=true;window.globalInfo={ownerId:42};',
    'const fixture="window.isLogin=true;window.globalInfo={ownerId:42};";',
    'window.isLogin=true;window.globalInfo={user:{ownerId:42},ownerName:"demo"};',
    'window.isLogin=true;window.globalInfo={ownerName:"userId:42,ownerId:42"};',
])
def test_nowcoder_source_literals_and_nested_public_fields_are_not_principal(source):
    assert inspect_identity('nowcoder', '<script>' + source + '</script>') == ('unknown', None)

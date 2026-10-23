"""Local reproductions of the supplied security findings, without external scans."""
import json
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.settings import Settings
from acmweb.users import AUTH_SLOTS, digest
from test_multiuser import site, PASSWORD


@pytest.fixture(autouse=True)
def stable_rate_limit_window(monkeypatch):
    # Repeated scrypt checks can otherwise straddle a real 15-minute boundary
    # and legitimately reset the counter halfway through a throttling test.
    instant = time.time()
    monkeypatch.setattr(time, 'time', lambda: instant)


def test_login_flood_from_other_peer_cannot_lock_out_correct_password(site):
    _, _, _, app, _, _ = site
    headers = {'X-ACM-Request':'1','Origin':'https://testserver'}
    attacker = TestClient(app, base_url='https://testserver', client=('192.0.2.10',40000))
    legitimate = TestClient(app, base_url='https://testserver', client=('192.0.2.20',40000))
    for _ in range(10):
        assert attacker.post('/api/auth/login',headers=headers,json={
            'username':'member','password':'wrong-password-123'}).status_code == 401
    assert attacker.post('/api/auth/login',headers=headers,json={
        'username':'member','password':PASSWORD}).status_code == 429
    assert legitimate.post('/api/auth/login',headers=headers,json={
        'username':'member','password':PASSWORD}).status_code == 200


def test_bad_invitation_rejected_before_scrypt_and_reserved_name_disclosure(site, monkeypatch):
    request, *_ = site
    hashed = []
    def forbidden(*args, **kwargs):
        hashed.append(True)
        pytest.fail('invalid invitations must not run scrypt')
    monkeypatch.setattr('acmweb.users.password_hash',forbidden)
    replies = [request(None,'POST','/api/auth/register',{
        'username':name,'password':PASSWORD,'invitation':'invalid-invitation'})
        for name in ('kendas','admin','unusedname')]
    assert all(r.status_code == 400 for r in replies)
    assert len({r.text for r in replies}) == 1 and hashed == []


def test_authenticated_visitors_do_not_share_anonymous_ip_budget(site):
    request, admin, member, app, *_ = site
    now = int(time.time())
    with app.state.identities.connect() as db:
        db.execute('INSERT OR REPLACE INTO rate_limits VALUES(?,?,?,?)',
                   ('ip:'+digest('testclient'),now//60,300,now+60))
        db.execute('INSERT OR REPLACE INTO rate_limits VALUES(?,?,?,?)',
                   ('auth-global',now//60,60,now+60))
    assert request(member,'GET','/api/accounts').status_code == 200
    assert request(admin,'GET','/api/accounts').status_code == 200


def test_auth_capacity_recovers_immediately_without_global_window_lockout(site):
    request, *_ = site
    assert AUTH_SLOTS.acquire(False)
    assert AUTH_SLOTS.acquire(False)
    try:
        busy = request(None,'POST','/api/auth/login',{'username':'member','password':PASSWORD})
        assert busy.status_code == 503 and busy.headers['Retry-After'] == '1'
    finally:
        AUTH_SLOTS.release()
        AUTH_SLOTS.release()
    assert request(None,'POST','/api/auth/login',{'username':'member','password':PASSWORD}).status_code == 200


def test_member_contract_is_not_published_and_presence_contains_no_internal_ids(site):
    request, admin, member, *_ = site
    for path in ('/api/openapi.json','/api/docs'):
        assert request(member,'GET',path).status_code == 404
    request(admin,'POST','/api/presence',{})
    result = request(member,'GET','/api/presence')
    assert admin['user']['id'] not in result.text
    assert result.json()['items'] == [{'username':'kendas','self':False}]


def test_initial_setup_requires_secret_before_disclosing_reserved_name_or_state(site):
    request, *_ = site
    responses = [request(None,'POST','/api/auth/setup',{
        'username':name,'password':PASSWORD,'setup_token':'invalid'}) for name in ('admin','kendas','unusedname')]
    assert all(r.status_code == 403 for r in responses)
    assert len({r.text for r in responses}) == 1


def test_host_is_checked_even_when_single_user_access_token_exists(tmp_path):
    settings=Settings(data_dir=tmp_path,config_path=tmp_path/'none.toml',production=True,
                      multi_user=False,enable_codex=False,allowed_hosts=('acm.example.test',),
                      access_token='synthetic-test-access-token-long-enough')
    with TestClient(create_app(settings),base_url='https://acm.example.test') as client:
        assert client.get('/api/health',headers={'Host':'evil.invalid'}).status_code == 400
        assert client.get('/api/health').json() == {'status':'ok'}
        response=client.get('/api/openapi.json',headers={'Authorization':'Bearer '+settings.access_token})
        assert response.status_code == 404


def test_production_wildcard_hosts_are_rejected(tmp_path):
    settings=Settings(data_dir=tmp_path,production=True,allowed_hosts=('*',),
                      access_token='synthetic-test-access-token-long-enough')
    with pytest.raises(RuntimeError,match='通配符'):
        settings.validate()


# ---------------------------------------------------------------------------
# Second review pass (2026-10-23): each supplied finding is reproduced here with
# the residual behaviour it leaves behind. These are development self-checks on
# temporary data; they are not a deployment or WAF verification.
# ---------------------------------------------------------------------------

HEAD = {'X-ACM-Request':'1','Origin':'https://testserver'}

def peer(app, address):
    # No context manager: the fixture already owns the lifespan/instance lock.
    return TestClient(app, base_url='https://testserver', client=(address, 40000))

def call(client, who, method, path, body=None):
    headers = dict(HEAD)
    if who:
        headers.update({'Cookie':'acm_user_session='+who['cookie'],'X-CSRF-Token':who['csrf_token']})
    else:
        headers['Cookie'] = ''
    return client.request(method, path, headers=headers, **({'json':body} if body is not None else {}))

def sign_in(client, username, password):
    return client.post('/api/auth/login', headers=dict(HEAD),
                       json={'username':username,'password':password})


def test_login_throttle_is_scoped_to_peer_and_account_pair(site):
    _, admin, member, app, *_ = site
    attacker = peer(app, '198.51.100.31')
    for _ in range(10):
        assert sign_in(attacker, 'member', 'wrong-password-123').status_code == 401
    # Only this (source, account) pair is limited: no account-wide or site-wide lock.
    assert sign_in(attacker, 'member', PASSWORD).status_code == 429
    assert sign_in(attacker, 'kendas', PASSWORD).status_code == 200
    assert sign_in(peer(app, '198.51.100.32'), 'member', PASSWORD).status_code == 200


def test_login_errors_are_identical_for_unknown_reserved_and_inactive_accounts(site):
    request, admin, member, app, *_ = site
    probe = peer(app, '198.51.100.41')
    replies = [
        sign_in(probe, 'nobody_here', 'wrong-password-123'),
        sign_in(probe, 'kendas', 'wrong-password-123'),
        sign_in(probe, 'member', 'wrong-password-123'),
    ]
    assert request(admin, 'PUT', f"/api/admin/users/{member['user']['id']}",
                   {'role':'member','active':False}).status_code == 200
    replies.append(sign_in(probe, 'member', PASSWORD))
    assert {r.status_code for r in replies} == {401}
    assert len({r.text for r in replies}) == 1


def test_unknown_user_still_pays_the_password_verification_cost(site, monkeypatch):
    _, _, _, app, *_ = site
    calls = []
    monkeypatch.setattr('acmweb.users.verify_password',
                        lambda password, encoded: calls.append(encoded) or False)
    probe = peer(app, '198.51.100.51')
    assert sign_in(probe, 'nobody_here', 'wrong-password-123').status_code == 401
    assert sign_in(probe, 'member', 'wrong-password-123').status_code == 401
    # Unknown names are verified against a dummy hash instead of skipping scrypt.
    assert len(calls) == 2 and all(calls)


def test_registration_does_not_reveal_reserved_names_before_the_invitation(site):
    request, *_ = site
    replies = [request(None, 'POST', '/api/auth/register',
                       {'username':name,'password':PASSWORD,'invitation':'invalid-invitation'})
               for name in ('kendas','KENDAS','admin','kds','unusedname')]
    assert {r.status_code for r in replies} == {400}
    assert len({r.text for r in replies}) == 1
    # With a real invitation the reserved name is rejected as "unavailable", the
    # same answer a taken name gets; this is a registration feature, not an oracle.
    invitation = request(site[1], 'POST', '/api/admin/invitations', {}).json()['invitation']
    taken = request(None, 'POST', '/api/auth/register',
                    {'username':'kendas','password':PASSWORD,'invitation':invitation})
    assert taken.status_code == 409


def test_auth_capacity_gate_covers_login_and_registration_without_a_global_window(site):
    request, admin, *_ = site
    invitation = request(admin, 'POST', '/api/admin/invitations', {}).json()['invitation']
    assert AUTH_SLOTS.acquire(False)
    assert AUTH_SLOTS.acquire(False)
    try:
        for path, body in (('/api/auth/login', {'username':'member','password':PASSWORD}),
                           ('/api/auth/register', {'username':'newmember','password':PASSWORD,
                                                   'invitation':invitation})):
            busy = request(None, 'POST', path, body)
            assert busy.status_code == 503 and busy.headers['Retry-After'] == '1', path
    finally:
        AUTH_SLOTS.release()
        AUTH_SLOTS.release()
    assert request(None, 'POST', '/api/auth/login',
                   {'username':'member','password':PASSWORD}).status_code == 200


def test_anonymous_auth_budget_is_per_peer_and_spares_signed_in_members(site):
    _, admin, member, app, *_ = site
    noisy = peer(app, '198.51.100.61')
    # Login has its own 120/900s source budget. Seed its boundary so this
    # regression does not perform 120 deliberately expensive password hashes.
    now = int(time.time())
    with app.state.identities.connect() as db:
        db.execute('INSERT OR REPLACE INTO rate_limits VALUES(?,?,?,?)',
                   ('auth-ip:login:'+digest('198.51.100.61'), now//900, 119, now+900))
    assert sign_in(noisy, 'probe_boundary', 'wrong-password-123').status_code == 401
    blocked = sign_in(noisy, 'member', PASSWORD)
    assert blocked.status_code == 429
    # The exhausted anonymous budget belongs to that peer only.
    assert call(noisy, member, 'GET', '/api/accounts').status_code == 200
    assert sign_in(peer(app, '198.51.100.62'), 'member', PASSWORD).status_code == 200


def test_api_contract_is_not_published_to_anonymous_members_or_admins(site):
    request, admin, member, *_ = site
    for path in ('/api/openapi.json', '/api/docs', '/api/redoc'):
        assert request(None, 'GET', path).status_code == 401, path
        assert request(member, 'GET', path).status_code == 404, path
        assert request(admin, 'GET', path).status_code == 404, path


def test_public_status_reveals_setup_state_but_no_identity(site):
    request, admin, member, *_ = site
    body = request(None, 'GET', '/api/auth/status').json()
    # Needed by the setup wizard: whether any administrator exists yet.
    assert body == {'required':True,'multi_user':True,'authenticated':False,
                    'setup_required':False,'user':None,'csrf_token':None}
    assert admin['user']['id'] not in request(None, 'GET', '/api/auth/status').text
    assert 'member' not in request(None, 'GET', '/api/auth/status').text


def test_production_health_and_status_hide_version_and_mode(tmp_path):
    token = 'synthetic-production-token-long-enough'
    for multi in (False, True):
        settings = Settings(data_dir=tmp_path/f'app-{multi}', production=True, multi_user=multi,
                            enable_codex=False, access_token=token, allowed_hosts=('testserver',))
        with TestClient(create_app(settings), base_url='https://testserver') as client:
            health = client.get('/api/health')
            assert health.json() == {'status':'ok'}
            assert 'version' not in health.text and 'multi-user' not in health.text
            status = client.get('/api/auth/status')
            assert 'version' not in status.text
            assert '0.19' not in status.text


def test_production_500_page_does_not_leak_internals(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, production=True, multi_user=True, enable_codex=False,
                        access_token='synthetic-production-token-long-enough',
                        allowed_hosts=('testserver',))
    app = create_app(settings)
    def boom():
        raise RuntimeError('internal-detail /srv/acm/private.py line 42')
    monkeypatch.setattr(app.state.identities, 'count', boom)
    client = TestClient(app, base_url='https://testserver', raise_server_exceptions=False)
    response = client.get('/api/auth/status')
    assert response.status_code == 500
    assert response.json() == {'detail': '服务器暂时无法完成请求，请稍后重试'}
    assert 'internal-detail' not in response.text and 'Traceback' not in response.text
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['permissions-policy'] == 'camera=(), microphone=(), geolocation=()'
    assert response.headers['strict-transport-security'] == 'max-age=31536000'
    assert "form-action 'self'" in response.headers['content-security-policy']


def test_presence_payload_contains_only_display_fields(site):
    request, admin, member, *_ = site
    request(admin, 'POST', '/api/presence', {})
    request(member, 'POST', '/api/presence', {})
    body = request(member, 'GET', '/api/presence').json()
    assert body['expires_after'] == 90
    assert body['items'] == [{'username':'kendas','self':False},{'username':'member','self':True}]
    assert all(set(item) == {'username','self'} for item in body['items'])


def test_shipped_version_metadata_is_aligned(tmp_path):
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11
        import tomli as tomllib
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    frontend = json.loads((root/'frontend/package.json').read_text(encoding='utf-8'))['version']
    assert frontend == version
    from acmweb.announcements import RELEASES
    assert RELEASES[0]['id'] == version, '发布的公告版本必须与代码版本一致'
    settings = Settings(data_dir=tmp_path, production=False, multi_user=False, enable_codex=False)
    assert create_app(settings).version == version

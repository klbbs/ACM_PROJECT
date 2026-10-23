"""Local reproductions of the supplied security findings, without external scans."""
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.settings import Settings
from acmweb.users import AUTH_SLOTS, digest
from test_multiuser import site, PASSWORD


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

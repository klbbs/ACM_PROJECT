import asyncio
import json
import pytest
from fastapi.testclient import TestClient

from acmweb.accounts import normalize_handle
from acmweb.app import create_app
from acmweb.settings import Settings
from acmweb.codex_bridge import AIError, CodexBridge
from ojcrawler.config import load


@pytest.fixture
def workspace(tmp_path):
    settings = Settings(data_dir=tmp_path, config_path=tmp_path/'readonly.toml', enable_codex=False)
    settings.config_path.write_text('[crawler]\ninterval=3\n[[accounts]]\nplatform="luogu"\nhandle="123"\ncookie="secret=original"\n')
    with TestClient(create_app(settings)) as client:
        yield client, settings


def test_web_account_cookie_lifecycle_and_readonly_base(workspace):
    client, settings = workspace
    original = settings.config_path.read_bytes()
    assert 'original' not in client.get('/api/accounts').text
    payload = {'items': [{'platform': 'luogu', 'handle': 'https://www.luogu.com.cn/user/123'}]}
    response = client.put('/api/accounts', json=payload)
    assert response.status_code == 200
    assert 'original' not in response.text and response.json()['source'] == 'web'
    path = settings.data_dir/'accounts.web.toml'
    assert load(path).accounts[0].cookie == 'secret=original'
    assert path.stat().st_mode & 0o777 == 0o600
    assert settings.config_path.read_bytes() == original
    assert load(path).interval == 3
    payload['items'][0]['cookie'] = 'secret=replaced'
    assert client.put('/api/accounts', json=payload).status_code == 200
    assert load(path).accounts[0].cookie == 'secret=replaced'
    saved = path.read_bytes()
    payload['items'][0]['cookie'] = 'secret=bad\nvalue'
    result = client.put('/api/accounts', json=payload)
    assert result.status_code == 400 and 'secret=bad' not in result.text
    assert path.read_bytes() == saved
    payload['items'][0]['cookie'] = ''
    assert client.put('/api/accounts', json=payload).json()['items'][0]['has_cookie'] is False
    assert load(path).accounts[0].cookie is None
    assert client.put('/api/accounts', json={'items': []}).json()['configured'] is False
    assert client.post('/api/crawl', json={}).status_code == 400
    assert settings.config_path.read_bytes() == original


def test_first_run_without_toml_and_restart(workspace):
    client, settings = workspace
    settings.config_path.unlink()
    assert client.get('/api/accounts').json()['items'] == []
    result = client.put('/api/accounts', json={'items': [{'platform': 'atcoder', 'handle': 'https://atcoder.jp/users/demo'}]})
    assert result.status_code == 200 and result.json()['items'][0]['handle'] == 'demo'
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get('/api/accounts').json()['configured']


def test_duplicate_secret_errors_and_missing_env_preserve_config(workspace):
    client, settings = workspace
    item = {'platform': 'codeforces', 'handle': 'demo'}
    assert client.put('/api/accounts', json={'items': [item, item]}).status_code == 400
    item['cookie'] = 'SENSITIVE' * 3000
    result = client.put('/api/accounts', json={'items': [item]})
    assert result.status_code == 422 and 'SENSITIVE' not in result.text
    assert not (settings.data_dir/'accounts.web.toml').exists()
    settings.config_path.write_text('[[accounts]]\nplatform="luogu"\nhandle="123"\ncookie_env="ACM_TEST_MISSING_COOKIE"\n')
    assert client.get('/api/accounts').json()['items'][0]['credential_source'] == 'environment'
    assert client.put('/api/accounts', json={'items': [{'platform': 'luogu', 'handle': '123'}]}).status_code == 400


@pytest.mark.parametrize('platform,value,expected', [
    ('codeforces','https://codeforces.com/profile/tourist','tourist'),
    ('nowcoder','https://ac.nowcoder.com/acm/contest/profile/123/practice-index','123'),
    ('nowcoder','https://www.nowcoder.com/profile/123','123'),
    ('atcoder','demo_123','demo_123'),
])
def test_profile_urls(platform,value,expected):
    assert normalize_handle(platform,value) == expected


@pytest.mark.parametrize('value', ['https://evil.example/user/123', 'https://www.luogu.com.cn.evil/user/123', 'http://www.luogu.com.cn/user/123', 'https://www.luogu.com.cn/user/0', 'https://me@www.luogu.com.cn/user/123', '../123'])
def test_invalid_urls(value):
    with pytest.raises(AIError):
        normalize_handle('luogu',value)


def test_server_opt_in_and_device_only(tmp_path):
    token = 'a-long-enough-private-server-token'
    default = Settings(data_dir=tmp_path, production=True, access_token=token)
    default.validate()
    assert not default.enable_codex
    enabled = Settings(data_dir=tmp_path, production=True, server_codex=True, access_token=token)
    with TestClient(create_app(enabled)) as client:
        assert client.post('/api/ai/login',json={}).status_code == 401
        header = {'Authorization': 'Bearer '+token}
        assert client.get('/api/ai/settings',headers=header).json()['codex_device_only']
        result = client.post('/api/ai/login?mode=browser',headers=header,json={})
        assert result.status_code == 400 and '设备码' in result.text
        assert client.app.state.codex.home == tmp_path/'codex'


def test_login_cancel_logout_protocol_and_completion():
    async def run():
        bridge = CodexBridge(device_only=True)
        calls = []
        async def fake(method,params=None,timeout=30):
            calls.append((method,params))
            if method == 'account/login/start':
                return {'loginId':'login-1','type':'chatgptDeviceCode','verificationUrl':'https://auth.openai.com/codex/device','userCode':'TEST-CODE','secret':'never-return'}
            return {}
        bridge.call = fake
        try:
            result = await bridge.login()
            assert result['userCode'] == 'TEST-CODE' and 'secret' not in result
            assert calls[0] == ('account/login/start', {'type':'chatgptDeviceCode'})
            await bridge.cancel_login()
            assert calls[-1] == ('account/login/cancel', {'loginId':'login-1'})
            await bridge.logout()
            assert calls[-1][0] == 'account/logout'
            assert bridge.login_result is None
        finally:
            await bridge.close()
    asyncio.run(run())

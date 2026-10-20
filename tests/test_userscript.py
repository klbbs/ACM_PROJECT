from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.extension import USERSCRIPT_NAME, userscript_source
from acmweb.settings import Settings


def test_userscript_source_and_download_are_consistent_and_credential_free(tmp_path):
    settings = Settings(data_dir=tmp_path, config_path=tmp_path/'account.toml', enable_codex=False)
    with TestClient(create_app(settings)) as client:
        source = client.get('/api/browser-userscript').json()
        download = client.get('/api/browser-userscript/' + USERSCRIPT_NAME)
    assert source == {'filename': USERSCRIPT_NAME, 'version': '1.0.2', 'source': userscript_source()}
    assert '// @version      1.0.2' in source['source']
    assert download.status_code == 200
    assert download.text == source['source']
    assert download.headers['content-type'].startswith('application/javascript')
    assert download.headers['cache-control'] == 'no-store'
    assert USERSCRIPT_NAME in download.headers['content-disposition']
    matches = [line.split()[-1] for line in source['source'].splitlines() if line.startswith('// @match')]
    assert matches == ['https://codeforces.com/*', 'https://www.luogu.com.cn/*',
                       'https://www.nowcoder.com/*', 'https://ac.nowcoder.com/*', 'https://atcoder.jp/*']
    assert '// @updateURL    none' in source['source']
    assert '// @downloadURL  none' in source['source']
    assert '// @run-in       normal-tabs' in source['source']
    for forbidden in ['@require', '@connect', 'GM_xmlhttpRequest', 'document.cookie=', 'fetch(', 'localStorage', 'GM_setValue']:
        assert forbidden not in source['source']


def test_userscript_download_does_not_bypass_site_authentication(tmp_path):
    settings = Settings(data_dir=tmp_path, access_token='synthetic-login-token', enable_codex=False)
    with TestClient(create_app(settings)) as client:
        for path in ['/api/browser-userscript', '/api/browser-userscript/' + USERSCRIPT_NAME]:
            assert client.get(path).status_code == 401
            assert client.get(path + '?token=' + settings.access_token).status_code == 401
        client.post('/api/auth/login', json={'token': settings.access_token})
        assert client.get('/api/browser-userscript').status_code == 200
        assert client.get('/api/browser-userscript/' + USERSCRIPT_NAME).status_code == 200


def test_userscript_multiuser_authentication_and_no_user_specific_source(tmp_path):
    settings = Settings(data_dir=tmp_path, config_path=tmp_path/'account.toml', multi_user=True,
                        access_token='synthetic-setup-token-long-enough', enable_codex=False, allowed_hosts=('testserver',))
    with TestClient(create_app(settings), base_url='https://testserver') as client:
        assert client.get('/api/browser-userscript').status_code == 401
        assert client.get('/api/browser-userscript/' + USERSCRIPT_NAME).status_code == 401
        headers = {'X-ACM-Request': '1', 'Origin': 'https://testserver'}
        assert client.post('/api/auth/setup', headers=headers, json={
            'username': 'kendas', 'password': 'Safe-password-123', 'setup_token': settings.access_token,
        }).status_code == 200
        source = client.get('/api/browser-userscript').json()['source']
        assert source == userscript_source()
        assert settings.access_token not in source
        assert client.get('/api/browser-userscript/' + USERSCRIPT_NAME).text == source

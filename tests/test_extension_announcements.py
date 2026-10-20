import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from acmweb.announcements import RELEASES
from acmweb.app import create_app
from acmweb.extension import ASSETS, build_extension
from acmweb.settings import Settings


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, config_path=tmp_path/'account.toml', enable_codex=False))) as c:
        yield c


@pytest.mark.parametrize('browser', ['chromium', 'firefox'])
def test_download_complete_minimal_extension(client, browser):
    response = client.get(f'/api/browser-extension/{browser}.zip')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/zip'
    assert f'{browser}-1.0.0.zip' in response.headers['content-disposition']
    assert response.headers['cache-control'] == 'no-store'
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == set(ASSETS)
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['permissions'] == ['activeTab', 'cookies', 'clipboardWrite']
        assert not manifest.get('host_permissions')
        assert not manifest.get('content_scripts') and not manifest.get('background')
        assert len(manifest['optional_host_permissions']) == 5
        assert all(h.startswith('https://') and '*.' not in h for h in manifest['optional_host_permissions'])
        assert "connect-src 'none'" in manifest['content_security_policy']['extension_pages']
        assert ('browser_specific_settings' in manifest) == (browser == 'firefox')
        assert '<script type="module" src="popup.js">' in archive.read('popup.html').decode()
    assert response.content == build_extension(browser)
    assert client.get('/api/browser-extension/unknown.zip').status_code == 404


def test_download_requires_login(tmp_path):
    settings = Settings(data_dir=tmp_path, access_token='private-extension-test-token', enable_codex=False)
    with TestClient(create_app(settings)) as c:
        for path in ['/api/browser-extension/chromium.zip', '/api/announcements']:
            assert c.get(path).status_code == 401
        c.post('/api/auth/login', json={'token':settings.access_token})
        assert c.get('/api/browser-extension/chromium.zip').status_code == 200


def test_announcement_read_validation_merge_and_persistence(client):
    response = client.get('/api/announcements').json()
    ids = [r['id'] for r in RELEASES]
    assert response['unread_ids'] == ids
    assert response['items'][0]['id'] == client.get('/api/health').json()['version']
    assert client.post('/api/announcements/read', json={'ids':['999.0.0']}).status_code == 422
    assert client.post('/api/announcements/read', json={'ids':[]}).status_code == 422
    assert client.post('/api/announcements/read', json={'ids':[ids[0]],'user_id':'another-user'}).status_code == 422
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda rid: client.post('/api/announcements/read',json={'ids':[rid]}).status_code, ids[:2]))
    assert results == [200,200]
    assert client.get('/api/announcements').json()['unread_ids'] == ids[2:]
    assert len(client.get('/api/announcements').json()['items']) == len(ids)
    # A new app/process reads the same owner database without requiring browser storage.
    settings = Settings(data_dir=client.app.state.database.path.parent, config_path=client.app.state.database.path.parent/'account.toml', enable_codex=False)
    with TestClient(create_app(settings)) as other:
        assert other.get('/api/announcements').json()['unread_ids'] == ids[2:]


def test_multiuser_announcement_isolation_and_download(tmp_path):
    settings = Settings(data_dir=tmp_path, config_path=tmp_path/'account.toml', multi_user=True,
                        access_token='initialization-secret-for-extension',enable_codex=False,allowed_hosts=('testserver',))
    with TestClient(create_app(settings), base_url='https://testserver') as c:
        headers = {'X-ACM-Request':'1','Origin':'https://testserver'}
        assert c.get('/api/browser-extension/firefox.zip').status_code == 401
        assert c.post('/api/auth/setup',headers=headers,json={'username':'kendas','password':'Safe-password-123','setup_token':settings.access_token}).status_code == 200
        csrf = c.get('/api/auth/status').json()['csrf_token']
        auth = {**headers,'X-CSRF-Token':csrf}
        assert c.get('/api/browser-extension/firefox.zip').status_code == 200
        ids = c.get('/api/announcements').json()['unread_ids']
        assert c.post('/api/announcements/read',headers=headers,json={'ids':ids}).status_code == 403
        assert c.post('/api/announcements/read',headers=auth,json={'ids':ids}).status_code == 200
        invite = c.post('/api/admin/invitations',headers=auth,json={}).json()['invitation']
        assert c.post('/api/auth/logout',headers=auth,json={}).status_code == 200
        assert c.post('/api/auth/register',headers=headers,json={'username':'member','password':'Member-password-123','invitation':invite}).status_code == 200
        assert c.get('/api/announcements').json()['unread_ids'] == ids
        assert c.get('/api/browser-extension/chromium.zip').status_code == 200

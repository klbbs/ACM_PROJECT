"""CI-only smoke in an ephemeral, read-only container with tmpfs data."""
import os
from fastapi.testclient import TestClient
from acmweb.app import create_app


with TestClient(create_app(),base_url='https://localhost') as client:
    assert client.get('/api/health').json()['mode'] == 'multi-user'
    response = client.post('/api/auth/setup',headers={'X-ACM-Request':'1'},json={
        'username':'kendas','password':'Container-only-test-password',
        'setup_token':os.environ['ACM_ACCESS_TOKEN'],
    })
    assert response.status_code == 200,response.text
    status = client.get('/api/auth/status').json()
    headers = {'X-ACM-Request':'1','X-CSRF-Token':status['csrf_token']}
    assert client.get('/api/accounts').json()['items'] == []
    assert client.get('/api/workbench').json()['profile']['local_snapshots'] == 0
    draft = {'code':'// container smoke only','language':'cpp17','revision':0}
    assert client.put('/api/workbench/problem/4-A/draft',headers=headers,json=draft).status_code == 200
    snapshot = client.post('/api/workbench/problem/4-A/snapshot',headers=headers,json={**draft,'revision':1})
    assert snapshot.status_code == 200,snapshot.text
    assert client.get('/api/workbench/problem/4-A').json()['attempts'][0]['verdict'] == 'UNLINKED'
    if os.getenv('ACM_ENABLE_SERVER_CODEX') == 'true':
        account = client.get('/api/ai/account').json()
        assert account.get('available') and not account.get('connected'), account
    assert client.post('/api/shared',headers=headers,json={'title':'CI draft','body':'isolated test'}).status_code == 200
    assert client.get('/api/shared').json()['total'] == 1
print('Read-only multi-user container smoke passed')

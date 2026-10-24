from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.settings import Settings


def test_connection_preflight_rejects_incomplete_configuration_without_creating_job(tmp_path):
    settings = Settings(data_dir=tmp_path, config_path=tmp_path/'missing.toml',
                        enable_codex=False, allow_env_ai_key=False,
                        access_token='test-private-access-token-long')
    app = create_app(settings)
    headers = {'Authorization': 'Bearer ' + settings.access_token}
    with TestClient(app) as client:
        response = client.post('/api/ai/test', json={}, headers=headers)
        assert response.status_code == 400
        assert response.json()['detail'] == '请先保存默认模型 ID'
        assert app.state.jobs.list() == []
        response = client.put('/api/ai/settings', json={'model':'text-model'}, headers=headers)
        assert response.status_code == 200
        response = client.post('/api/ai/test', json={}, headers=headers)
        assert response.status_code == 400
        assert '配置 Key' in response.json()['detail']
        assert app.state.jobs.list() == []
        reasons = {r['details'].get('reason') for r in app.state.event_log.listing()['items']}
        assert {'ai_model_missing', 'ai_key_missing'} <= reasons

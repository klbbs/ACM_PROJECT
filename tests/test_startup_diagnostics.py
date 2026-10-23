import logging
from types import SimpleNamespace

from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.settings import Settings
from acmweb.startup_diagnostics import _single_findings, emit, report_multiuser


class AccountConfig:
    def __init__(self, items):
        self.items = items

    def public(self):
        return {'items': self.items}


class AIConfig:
    def __init__(self, *, key=False, model=''):
        self.key, self.model = key, model

    def public_config(self):
        return {'has_key': self.key, 'model': self.model}


def account(platform, cookie_status='missing', handle='private-handle'):
    return {'platform': platform, 'handle': handle,
            'cookie_info': {'status': cookie_status}}


def settings(tmp_path, **changes):
    return SimpleNamespace(static_dir=tmp_path, server_codex=False,
                           codex_binary='acm-no-such-codex-cli', **changes)


def test_unconfigured_personal_features_are_reported_without_secrets(tmp_path, caplog):
    findings = _single_findings(settings(tmp_path), AccountConfig([]), AIConfig(),
                                SimpleNamespace(enabled=False),
                                SimpleNamespace(enabled=False, binary='acm-no-such-codex-cli'))
    features = {item['feature']: item for item in findings}
    assert features['平台账号']['level'] == 'warning'
    assert features['AI 分析与选题']['level'] == 'warning'
    assert features['代码自测']['level'] == 'warning'
    assert features['网页前端']['level'] == 'warning'
    emit(findings, mode='个人')
    output = caplog.text
    assert 'private-handle' not in output
    assert 'Cookie 值' not in output
    assert 'API Key 值' not in output


def test_personal_app_emits_diagnostics_when_service_starts(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger='acmweb.startup')
    config = Settings(data_dir=tmp_path / 'data', config_path=tmp_path / 'missing.toml',
                      static_dir=tmp_path / 'missing-dist', access_token='local-test-token',
                      enable_codex=False, allow_env_ai_key=False)
    with TestClient(create_app(config), base_url='http://testserver') as client:
        assert client.get('/api/health').status_code == 200
    assert '启动配置巡检（个人）' in caplog.text
    assert '未配置 ACM_SELFTEST_URL' in caplog.text


def test_saved_cookie_is_not_claimed_as_verified_login(tmp_path):
    findings = _single_findings(
        settings(tmp_path), AccountConfig([account('codeforces', 'valid')]),
        AIConfig(key=True, model='configured-model'), SimpleNamespace(enabled=True),
        SimpleNamespace(enabled=False, binary='acm-no-such-codex-cli'))
    details = '\n'.join(item['detail'] for item in findings)
    assert '启动时未连接平台验证会话' in details
    assert '启动时未发送连接测试' in details
    assert '已配置隔离运行服务地址' in details
    assert 'private-handle' not in details


def test_multiuser_report_aggregates_without_user_names_or_credentials(tmp_path, caplog):
    child = SimpleNamespace(state=SimpleNamespace(
        account_config=AccountConfig([account('codeforces', 'valid', 'secret-user')]),
        ai=AIConfig(key=True, model='secret-model')))
    workspaces = SimpleNamespace(apps={'secret-uid': (child, object())})
    identities = SimpleNamespace(count=lambda: 3)
    findings = report_multiuser(settings(tmp_path), identities, workspaces)
    details = '\n'.join(item['detail'] for item in findings)
    assert '1/1 个已加载用户空间配置了 Key 和模型' in details
    assert 'secret-user' not in details
    assert 'secret-model' not in details
    assert 'secret-uid' not in caplog.text
    assert 'secret-user' not in caplog.text

import json

import pytest
from fastapi.testclient import TestClient

from acmweb.ai import AIProvider, ProviderConfig
from acmweb.app import create_app
from acmweb.codex_bridge import AIError
from acmweb.database import Database
from acmweb.settings import Settings


def provider(data_dir, enable_codex=False):
    return AIProvider(Database(data_dir / 'accounts.sqlite3'),
                      Settings(data_dir=data_dir, allow_env_ai_key=False, enable_codex=enable_codex),
                      None)


def test_the_saved_api_model_is_the_default_for_every_feature(tmp_path):
    ai = provider(tmp_path)
    saved = ai.save(ProviderConfig(model='fixture-model', api_key='secret-fixture-key'))
    assert saved['channel'] == 'api'
    assert saved['active_model'] == 'fixture-model'
    assert saved['ready'] is True
    assert saved['has_key'] is True
    assert 'secret-fixture-key' not in json.dumps(saved)
    assert ai.selection()['model'] == 'fixture-model'
    assert ai.active_model() == 'fixture-model'


def test_clearing_the_key_or_the_model_updates_readiness_and_the_active_model(tmp_path):
    ai = provider(tmp_path)
    ai.save(ProviderConfig(model='fixture-model', api_key='secret-fixture-key'))

    cleared = ai.save(ProviderConfig(model='fixture-model', api_key=''))
    assert cleared['has_key'] is False
    assert cleared['ready'] is False
    assert cleared['active_model'] == 'fixture-model'

    ai.save(ProviderConfig(model='fixture-model', api_key='secret-fixture-key'))
    blank = ai.save(ProviderConfig(model='', api_key='secret-fixture-key'))
    assert blank['ready'] is False
    assert blank['active_model'] == ''
    assert blank['has_key'] is True

    changed = ai.save(ProviderConfig(model='next-model', api_key='secret-fixture-key'))
    assert changed['active_model'] == 'next-model'
    assert changed['ready'] is True


def test_the_official_codex_channel_is_an_optional_default_chosen_once(tmp_path):
    ai = provider(tmp_path, enable_codex=True)
    ai.save(ProviderConfig(model='fixture-model', api_key='secret-fixture-key'))

    selected = ai.save(ProviderConfig(model='fixture-model', channel='codex',
                                      codex_model='fixture-codex'))
    assert selected['channel'] == 'codex'
    assert selected['active_model'] == 'fixture-codex'
    assert selected['ready'] is True
    # The API key survives so switching back does not need it typed again.
    assert selected['has_key'] is True

    without_model = ai.save(ProviderConfig(model='fixture-model', channel='codex',
                                           codex_model=''))
    assert without_model['channel'] == 'codex'
    assert without_model['active_model'] == ''
    assert without_model['ready'] is False

    back = ai.save(ProviderConfig(model='fixture-model', channel='api'))
    assert back['channel'] == 'api'
    assert back['active_model'] == 'fixture-model'
    assert back['ready'] is True


def test_selecting_the_official_channel_is_rejected_when_the_server_disables_it(tmp_path):
    ai = provider(tmp_path)
    with pytest.raises(AIError, match='官方 Codex 通道未启用'):
        ai.save(ProviderConfig(model='fixture-model', channel='codex',
                               codex_model='fixture-codex'))


def test_each_workspace_keeps_its_own_default_configuration(tmp_path):
    alice = tmp_path / 'alice'
    bob = tmp_path / 'bob'
    for directory in (alice, bob):
        directory.mkdir()
    first, second = provider(alice), provider(bob)

    first.save(ProviderConfig(model='alice-model', api_key='alice-key'))
    second.save(ProviderConfig(model='bob-model', api_key='bob-key', channel='api'))

    assert first.public_config()['active_model'] == 'alice-model'
    assert second.public_config()['active_model'] == 'bob-model'


def test_settings_endpoint_is_the_single_source_of_readiness_and_current_model(tmp_path):
    settings = Settings(data_dir=tmp_path, config_path=tmp_path / 'missing.toml',
                        enable_codex=False, allow_env_ai_key=False,
                        access_token='test-private-access-token-long')
    app = create_app(settings)
    headers = {'Authorization': 'Bearer ' + settings.access_token}
    with TestClient(app) as client:
        initial = client.get('/api/ai/settings', headers=headers)
        assert initial.status_code == 200
        assert initial.json()['channel'] == 'api'
        assert initial.json()['active_model'] == ''
        assert initial.json()['ready'] is False

        saved = client.put('/api/ai/settings', headers=headers,
                           json={'model': 'fixture-model', 'api_key': 'secret-fixture-key'})
        assert saved.status_code == 200
        assert saved.json()['active_model'] == 'fixture-model'
        assert saved.json()['ready'] is True
        assert 'secret-fixture-key' not in saved.text

        changed = client.put('/api/ai/settings', headers=headers, json={'model': 'next-model'})
        assert changed.json()['active_model'] == 'next-model'
        assert changed.json()['ready'] is True

        cleared = client.put('/api/ai/settings', headers=headers,
                             json={'model': 'next-model', 'api_key': ''})
        assert cleared.json()['has_key'] is False
        assert cleared.json()['ready'] is False
        assert cleared.json()['active_model'] == 'next-model'

        refused = client.put('/api/ai/settings', headers=headers,
                             json={'model': 'next-model', 'channel': 'codex',
                                   'codex_model': 'fixture-codex'})
        assert refused.status_code == 400
        assert '官方 Codex 通道未启用' in refused.json()['detail']

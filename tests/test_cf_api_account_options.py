"""Synthetic configuration tests: never access Codeforces or real credentials."""
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from acmweb.accounts import AccountInput, AccountList, Accounts
from acmweb.app import create_app
from acmweb.codex_bridge import AIError
from acmweb.settings import Settings
from ojcrawler.config import Account, load


KEY = '0123456789abcdef' * 2
SECRET = 'fedcba9876543210' * 2


@pytest.fixture
def accounts(tmp_path):
    return Accounts(Settings(data_dir=tmp_path, config_path=tmp_path / 'absent.toml', enable_codex=False))


def save(accounts, **values):
    return accounts.save(AccountList(items=[{'platform': 'codeforces', 'handle': 'demo', **values}]))


@pytest.mark.parametrize('length', [32, 40])
def test_private_pair_survives_cookie_and_verified_account_edits(accounts, length):
    key, secret = ('0123456789abcdef' * 3)[:length], ('fedcba9876543210' * 3)[:length]
    result = save(accounts, api_key=key, api_secret=secret, cookie='sid=synthetic')
    assert result['items'][0]['has_api_key'] is True
    assert KEY not in json.dumps(result) and SECRET not in json.dumps(result)
    assert accounts.path.stat().st_mode & 0o777 == 0o600
    loaded = load(accounts.path).accounts[0]
    assert loaded.api_key == key and loaded.api_secret == secret
    assert KEY not in repr(loaded) and SECRET not in repr(loaded)
    save(accounts, api_key=None, api_secret=None, cookie='sid=replaced')
    accounts.bind_verified_account('codeforces', 'demo', 'sid=verified', accounts.revision())
    stored = accounts.document()['accounts'][0]
    assert stored['api_key'] == key and stored['api_secret'] == secret
    assert stored['cookie'] == 'sid=verified'
    assert save(accounts, api_key='', api_secret='')['items'][0]['has_api_key'] is False
    assert load(accounts.path).accounts[0].api_key is None
    assert load(accounts.path).accounts[0].cookie == 'sid=verified'


@pytest.mark.parametrize('values', [
    {'api_key': KEY}, {'api_secret': SECRET}, {'api_key': '', 'api_secret': SECRET},
    {'api_key': 'x' * 32, 'api_secret': SECRET}, {'api_key': KEY, 'api_secret': 'a' * 31},
    {'api_key': KEY, 'api_secret': 'a' * 33}, {'api_key': ' ' + KEY, 'api_secret': SECRET},
    {'api_key': 'a' * 39, 'api_secret': 'b' * 40},
    {'api_key': 'a' * 41, 'api_secret': 'b' * 40},
])
def test_invalid_pairs_preserve_existing_private_file(accounts, values):
    save(accounts, api_key=KEY, api_secret=SECRET)
    previous = accounts.path.read_bytes()
    with pytest.raises((AIError, ValidationError)):
        save(accounts, **values)
    assert accounts.path.read_bytes() == previous


@pytest.mark.parametrize('platform,handle', [('luogu', '123'), ('nowcoder', '123'), ('atcoder', 'demo')])
def test_other_platforms_reject_api_credentials(accounts, platform, handle):
    with pytest.raises(AIError, match='只有 Codeforces'):
        accounts.save(AccountList(items=[dict(platform=platform, handle=handle, api_key=KEY, api_secret=SECRET)]))
    assert not accounts.path.exists()


def test_model_repr_does_not_include_private_pair():
    assert KEY not in repr(AccountInput(platform='codeforces', handle='demo', api_key=KEY, api_secret=SECRET))
    assert SECRET not in repr(Account('codeforces', 'demo', api_key=KEY, api_secret=SECRET))


def test_isolated_user_config_never_inherits_another_users_pair(tmp_path):
    users = []
    for name in ('first', 'second'):
        directory = tmp_path / name
        directory.mkdir()
        users.append(Accounts(Settings(data_dir=directory, config_path=directory / 'absent.toml', isolated=True)))
    save(users[0], api_key=KEY, api_secret=SECRET)
    save(users[1])
    assert users[0].public()['items'][0]['has_api_key']
    assert not users[1].public()['items'][0]['has_api_key']
    assert 'api_secret' not in users[1].document()['accounts'][0]


def test_http_validation_does_not_echo_secret_and_preserves_saved_pair(accounts):
    with TestClient(create_app(accounts.settings)) as client:
        item = dict(platform='codeforces', handle='demo', api_key=KEY, api_secret=SECRET)
        response = client.put('/api/accounts', json={'items': [item]})
        assert response.status_code == 200 and response.json()['items'][0]['has_api_key']
        response = client.put('/api/accounts', json={'items': [{**item, 'api_secret': 'SENSITIVE!' * 40}]})
        assert response.status_code == 422 and 'SENSITIVE!' not in response.text
        response = client.put('/api/accounts', json={'items': [{**item, 'api_secret': 'SENSITIVE'}]})
        assert response.status_code == 400 and 'SENSITIVE' not in response.text
        assert accounts.document()['accounts'][0]['api_secret'] == SECRET
        response = client.get('/api/accounts')
        assert KEY not in response.text and SECRET not in response.text


def test_cli_old_account_config_remains_compatible_and_private_pair_validates(tmp_path):
    path = tmp_path / 'account.toml'
    path.write_text('[[accounts]]\nplatform="codeforces"\nhandle="demo"\n')
    assert load(path).accounts[0].api_key is None
    path.write_text(path.read_text() + f'api_key="{KEY}"\n')
    with pytest.raises(ValueError, match='必须同时填写'):
        load(path)
    path.write_text(path.read_text() + f'api_secret="{SECRET}"\n')
    assert load(path).accounts[0].api_key == KEY

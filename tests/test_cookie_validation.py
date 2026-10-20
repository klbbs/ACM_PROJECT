import pytest
from acmweb.accounts import Accounts, AccountList
from acmweb.cookie_validation import valid_cookie
from acmweb.credential_check import CredentialChecks
from acmweb.codex_bridge import AIError
from acmweb.settings import Settings

@pytest.mark.parametrize('value', ['session=abc==; another=value;', 'a=; b=value', 'x=abc.def-123_456', 'session="abc=="; x=value', 'Hm_lvt_test=1760920000,1761006400; session=abc', 'label="browser value"; session=abc', 'session=abc...def', 'session=...' ])
def test_complete_request_cookie(value):
    assert valid_cookie(value)

@pytest.mark.parametrize('value', ['session=abc…', 'Set-Cookie: session=abc',
                                   'a=b\r\nHost: attacker', 'no-equals', 'a=b; malformed', 'a=b;;', 'a=b\t', '会话=abc'])
def test_invalid_cookie_is_rejected_without_network_or_echo(tmp_path, monkeypatch, value):
    settings = Settings(data_dir=tmp_path,config_path=tmp_path/'missing.toml')
    accounts = Accounts(settings)
    accounts.save(AccountList.model_validate({'items':[{'platform':'codeforces','handle':'demo','cookie':'session=KEEP-SECRET'}]}))
    original = accounts.path.read_bytes()
    with pytest.raises(AIError) as error:
        accounts.save(AccountList.model_validate({'items':[{'platform':'codeforces','handle':'demo','cookie':value}]}))
    assert value not in str(error.value)
    assert accounts.path.read_bytes() == original
    # Simulate a legacy configuration without rewriting its invalid credential.
    monkeypatch.setattr(accounts,'document',lambda:{'accounts':[{'platform':'codeforces','handle':'demo','cookie':value}]})
    monkeypatch.setattr('acmweb.credential_check.requests.Session',lambda:pytest.fail('must not access network'))
    result = CredentialChecks(accounts).check('codeforces','demo')
    assert result['status']=='invalid' and result['login_verified'] is False
    assert value not in str(result) and 'KEEP-SECRET' not in str(result)
    assert accounts.path.read_bytes() == original


def test_single_request_header_is_normalized_on_save_and_not_returned(tmp_path):
    accounts = Accounts(Settings(data_dir=tmp_path, config_path=tmp_path/'missing.toml'))
    result = accounts.save(AccountList.model_validate({'items': [{'platform': 'codeforces', 'handle': 'demo',
                                                                 'cookie': ' Cookie: JSESSIONID=synthetic...session; x=value; '}]}))
    item = result['items'][0]
    assert item['cookie_info']['status'] == 'valid' and item['cookie_info']['pair_count'] == 2
    assert 'synthetic' not in str(result) and 'JSESSIONID' not in str(result)
    assert accounts.document()['accounts'][0]['cookie'] == 'JSESSIONID=synthetic...session; x=value;'
    again = accounts.save(AccountList.model_validate({'items': [{'platform': 'codeforces', 'handle': 'demo'}]}))
    assert again['items'][0]['cookie_info'] == item['cookie_info']
    assert accounts.document()['accounts'][0]['cookie'] == 'JSESSIONID=synthetic...session; x=value;'


def test_legacy_invalid_credentials_are_visible_without_leaking_or_losing_other_accounts(tmp_path, monkeypatch):
    config = tmp_path/'account.toml'
    config.write_text('[[accounts]]\nplatform="codeforces"\nhandle="demo"\ncookie="no-equals-secret"\n')
    accounts = Accounts(Settings(data_dir=tmp_path, config_path=config))
    info = accounts.public()['items'][0]['cookie_info']
    assert info['status'] == 'invalid' and info['code'] == 'missing_equals'
    assert 'no-equals-secret' not in str(info)
    result = accounts.save(AccountList.model_validate({'items': [{'platform': 'codeforces', 'handle': 'demo'},
                                                                 {'platform': 'atcoder', 'handle': 'second'}]}))
    assert result['items'][0]['cookie_info'] == info
    monkeypatch.setattr('acmweb.credential_check.requests.Session', lambda: pytest.fail('invalid syntax must not reach network'))
    checked = CredentialChecks(accounts).check('codeforces', 'demo')
    assert checked['cookie_info'] == info and checked['status'] == 'invalid'
    assert '第 1 个字段缺少等号' in checked['message']
    assert 'no-equals-secret' not in str(result) + str(checked)
    assert accounts.document()['accounts'][0]['cookie'] == 'no-equals-secret'


def test_legacy_single_prefix_is_repaired_by_explicit_save(tmp_path):
    config = tmp_path/'account.toml'
    config.write_text('[[accounts]]\nplatform="codeforces"\nhandle="demo"\ncookie="Cookie: session=synthetic-secret"\n')
    accounts = Accounts(Settings(data_dir=tmp_path, config_path=config))
    assert accounts.public()['items'][0]['cookie_info']['code'] == 'cookie_prefix'
    result = accounts.save(AccountList.model_validate({'items': [{'platform': 'codeforces', 'handle': 'demo'}]}))
    assert result['items'][0]['cookie_info']['status'] == 'valid'
    assert accounts.document()['accounts'][0]['cookie'] == 'session=synthetic-secret'
    assert 'Cookie: session=' in config.read_text()  # read-only base unchanged


def test_login_verification_cache_matches_the_exact_saved_credential(tmp_path):
    accounts = Accounts(Settings(data_dir=tmp_path, config_path=tmp_path/'missing.toml'))
    payload = {'items': [{'platform': 'atcoder', 'handle': 'demo', 'cookie': 'session=synthetic-new'}]}
    accounts.save(AccountList.model_validate(payload))
    checks = CredentialChecks(accounts)
    assert checks.remember_verified('atcoder', 'demo', 'session=synthetic-old') is False
    assert checks.statuses() == {'items': []}
    assert checks.remember_verified('atcoder', 'demo', 'session=synthetic-new') is True
    assert checks.statuses()['items'][0]['login_verified'] is True
    assert checks.remember_verified('atcoder', 'missing', 'session=synthetic-new') is False
    payload['items'][0]['cookie'] = 'session=synthetic-changed'
    accounts.save(AccountList.model_validate(payload))
    assert checks.statuses() == {'items': []}

import pytest
from acmweb.accounts import Accounts, AccountList
from acmweb.cookie_validation import valid_cookie
from acmweb.credential_check import CredentialChecks
from acmweb.codex_bridge import AIError
from acmweb.settings import Settings

@pytest.mark.parametrize('value', ['session=abc==; another=value;', 'a=; b=value', 'x=abc.def-123_456', 'session="abc=="; x=value'])
def test_complete_request_cookie(value):
    assert valid_cookie(value)

@pytest.mark.parametrize('value', ['session=abc…', 'session=abc...', 'Cookie: session=abc', 'Set-Cookie: session=abc',
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

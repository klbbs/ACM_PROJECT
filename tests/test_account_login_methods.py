from acmweb.accounts import Accounts,AccountList
from acmweb.settings import Settings


def test_qr_session_is_complete_without_manual_cookie_import_and_survives_edits(tmp_path):
    accounts=Accounts(Settings(data_dir=tmp_path,config_path=tmp_path/'absent.toml',enable_codex=False))
    result=accounts.bind_verified_account('nowcoder','123','sid=synthetic-qr-session',accounts.revision(),login_method='qr')
    assert result['items'][0]['has_cookie'] and result['items'][0]['login_method']=='qr'
    assert 'synthetic-qr-session' not in str(result)
    accounts.save(AccountList(items=[{'platform':'nowcoder','handle':'123','cookie':None}]))
    assert accounts.public()['items'][0]['login_method']=='qr'
    assert accounts.document()['accounts'][0]['cookie']=='sid=synthetic-qr-session'
    restarted=Accounts(accounts.settings)
    assert restarted.public()['items'][0]['login_method']=='qr'
    # Explicitly replacing the session selects the alternative method once.
    accounts.save(AccountList(items=[{'platform':'nowcoder','handle':'123','cookie':'sid=synthetic-browser-session'}]))
    assert accounts.public()['items'][0]['login_method']=='cookie'
    accounts.save(AccountList(items=[{'platform':'nowcoder','handle':'123','cookie':''}]))
    assert accounts.public()['items'][0]['login_method']=='public'
    assert not accounts.public()['items'][0]['has_cookie']

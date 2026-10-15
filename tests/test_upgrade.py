import importlib.util
import sqlite3
from pathlib import Path

from acmweb.database import Database

spec = importlib.util.spec_from_file_location('upgrade_workspace', Path(__file__).resolve().parents[1] / 'scripts/upgrade_workspace.py')
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)


def test_upgrade_preserves_history_preferences_and_is_repeatable(tmp_path):
    source, target = tmp_path / 'source.sqlite3', tmp_path / 'target.sqlite3'
    Database(source).set('training', {'minimum': 1400})
    Database(target).set('training', {'minimum': 800})
    with sqlite3.connect(target) as db:
        db.execute("INSERT INTO runs VALUES('original', 1, '{}')")
    with sqlite3.connect(source) as db:
        db.execute("INSERT INTO runs VALUES('not-for-import', 2, '{}')")
        db.execute("INSERT INTO ai_reports VALUES('report', 1, 'analysis', 'api', 'model', '{}')")
    for _ in range(2):
        backup = upgrade.merge_database(source, target)
        with sqlite3.connect(backup) as db:
            assert db.execute('SELECT id FROM runs').fetchall() == [('original',)]
        with sqlite3.connect(target) as db:
            assert db.execute('SELECT id FROM runs').fetchall() == [('original',)]
            assert db.execute('SELECT COUNT(*) FROM ai_reports').fetchone()[0] == 1
        assert Database(target).get('training') == {'minimum': 800}


def test_config_upgrade_preserves_credentials_and_account_options(tmp_path):
    config = tmp_path / 'account.toml'
    config.write_text('[crawler]\ndatabase = "old.sqlite3"\ninterval = 3\n[[accounts]]\nhandle = "test"\ncookie = "test-secret"\n')
    upgrade.update_config(config)
    first = config.read_text()
    upgrade.update_config(config)
    assert config.read_text() == first
    assert 'database = "data/account-history/accounts.sqlite3"' in first
    assert 'output_dir = "data/account-history/runs"' in first
    assert 'cookie = "test-secret"' in first and 'interval = 3' in first
    assert config.stat().st_mode & 0o777 == 0o600

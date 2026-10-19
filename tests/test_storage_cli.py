import json

import pytest

from ojcrawler.cli import main, timestamp
from ojcrawler.config import load
from ojcrawler.models import Collection, Submission
from ojcrawler.storage import Store, run_lock


def test_upsert_rejudging_partial_checkpoint_and_account_isolation(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    result = Collection("codeforces", "a", submissions=[Submission("1", "1-A", 100, "WA")], complete=True)
    store.save("run1", 200, result)
    assert store.watermark("codeforces", "a") == 100
    result.submissions[0].verdict = "AC"
    result.submissions.append(Submission("2", "1-B", 300, "WA"))
    result.complete = False
    store.save("run2", 400, result)
    assert store.watermark("codeforces", "a") == 100
    assert len(store.export()) == 2 and store.export()[0]["verdict"] == "AC"
    result.account = "b"
    result.complete = True
    store.save("run3", 401, result)
    assert len(store.export(account="b")) == 2 and len(store.export()) == 4
    assert len(store.export(since=200)) == 2
    store.close()


def test_metadata_survives_incremental_replay(tmp_path):
    store = Store(tmp_path / "db")
    result = Collection("atcoder", "a", submissions=[Submission("1", "abc_1", 100, "AC", problem={"title": "Title", "difficulty": 400})], complete=True)
    store.save("r1", 101, result)
    result.submissions[0].problem = {"title": None, "difficulty": None, "tags": []}
    store.save("r2", 102, result)
    assert store.export()[0]["problem"]["difficulty"] == 400
    store.close()


@pytest.mark.parametrize('platform,expected_accounts', [('codeforces', ['demo']), ('atcoder', ['DEMO', 'demo'])])
def test_account_casing_reuses_existing_keys_only_for_codeforces(tmp_path, platform, expected_accounts):
    store = Store(tmp_path / 'db')
    try:
        store.save('first', 101, Collection(platform, 'demo', submissions=[
            Submission('1', '1-A', 100, 'WA', problem={'title': 'Preserved title'})], complete=True))
        store.save('replayed', 201, Collection(platform, 'DEMO', submissions=[
            Submission('1', '1-A', 100, 'AC'), Submission('2', '1-B', 200, 'WA')], complete=True))
        rows = store.export()
        assert sorted({row['account'] for row in rows}) == expected_accounts
        if platform == 'codeforces':
            assert len(rows) == 2
            assert rows[0]['verdict'] == 'AC' and rows[0]['problem']['title'] == 'Preserved title'
            assert store.watermark(platform, 'DEMO') == store.watermark(platform, 'demo') == 200
            assert store.db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0] == 1
        else:
            assert len(rows) == 3  # Other platforms retain their original case-sensitive semantics.
    finally:
        store.close()


def test_casing_reuse_does_not_merge_or_delete_preexisting_duplicate_history(tmp_path):
    store = Store(tmp_path / 'db')
    try:
        store.save('first', 101, Collection('codeforces', 'demo', submissions=[Submission('1', '1-A', 100, 'WA')], complete=True))
        with store.db:
            store.db.execute("INSERT INTO submissions SELECT platform,'DEMO',id,problem_id,submitted_at,verdict,data,updated_at FROM submissions")
        keys_before = store.db.execute('SELECT platform,account,id FROM submissions ORDER BY account').fetchall()
        store.save('next', 201, Collection('codeforces', 'DEMO', submissions=[Submission('1', '1-A', 100, 'AC')], complete=True))
        assert store.db.execute('SELECT platform,account,id FROM submissions ORDER BY account').fetchall() == keys_before
        assert len(store.export()) == 2
    finally:
        store.close()


def test_cli_failure_isolation_and_exit_status(tmp_path, monkeypatch):
    from ojcrawler import cli
    class Adapter:
        def __init__(self, client, account, **kwargs):
            self.account = account
        def collect(self, since):
            return Collection("codeforces", self.account,
                submissions=[] if self.account == "bad" else [Submission("1", "1-A", 100, "AC")],
                complete=self.account != "bad", error="blocked" if self.account == "bad" else None)
    monkeypatch.setitem(cli.ADAPTERS, "codeforces", Adapter)
    path = config(tmp_path, '[[accounts]]\nplatform="codeforces"\nhandle="bad"\n[[accounts]]\nplatform="codeforces"\nhandle="good"')
    assert main(["crawl", "--config", str(path)]) == 1
    snapshot = json.loads(next((tmp_path / "data/runs").glob("*.json")).read_text())
    assert not snapshot["complete"] and snapshot["accounts"][1]["complete"]
    store = Store(tmp_path / "data/accounts.sqlite3")
    assert store.watermark("codeforces", "bad") == 0
    assert store.watermark("codeforces", "good") == 100
    store.close()


def test_lock_prevents_overlapping_runs(tmp_path):
    with run_lock(tmp_path / "db"):
        with pytest.raises(RuntimeError):
            with run_lock(tmp_path / "db"):
                pass


def config(tmp_path, contents):
    path = tmp_path / "accounts.toml"
    path.write_text(contents)
    return path


def test_config_paths_and_secret_checks(tmp_path, monkeypatch):
    path = config(tmp_path, '[[accounts]]\nplatform="luogu"\nhandle="123"\ncookie_env="TEST_OJ_SECRET"\n')
    monkeypatch.delenv("TEST_OJ_SECRET", raising=False)
    with pytest.raises(ValueError, match="未设置"):
        load(path)
    monkeypatch.setenv("TEST_OJ_SECRET", "secret")
    cfg = load(path)
    assert cfg.database == tmp_path / "data/accounts.sqlite3"
    assert main(["check", "--config", str(path)]) == 0


def test_literal_cookie_configuration_is_not_disclosed(tmp_path, capsys):
    secret = "session=test-secret-cookie"
    path = config(tmp_path, '[[accounts]]\nplatform="atcoder"\nhandle="a"\ncookie="' + secret + '"\n')
    cfg = load(path)
    assert cfg.accounts[0].cookie == secret
    assert secret not in repr(cfg.accounts[0])
    assert main(["check", "--config", str(path)]) == 0
    assert secret not in capsys.readouterr().out


def test_cookie_and_environment_are_mutually_exclusive(tmp_path):
    path = config(tmp_path, '[[accounts]]\nplatform="atcoder"\nhandle="a"\ncookie="session=secret"\ncookie_env="SESSION"\n')
    with pytest.raises(ValueError, match="只能选择一种"):
        load(path)


@pytest.mark.parametrize("contents", [
    '[crawler]\ninterval=0\n[[accounts]]\nplatform="codeforces"\nhandle="a"',
    '[[accounts]]\nplatform="luogu"\nhandle="a"',
    '[[accounts]]\nplatform="unknown"\nhandle="a"',
    '[[accounts]]\nplatform="atcoder"\nhandle="a"\n[[accounts]]\nplatform="atcoder"\nhandle="a"',
])
def test_invalid_configuration(tmp_path, contents):
    with pytest.raises(ValueError):
        load(config(tmp_path, contents))


def test_export_cli(tmp_path):
    db = tmp_path / "db"
    store = Store(db)
    store.save("r", 200, Collection("codeforces", "a", submissions=[Submission("1", "1-A", 100, "AC")], complete=True))
    store.close()
    target = tmp_path / "export.jsonl"
    assert main(["export", "--database", str(db), "--output", str(target)]) == 0
    data = json.loads(target.read_text())
    assert data["platform"] == "codeforces" and data["id"] == "1"
    assert main(["export", "--database", str(db), "--output", str(db)]) == 2
    assert timestamp("2026-10-14") == timestamp("2026-10-14T00:00:00Z")

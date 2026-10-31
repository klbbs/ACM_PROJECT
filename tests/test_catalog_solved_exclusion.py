"""Regression checks for post-sync and remote-judge candidate exclusion."""
import json
import time
from types import SimpleNamespace

import pytest

from acmweb import catalog
from acmweb.database import Database
from ojcrawler.models import Collection, Submission
from ojcrawler.storage import Store


def seed(db, key='4-A', platform='codeforces'):
    row = catalog._problem(platform, key, 'Example', difficulty=1000)
    with db.connect() as con:
        con.execute('INSERT INTO problem_catalog VALUES(?,?,?)', (platform, row['id'], json.dumps(row)))
    return row['id']


def save_history(db, key, verdict='AC', account='Alice', sid='123', platform='codeforces'):
    now = int(time.time())
    store = Store(db.path)
    try:
        store.save('history', now, Collection(platform, account,
            submissions=[Submission(sid, key, now - 1, verdict)], complete=False), capture_snapshot=False)
    finally:
        store.close()


@pytest.mark.parametrize('key', ['4-A', '4-a', 'codeforces:4-a',
    'https://codeforces.com/problemset/problem/4/A', 'https://codeforces.com/contest/4/problem/a'])
@pytest.mark.parametrize('verdict', ['AC', 'WA'])
def test_synced_history_immediately_removes_aliases_without_rebuilding_catalog(tmp_path, key, verdict):
    db = Database(tmp_path / 'history.sqlite3')
    seed(db)
    assert len(catalog._pool(db, 'codeforces')) == 1
    save_history(db, key, verdict)
    assert catalog._pool(db, 'codeforces') == []
    # Specialist practice allows a failed attempt to be revisited, never AC.
    assert len(catalog._pool(db, 'codeforces', allow_attempted=True)) == (verdict != 'AC')


def test_gym_alias_and_account_workspace_isolation(tmp_path):
    mine, other = Database(tmp_path / 'mine.sqlite3'), Database(tmp_path / 'other.sqlite3')
    for db in (mine, other):
        seed(db, '727347-C')
    save_history(mine, 'https://codeforces.com/gym/727347/problem/c')
    assert catalog._pool(mine, 'codeforces') == []
    assert len(catalog._pool(other, 'codeforces')) == 1


def attempt(db, *, judge=None, verdict='AC', handle='Alice', sid=123, platform='codeforces'):
    pid = 'codeforces:4-a' if platform == 'codeforces' else 'luogu:P1001'
    with db.connect() as con:
        con.execute('''INSERT INTO wb_attempts
            (id,problem_id,language,code,code_hash,created_at,handle,submission_id,verdict,judge_data,last_synced)
            VALUES('attempt',?,'cpp','private code','hash',1,?,?,?,?,?)''',
            (pid, handle, sid, verdict, json.dumps(judge), int(time.time())))


@pytest.mark.parametrize('platform', ['codeforces', 'luogu'])
def test_verified_remote_ac_is_excluded_before_collection_sync(tmp_path, platform):
    db = Database(tmp_path / 'remote.sqlite3')
    seed(db, '4-A' if platform == 'codeforces' else 'P1001', platform)
    now = int(time.time())
    judge = {'verdict':'OK', 'creationTimeSeconds':now-1} if platform == 'codeforces' else {
        'verdict':'AC','submitted_at':now-1,'source':'official_remote_submission','platform':platform}
    attempt(db, judge=judge, platform=platform)
    assert catalog._pool(db, platform, allow_attempted=True) == []


@pytest.mark.parametrize('kwargs', [
    {'verdict':'WA'}, {'handle':''}, {'sid':None}, {'judge':None},
    {'judge':{'verdict':'AC', 'creationTimeSeconds':int(time.time()) + 10000}},
    {'judge':{'verdict':'AC', 'creationTimeSeconds':1, 'platform':'luogu'}},
])
def test_unverified_attempt_is_not_claimed_as_accepted(tmp_path, kwargs):
    db = Database(tmp_path / 'draft.sqlite3')
    seed(db)
    data = {'judge':{'verdict':'OK', 'creationTimeSeconds':int(time.time())-1}, **kwargs}
    attempt(db, **data)
    assert len(catalog._pool(db, 'codeforces', allow_attempted=True)) == 1


def test_collected_rejudge_replaces_old_remote_ac(tmp_path):
    db = Database(tmp_path / 'rejudge.sqlite3')
    seed(db)
    attempt(db, judge={'verdict':'OK', 'creationTimeSeconds':int(time.time())-1})
    # The canonical alias differs intentionally; matching receipt/account still
    # makes collected official history authoritative over old workbench data.
    save_history(db, '4-A', 'WA', account='ALICE')
    assert len(catalog._pool(db, 'codeforces', allow_attempted=True)) == 1


def test_feedback_reservations_and_explicit_exclusions_share_identity(tmp_path):
    db = Database(tmp_path / 'excluded.sqlite3')
    first, second, third = (seed(db, key) for key in ('4-A', '5-A', '6-A'))
    with db.connect() as con:
        con.execute('INSERT INTO practice_feedback VALUES(?,?,?,?,?)', ('codeforces','4-a','done','',1))
    db.set('battle_problem_ids', ['https://codeforces.com/contest/5/problem/a'])
    accounts = SimpleNamespace(credential_checks=SimpleNamespace(statuses=lambda: {'items':[
        {'platform':'codeforces','status':'authenticated','login_verified':True}]}))
    assert [p['id'] for p in catalog.recommendation_candidates(db, accounts)] == [third]
    assert catalog.recommendation_candidates(db, accounts, excluded_ids=['codeforces:6-a']) == []


def test_unsupported_history_does_not_disable_candidates_or_cross_platforms(tmp_path):
    db = Database(tmp_path / 'unsupported.sqlite3')
    seed(db)
    save_history(db, 'unsupported-old-id')
    save_history(db, '4-A', platform='nowcoder')
    assert len(catalog._pool(db, 'codeforces')) == 1

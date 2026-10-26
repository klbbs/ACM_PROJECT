"""Synthetic observation ordering, without platform requests or code execution."""
import json

import pytest

from ojcrawler.models import Collection, Submission
from ojcrawler.storage import Store


def collection(status, *, raw_status=None):
    raw = {} if raw_status is None else {'verdict': raw_status, 'creationTimeSeconds': 100}
    return Collection('codeforces', 'DEMO', submissions=[Submission('12345', '4-A', 100, status, raw=raw)])


def attempt_table(store, *, verdict='AC', synced=400):
    store.db.executescript('''CREATE TABLE wb_attempts (
        id TEXT PRIMARY KEY,handle TEXT,submission_id INTEGER,problem_id TEXT,
        verdict TEXT,judge_data TEXT,last_synced INTEGER,review TEXT,code TEXT,code_hash TEXT
    );''')
    with store.db:
        store.db.execute('INSERT INTO wb_attempts VALUES(?,?,?,?,?,?,?,?,?,?)',
            ('attempt','demo',12345,'codeforces:4-A',verdict,'{}',synced,'{"category":"saved"}',
             'PRIVATE-SOURCE','PRIVATE-HASH'))


@pytest.mark.parametrize('old_status', ['PENDING', 'WA', 'AC'])
def test_delayed_old_collection_cannot_replace_newer_submission_and_attempt(tmp_path, old_status):
    store = Store(tmp_path/'user.sqlite3')
    try:
        store.save('fresh', 400, collection('AC'))
        attempt_table(store)
        before = store.db.execute('SELECT * FROM wb_attempts').fetchone()
        store.save('delayed', 300, collection(old_status))
        saved = store.db.execute('SELECT verdict,updated_at FROM submissions').fetchone()
        assert saved == ('AC', 400)
        assert store.export()[0]['verdict'] == 'AC'
        assert store.db.execute('SELECT * FROM wb_attempts').fetchone() == before
    finally:
        store.close()


def test_newer_workbench_evidence_blocks_old_import_even_without_submission_row(tmp_path):
    store = Store(tmp_path/'user.sqlite3')
    try:
        attempt_table(store, synced=500)
        before = store.db.execute('SELECT * FROM wb_attempts').fetchone()
        store.save('delayed', 400, collection('PENDING'))
        assert store.export() == []
        assert store.db.execute('SELECT * FROM wb_attempts').fetchone() == before
    finally:
        store.close()


@pytest.mark.parametrize('new_status,raw_status', [('WA','WRONG_ANSWER'), ('PENDING','TESTING')])
def test_later_official_rejudge_may_change_accepted_verdict_including_to_pending(tmp_path, new_status, raw_status):
    store = Store(tmp_path/'user.sqlite3')
    try:
        store.save('fresh', 400, collection('AC'))
        attempt_table(store)
        store.save('rejudge', 401, collection(new_status, raw_status=raw_status))
        assert store.export()[0]['verdict'] == new_status
        status, review, code = store.db.execute('SELECT verdict,review,code FROM wb_attempts').fetchone()
        assert status == new_status and review is None and code == 'PRIVATE-SOURCE'
    finally:
        store.close()


def test_same_timestamp_official_rejudge_evidence_is_still_accepted(tmp_path):
    store = Store(tmp_path/'user.sqlite3')
    try:
        store.save('first', 400, collection('WA', raw_status='WRONG_ANSWER'))
        attempt_table(store, verdict='WA')
        changed = collection('WA', raw_status='WRONG_ANSWER')
        changed.submissions[0].raw['passedTestCount'] = 3
        store.save('same-second-rejudge', 400, changed)
        row = store.db.execute('SELECT verdict,judge_data,review FROM wb_attempts').fetchone()
        assert row[0] == 'WA' and json.loads(row[1])['passedTestCount'] == 3 and row[2] is None
    finally:
        store.close()

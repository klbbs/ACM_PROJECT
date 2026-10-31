"""Local history/atomic delivery tests; no real platform or model requests."""
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from acmhermes.memory import DUPLICATE_MESSAGE, DrawMemory
from acmweb.codex_bridge import AIError
from acmweb.database import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / 'private.sqlite3')


def job(db, jid, pid, status='completed', kind='hermes_draw', format='text'):
    with db.connect() as c:
        c.execute('INSERT INTO web_jobs VALUES(?,?,?,?,?,?,?)',
                  (jid, kind, status, 100, 100, json.dumps({'format': format,
                   'problem': {'id': pid}}), None))


def submit(db, pid, verdict='WA', platform='codeforces', sid='1'):
    with db.connect() as c:
        c.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)',
                  (platform, 'Owner', sid, pid, int(time.time()) - 10,
                   verdict, json.dumps({'private': 'must not be exported'}), 100))


def attempt(db, aid, pid, *, verified=False):
    now = int(time.time()) - 10
    with db.connect() as c:
        c.execute('''INSERT INTO wb_attempts(id,problem_id,language,code,code_hash,created_at,
            handle,submission_id,verdict,judge_data,last_synced) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            (aid, pid, 'cpp', 'PRIVATE_SOURCE', 'hash', now, 'Owner' if verified else '',
             987 if verified else None, 'AC', json.dumps({'verdict': 'OK', 'creationTimeSeconds': now}), now if verified else 0))


def test_backfill_canonical_completed_text_image_and_legacy_only(db):
    job(db, 'old-text', 'https://codeforces.com/contest/4/problem/a')
    job(db, 'old-image', 'https://codeforces.com/gym/727347/problem/c', format='image')
    job(db, 'legacy', 'luogu:P1001', format='legacy_report')
    job(db, 'duplicate', 'codeforces:4-A')
    job(db, 'failed', 'codeforces:5-A', status='failed')
    job(db, 'cancelled', 'codeforces:6-A', status='interrupted')
    job(db, 'pending', 'codeforces:7-A', status='running')
    job(db, 'other', 'codeforces:8-A', kind='recommend')
    job(db, 'invalid', 'bad-id')
    memory = DrawMemory(db)
    assert memory.drawn_ids() == {'codeforces:4-A', 'codeforces:727347-C', 'luogu:P1001'}
    assert DrawMemory(db).drawn_ids() == memory.drawn_ids()


def test_delivery_unique_same_job_is_idempotent_and_rollback_is_atomic(db):
    memory = DrawMemory(db)
    result = {'problem': {'id': 'codeforces:4-a'}}
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        assert memory.record(c, 'one', result)
        assert memory.record(c, 'one', {'problem': {'id': 'https://codeforces.com/problemset/problem/4/A'}})
    with pytest.raises(AIError, match=DUPLICATE_MESSAGE):
        with db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            memory.record(c, 'two', result)
    with pytest.raises(AIError, match='不能修改已交付题号'):
        with db.connect() as c:
            memory.record(c, 'one', {'problem': {'id': 'codeforces:7-A'}})
    with pytest.raises(RuntimeError):
        with db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            memory.record(c, 'three', {'problem': {'id': 'codeforces:5-A'}})
            raise RuntimeError('simulate completion write failing')
    assert memory.drawn_ids() == {'codeforces:4-A'}
    with pytest.raises(AIError, match='可核验题号'):
        with db.connect() as c:
            memory.record(c, 'bad', {'problem': {'id': 'invalid'}})


def test_parallel_completions_allow_exactly_one_delivery(db):
    memory = DrawMemory(db)

    def complete(jid):
        try:
            with db.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                memory.record(c, jid, {'problem': {'id': 'codeforces:4-A'}})
            return True
        except AIError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(complete, ['one', 'two'])) == [False, True]
    assert memory.drawn_ids() == {'codeforces:4-A'}


def test_history_union_canonicalization_verified_ac_and_no_secrets(db):
    memory = DrawMemory(db)
    submit(db, 'https://codeforces.com/gym/727347/problem/c', 'AC')
    submit(db, '4-a', sid='2')
    submit(db, 'abc001_1', 'AC', platform='atcoder', sid='3')
    attempt(db, 'draft', 'codeforces:5-A')
    attempt(db, 'verified', 'codeforces:6-A', verified=True)
    with db.connect() as c:
        c.execute('INSERT INTO practice_feedback VALUES(?,?,?,?,?)', ('luogu', 'P1001', 'done', 'PRIVATE_NOTE', 1))
        c.execute('INSERT INTO practice_feedback VALUES(?,?,?,?,?)', ('codeforces', '7-a', 'skip', 'PRIVATE_NOTE', 1))
        memory.record(c, 'delivered', {'problem': {'id': 'codeforces:4-A'}})
    payload = memory.read()
    items = {item['problem_id']: item for item in payload['items']}
    assert items['codeforces:727347-C']['solved']
    assert items['atcoder:abc001_1']['solved']
    assert items['codeforces:4-A'] == dict(problem_id='codeforces:4-A', attempted=True, solved=False, drawn=True, skipped=False)
    assert items['codeforces:5-A']['attempted'] and not items['codeforces:5-A']['solved']
    assert items['codeforces:6-A']['solved']
    assert items['codeforces:7-A']['skipped']
    assert items['luogu:P1001']['attempted'] and not items['luogu:P1001']['solved']
    assert 'PRIVATE' not in json.dumps(payload)
    assert 'Owner' not in json.dumps(payload)


def test_pagination_revision_conflict_and_stable_per_user_scope(db, tmp_path):
    memory = DrawMemory(db)
    for number in range(1, 5):
        submit(db, f'{number}-a', sid=str(number))
    first = memory.read(limit=2)
    assert [item['problem_id'] for item in first['items']] == ['codeforces:1-A', 'codeforces:2-A']
    second = memory.read(after=first['next_after'], limit=2, revision=first['revision'])
    assert [item['problem_id'] for item in second['items']] == ['codeforces:3-A', 'codeforces:4-A']
    assert second['next_after'] is None
    assert memory.read()['revision'] == first['revision']
    assert DrawMemory(db).read()['scope'] == first['scope']
    submit(db, '5-A', sid='5')
    with pytest.raises(HTTPException) as changed:
        memory.read(after=first['next_after'], revision=first['revision'])
    assert changed.value.status_code == 409
    other = DrawMemory(Database(tmp_path / 'another-user.sqlite3'))
    assert other.read()['scope'] != first['scope']
    assert other.read()['items'] == []
    assert other.drawn_ids() == set()
    with pytest.raises(HTTPException) as invalid:
        memory.read(limit=0)
    assert invalid.value.status_code == 422


def test_matching_history_verdict_overrides_stale_workbench_ac(db):
    memory = DrawMemory(db)
    attempt(db, 'verified', 'codeforces:6-A', verified=True)
    submit(db, '6-A', 'WA', sid='987')
    item = memory.read()['items'][0]
    assert item['attempted'] and not item['solved']

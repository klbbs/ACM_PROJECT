import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from acmweb.database import Database
from acmweb.problem_records import ProblemRecords, UpdateRecord, register


@pytest.fixture
def records(tmp_path):
    service = ProblemRecords(Database(tmp_path / 'records.sqlite3'))
    app = FastAPI()
    register(app, service)
    with TestClient(app) as client:
        yield service, client


@pytest.mark.parametrize('value,pid', [
    ('4-a', 'codeforces:4-A'), ('P1001', 'luogu:P1001'),
    ('atcoder:abc001_1', 'atcoder:abc001_1'), ('nowcoder:1000', 'nowcoder:1000'),
    ('nowcoder:contest-123-a', 'nowcoder:contest-123-A'),
])
def test_records_normalize_and_work_without_statement_fetch(records, value, pid, monkeypatch):
    service, _ = records
    monkeypatch.setattr('ojcrawler.http.Client.get', lambda *a, **kw: pytest.fail('must not fetch remote statements'))
    first = service.get(value)
    assert first['problem_id'] == pid and first['revision'] == 0
    saved = service.update(value, UpdateRecord(revision=0, favorite=True))
    assert saved['problem_id'] == pid and saved['favorite'] and saved['revision'] == 1
    assert service.get(pid) == saved


def test_bookmark_and_note_updates_do_not_overwrite_each_other(records):
    service, _ = records
    result = service.update('4-A', UpdateRecord(revision=0, note='注意 long long\n边界 n=1'))
    assert not result['favorite'] and result['note'].startswith('注意')
    result = service.update('4-A', UpdateRecord(revision=1, favorite=True))
    assert result['note'].startswith('注意')
    result = service.update('4-A', UpdateRecord(revision=2, favorite=False))
    assert result['note'].startswith('注意') and not result['favorite']
    assert service.list()['total'] == 1
    assert service.list(favorites_only=True)['total'] == 0
    result = service.update('4-A', UpdateRecord(revision=3, note=''))
    assert result['revision'] == 4 and service.list()['total'] == 0
    assert service.get('4-A')['revision'] == 4
    with pytest.raises(HTTPException) as exc:
        service.update('4-A', UpdateRecord(revision=0, favorite=True))
    assert exc.value.status_code == 409


def test_revision_conflict_and_two_worker_atomicity(records):
    service, _ = records
    other = ProblemRecords(service.db)
    def update(worker):
        try:
            return worker.update('4-A', UpdateRecord(revision=0, note='updated'))['revision']
        except HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, (service, other))) == [1, 409]
    assert service.get('4-A')['note'] == 'updated'


def test_private_databases_survive_restart_and_do_not_mix(records, tmp_path):
    service, _ = records
    service.update('4-A', UpdateRecord(revision=0, note='owner-only note', favorite=True))
    reopened = ProblemRecords(Database(service.db.path))
    outsider = ProblemRecords(Database(tmp_path / 'outsider.sqlite3'))
    assert reopened.get('4-A')['note'] == 'owner-only note'
    assert outsider.get('4-A')['note'] == ''
    assert outsider.list()['items'] == []


def test_search_pagination_metadata_comes_from_local_cache_and_urls_are_canonical(records):
    service, _ = records
    with service.db.connect() as conn:
        conn.execute('INSERT INTO wb_problems VALUES(?,?,?)', ('codeforces:4-A', json.dumps({'title': 'Watermelon', 'url': 'javascript:alert(1)'}), 1))
        conn.execute('INSERT INTO problem_catalog VALUES(?,?,?)', ('luogu', 'luogu:P1001', json.dumps({'name': 'A+B Problem'})))
    service.update('4-A', UpdateRecord(revision=0, favorite=True, note='Parity 边界'))
    service.update('P1001', UpdateRecord(revision=0, note='加法'))
    assert service.list('WATERMELON')['total'] == 1
    assert service.list('边界')['items'][0]['problem_id'] == 'codeforces:4-A'
    assert service.list('P1001')['items'][0]['title'] == 'A+B Problem'
    assert service.get('4-A')['url'] == 'https://codeforces.com/problemset/problem/4/A'
    assert len(service.list(limit=1)['items']) == 1
    assert len(service.list(limit=1, offset=1)['items']) == 1
    assert service.list(limit=1, offset=2)['items'] == []
    assert service.list(favorites_only=True)['total'] == 1


def test_api_validation_and_maximum_note(records):
    _, client = records
    path = '/api/problem-records/codeforces:4-A'
    assert client.get(path).json()['revision'] == 0
    assert client.put(path, json={'revision': 0, 'note': '字' * 20000}).status_code == 200
    assert client.put(path, json={'revision': 0, 'favorite': True}).status_code == 409
    for payload in ({'revision': 1, 'note': 'a'*20001}, {'revision': 1}, {'revision': True, 'note': ''},
                    {'revision': 1, 'favorite': 'yes'}, {'revision': 1, 'note': '', 'url': 'http://evil.invalid'}):
        assert client.put(path, json=payload).status_code == 422
    assert client.get('/api/problem-records/invalid').status_code == 400
    assert client.get('/api/problem-records?limit=1000').status_code == 422
    assert client.get('/api/problem-records?offset=-1').status_code == 422
    assert client.get('/api/problem-records?search=' + 'x'*201).status_code == 422

"""Synthetic official API contracts; no OJ or model is contacted."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
import threading
import time

import pytest

from acmhermes import catalog
from acmweb.codex_bridge import AIError
from acmweb.database import Database


def accounts(platforms=('codeforces',)):
    return SimpleNamespace(credential_checks=SimpleNamespace(statuses=lambda: {'items': [
        {'platform': platform, 'status': 'authenticated', 'login_verified': True}
        for platform in platforms]}))


@pytest.fixture
def database(tmp_path):
    return Database(tmp_path / 'data.sqlite3')


def official(monkeypatch, problem_data=None, contest_data=None):
    problem_data = problem_data if problem_data is not None else {'status': 'OK', 'result': {'problems': [
        {'contestId': 4, 'index': 'A', 'name': 'Public A', 'rating': 800, 'tags': ['math']},
        {'contestId': 4, 'index': 'B', 'name': 'Unrated'},
        {'contestId': 5, 'index': 'A', 'name': 'Unfinished', 'rating': 900},
    ]}}
    contest_data = contest_data if contest_data is not None else {'status': 'OK', 'result': [
        {'id': 4, 'phase': 'FINISHED', 'startTimeSeconds': 1600000000},
        {'id': 5, 'phase': 'BEFORE', 'startTimeSeconds': 1600000010}]}
    calls = []
    def get(self, url, params=None, headers=None):
        calls.append((url, params, self.cookie))
        return deepcopy(contest_data if url.endswith('/contest.list') else problem_data)
    monkeypatch.setattr(catalog.Client, 'json', get)
    return calls


def snapshot(database):
    with database.connect() as db:
        return [tuple(row) for row in db.execute('SELECT * FROM problem_catalog ORDER BY id')], [
            tuple(row) for row in db.execute('SELECT * FROM web_settings ORDER BY key')]


def test_downloads_official_complete_rated_finished_index_and_reuses_fresh_cache(database, monkeypatch):
    calls = official(monkeypatch)
    result = asyncio.run(catalog.ensure_catalog(database, accounts()))
    assert result['status'] == 'updated' and result['count'] == 1
    assert result['freshness'] == 'fresh' and 'Gym' in result['coverage']
    assert calls == [('https://codeforces.com/api/problemset.problems', None, None),
                     ('https://codeforces.com/api/contest.list', {'gym': 'false'}, None)]
    row = catalog._cached(database)[0]
    assert row['id'] == 'codeforces:4-A' and row['published_at'] == 1600000000
    second = asyncio.run(catalog.ensure_catalog(database, accounts()))
    assert second['status'] == 'cached' and len(calls) == 2


def test_expires_after_six_hours(database, monkeypatch):
    calls = official(monkeypatch)
    asyncio.run(catalog.ensure_catalog(database, accounts()))
    database.set(catalog.SUCCESS_KEY, int(time.time()) - catalog.TTL_SECONDS)
    assert asyncio.run(catalog.ensure_catalog(database, accounts()))['status'] == 'updated'
    assert len(calls) == 4


@pytest.mark.parametrize('platforms', [(), ('atcoder',), ('luogu',)])
def test_no_verified_cf_does_not_network(database, monkeypatch, platforms):
    calls = official(monkeypatch)
    result = asyncio.run(catalog.ensure_catalog(database, accounts(platforms)))
    assert result['status'] == 'not_required' and calls == []


@pytest.mark.parametrize('response', [
    {'status': 'OK', 'result': {'problems': []}},
    {'status': 'OK', 'result': {'problems': [{'contestId': 4, 'index': 'A', 'name': 'A', 'rating': 800}], 'truncated': True}},
    {'status': 'OK', 'result': {'problems': [{'contestId': 4, 'index': 'A', 'name': 'A', 'rating': 800}], 'hasMore': True}},
    {'status': 'FAILED', 'comment': 'secret response'},
    {'status': 'OK', 'result': {'problems': 'broken'}},
    ['unexpected'],
])
def test_bad_problem_contract_preserves_old_cache(database, monkeypatch, response):
    official(monkeypatch)
    asyncio.run(catalog.ensure_catalog(database, accounts()))
    database.set(catalog.SUCCESS_KEY, 0)
    before = snapshot(database)
    official(monkeypatch, problem_data=response)
    with pytest.raises(AIError, match=catalog.FAILURE):
        asyncio.run(catalog.ensure_catalog(database, accounts()))
    assert snapshot(database) == before


def test_missing_contest_date_not_published(database, monkeypatch):
    official(monkeypatch, contest_data={'status': 'OK', 'result': [{'id': 4, 'phase': 'FINISHED'}]})
    with pytest.raises(AIError, match=catalog.FAILURE):
        asyncio.run(catalog.ensure_catalog(database, accounts()))
    assert snapshot(database) == ([], [])


def test_restricted_request_safe_failure_and_existing_cache_retained(database, monkeypatch):
    official(monkeypatch)
    asyncio.run(catalog.ensure_catalog(database, accounts()))
    database.set(catalog.SUCCESS_KEY, 0)
    before = snapshot(database)
    def restricted(*args, **kwargs):
        raise RuntimeError('private upstream response cookie=secret')
    monkeypatch.setattr(catalog.Client, 'json', restricted)
    with pytest.raises(AIError) as error:
        asyncio.run(catalog.ensure_catalog(database, accounts()))
    assert str(error.value) == catalog.FAILURE and snapshot(database) == before


def test_unrelated_legacy_cf_metadata_does_not_prevent_freshness(database, monkeypatch):
    calls = official(monkeypatch)
    with database.connect() as db:
        db.execute('INSERT INTO problem_catalog VALUES(?,?,?)', ('codeforces', 'codeforces:123456-A', json.dumps({
            'id': 'codeforces:123456-A', 'platform': 'codeforces', 'title': 'Legacy Gym'})))
    asyncio.run(catalog.ensure_catalog(database, accounts()))
    result = asyncio.run(catalog.ensure_catalog(database, accounts()))
    assert result['status'] == 'cached' and result['count'] == 1 and len(calls) == 2
    assert len(catalog._cached(database)) == 2


def test_cancellation_during_first_call_prevents_contest_call_and_publish(database, monkeypatch):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def slow(self, url, params=None, headers=None):
        calls.append(url)
        started.set()
        assert release.wait(3)
        finished.set()
        return {'status': 'OK', 'result': {'problems': [{'contestId': 4, 'index': 'A', 'rating': 800, 'name': 'A'}]}}
    monkeypatch.setattr(catalog.Client, 'json', slow)
    async def run():
        task = asyncio.create_task(catalog.ensure_catalog(database, accounts()))
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert await asyncio.to_thread(finished.wait, 3)
    asyncio.run(run())
    assert len(calls) == 1 and snapshot(database) == ([], [])


def test_stop_during_host_pacing_prevents_new_http_request(monkeypatch):
    stopped=threading.Event()
    sends=[]
    class Session:
        headers={}
        def get(self,*args,**kwargs):
            sends.append(args)
            pytest.fail('stopped catalog must not send a new request')
        def close(self):pass
    monkeypatch.setattr('ojcrawler.http.requests.Session',Session)
    def paced(self,*args,**kwargs):
        stopped.set()
        return self.session.get(*args,**kwargs)
    monkeypatch.setattr(catalog.Client,'get',paced)
    client=catalog._OfficialClient(stopped)
    try:
        with pytest.raises(asyncio.CancelledError):client.json('https://codeforces.com/api/problemset.problems')
        assert not sends
    finally:client.close()

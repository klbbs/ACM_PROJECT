"""Catalog jobs expose honest progress, safe errors and current candidate rejection reasons."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from acmweb import catalog
from acmweb.database import Database
from acmweb.jobs import Jobs
from ojcrawler.models import CrawlError


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / 'catalog.sqlite3')


def add(db, platform, key, **extra):
    row = catalog._problem(platform, key, key, **extra)
    with db.connect() as con:
        con.execute('INSERT INTO problem_catalog VALUES(?,?,?)', (platform, row['id'], json.dumps(row)))
    return row


def accounts(*platforms):
    return SimpleNamespace(credential_checks=SimpleNamespace(statuses=lambda: {'items': [
        {'platform': platform, 'status': 'authenticated', 'login_verified': True} for platform in platforms]}))


def test_refresh_reports_each_platform_progress_and_safe_failure_preserving_cache(db, monkeypatch):
    add(db, 'luogu', 'P1001')
    monkeypatch.setattr(catalog, 'Client', lambda **kwargs: SimpleNamespace(close=lambda: None))
    monkeypatch.setitem(catalog.DISCOVERY, 'codeforces', lambda client: [catalog._problem('codeforces', '1-A', 'Test')])
    def denied(client):
        raise CrawlError('HTTP 403; Cookie: session=very-secret; arbitrary upstream response')
    monkeypatch.setitem(catalog.DISCOVERY, 'luogu', denied)
    monkeypatch.setitem(catalog.DISCOVERY, 'atcoder', lambda client: [])
    monkeypatch.setitem(catalog.DISCOVERY, 'nowcoder', lambda client: [])
    progress = []
    result = catalog.refresh_catalog(db, on_progress=progress.append)
    assert result['count'] == 1 and not result['complete']
    assert progress[0]['completed_platforms'] == 0
    assert progress[1]['platforms'][0]['status'] == 'running'
    assert progress[-1]['completed_platforms'] == 4
    assert result['platforms'][2]['error_code'] == 'access_denied'
    assert '403' in result['platforms'][2]['message']
    assert 'very-secret' not in json.dumps(progress)
    assert db.get('catalog_refresh_at')
    assert catalog._pool(db, 'luogu')[0]['id'] == 'luogu:P1001'


@pytest.mark.parametrize(('message', 'expected'), [
    ('HTTP 429 secret', 'rate_limited'), ('HTTP 502 secret', 'upstream_unavailable'),
    ('HTTP 404 secret', 'http_error'), ('network ReadTimeout cookie=x', 'timeout'),
    ('SSLError cookie=x', 'tls_error'), ('网络请求失败 secret', 'network_error'),
    ('预期 JSON secret', 'unexpected_response'), ('secret', 'source_changed'),
])
def test_upstream_errors_never_copy_arbitrary_message(message, expected):
    code, text = catalog._sync_error(CrawlError(message))
    assert code == expected
    assert 'secret' not in text and 'cookie=x' not in text


def test_diagnostics_explain_filters_and_never_count_unverified_platform(db):
    stamp = catalog.publication_time('2025-06-01T00:00:00+00:00')
    def cf(key, difficulty=1000, **extra):
        return add(db, 'codeforces', key, difficulty=difficulty, published_at=stamp,
            published_at_source='codeforces.contest.startTimeSeconds', **extra)
    cf('1-A', tags=['dp'])
    cf('1-B', difficulty=2000, tags=['dp'])
    cf('1-C', tags=['greedy'])
    cf('1-D', tags=['dp'])
    cf('1-E', tags=['dp'])
    with db.connect() as con:
        con.execute("INSERT INTO practice_feedback VALUES('codeforces','1-D','skip','',1)")
    db.set('battle_problem_ids', ['codeforces:1-E'])
    add(db, 'luogu', 'P1001', published_at=stamp, published_at_source='luogu.problem.createTime', tags=['dp'])
    result = catalog.recommendation_status(db, accounts('codeforces'), minimum=800, maximum=1200, tags=['dp'])
    cf_status, _, luogu_status, _ = result['platforms']
    assert result['candidate_count'] == 1
    assert cf_status['filter_counts']['difficulty'] == 1
    assert cf_status['filter_counts']['tags'] == 1
    assert cf_status['filter_counts']['attempted_or_reserved'] == 2
    assert cf_status['total_cached_count'] == 5
    assert luogu_status['eligible_count'] == 0
    assert 'Cookie' in luogu_status['reasons'][0]
    assert len(catalog.recommendation_candidates(db, accounts('codeforces'), minimum=800, maximum=1200, tags=['dp'])) == result['candidate_count']


def test_diagnostics_distinguish_unknown_date_from_old_date_and_migration(db):
    add(db, 'atcoder', 'abc400_a')
    add(db, 'atcoder', 'abc400_b', published_at=catalog.publication_time('2024-01-01T00:00:00+00:00'), published_at_source='atcoder.archive.time')
    db.set('training', {'published_after': '2025-01-01'})
    result = catalog.recommendation_status(db, accounts('atcoder'))
    row = result['platforms'][1]
    assert row['filter_counts']['unknown_date'] == 1
    assert row['filter_counts']['before_date'] == 1
    assert result['candidate_count'] == 0
    db.set('training', {'min_contest': 2000})
    result = catalog.recommendation_status(db, accounts('atcoder'))
    assert result['requires_publication_date'] and result['candidate_count'] == 0
    assert catalog.LEGACY_DATE_NOTICE in result['platforms'][1]['reasons']


def test_job_persists_partial_progress_and_finishes_with_partial_status(db, monkeypatch):
    import acmweb.jobs as jobs_module
    calls = []
    original = Jobs.update
    def capture(self, *args, **kwargs):
        calls.append((args, kwargs))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Jobs, 'update', capture)
    def refresh(database, on_progress=None):
        on_progress({'count': 3, 'platforms': [{'platform': 'codeforces', 'status': 'updated', 'count': 3}], 'completed_platforms': 1, 'total_platforms': 4})
        return {'count': 3, 'complete': False, 'platforms': [{'platform': 'codeforces', 'status': 'updated', 'count': 3}]}
    monkeypatch.setattr(jobs_module, 'refresh_catalog', refresh)
    async def run():
        jobs = Jobs(db, SimpleNamespace(), None)
        job = jobs.start('catalog')
        await jobs.tasks[job['id']][1]
        return jobs.list()[0]
    row = asyncio.run(run())
    assert any(args[1] == 'running' and len(args) > 2 and args[2]['count'] == 3 for args, kwargs in calls)
    assert row['status'] == 'partial' and row['result']['count'] == 3


def test_late_thread_progress_cannot_resurrect_cancelled_job(db, monkeypatch):
    import threading
    import acmweb.jobs as jobs_module
    entered, release = threading.Event(), threading.Event()
    def refresh(database, on_progress=None):
        entered.set()
        release.wait(timeout=3)
        on_progress({'count': 1, 'complete': True, 'platforms': []})
        return {'count': 1, 'complete': True, 'platforms': []}
    monkeypatch.setattr(jobs_module, 'refresh_catalog', refresh)
    async def run():
        jobs = Jobs(db, SimpleNamespace(), None)
        job = jobs.start('catalog')
        while not entered.is_set(): await asyncio.sleep(0.001)
        await jobs.close()
        release.set()
        await asyncio.sleep(0.02)
        return jobs.list()[0]
    row = asyncio.run(run())
    assert row['status'] == 'interrupted'


def test_diagnostic_rejection_order_matches_candidate_predicates(db):
    add(db, 'codeforces', '1-A', difficulty=2200)
    db.set('training', {'published_after': '2025-01-01'})
    result = catalog.recommendation_status(db, accounts('codeforces'), minimum=800, maximum=1200)
    counts = result['platforms'][0]['filter_counts']
    assert counts['difficulty'] == 1 and counts['unknown_date'] == 0
    assert not catalog.recommendation_candidates(db, accounts('codeforces'), minimum=800, maximum=1200)

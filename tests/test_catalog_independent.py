"""Independent regression checks for truthful recommendation diagnostics."""
import json
import asyncio
import threading
from types import SimpleNamespace

import pytest

from acmweb import catalog
from acmweb.database import Database
from acmweb.jobs import Jobs


def authenticated(*platforms):
    return SimpleNamespace(credential_checks=SimpleNamespace(statuses=lambda: {'items': [
        {'platform': p, 'status': 'authenticated', 'login_verified': True}
        for p in platforms]}))


@pytest.mark.parametrize('published_at', [None, 1])
def test_reason_order_matches_actual_cf_difficulty_before_date(tmp_path, published_at):
    db = Database(tmp_path / 'diagnostics.sqlite3')
    row = catalog._problem('codeforces', '1-A', 'Example', difficulty=2400,
        published_at=published_at,
        published_at_source='codeforces.contest.startTimeSeconds' if published_at else None)
    with db.connect() as con:
        con.execute('INSERT INTO problem_catalog VALUES(?,?,?)', ('codeforces', row['id'], json.dumps(row)))
    args = dict(minimum=800, maximum=1200, published_after='2025-01-01')
    assert catalog.recommendation_candidates(db, authenticated('codeforces'), **args) == []
    status = catalog.recommendation_status(db, authenticated('codeforces'), **args)
    counts = status['platforms'][0]['filter_counts']
    # candidates() checks CF difficulty before publication, so diagnostics must too.
    assert counts['difficulty'] == 1
    assert counts['unknown_date'] == counts['before_date'] == 0


@pytest.mark.parametrize('verified', [False, True])
@pytest.mark.parametrize('date_floor', [None, '2025-01-01'])
def test_diagnostic_eligible_count_matches_unlimited_real_candidates(tmp_path, verified, date_floor):
    db = Database(tmp_path / 'diagnostics.sqlite3')
    for platform in catalog.PLATFORMS:
        for index in range(5):
            key = {'codeforces': f'{index + 1}-A', 'atcoder': f'abc400_{chr(97 + index)}', 'luogu': f'P{1001 + index}', 'nowcoder': str(1001 + index)}[platform]
            row = catalog._problem(platform, key, 'Example',
                difficulty=1000 if index % 2 else 2400,
                tags=['dp'] if index % 3 else ['greedy'],
                published_at=1760000000 if index % 2 else None,
                published_at_source='codeforces.contest.startTimeSeconds' if index % 2 else None)
            with db.connect() as con:
                con.execute('INSERT INTO problem_catalog VALUES(?,?,?)', (platform, row['id'], json.dumps(row)))
    accounts = authenticated(*catalog.PLATFORMS) if verified else authenticated()
    args = dict(minimum=800, maximum=1200, tags=['dp'], published_after=date_floor)
    status = catalog.recommendation_status(db, accounts, **args)
    if verified:
        assert status['candidate_count'] == len(catalog.recommendation_candidates(db, accounts, limit=100000, **args))
    else:
        assert status['candidate_count'] == 0
        assert all(not row['login_verified'] and row['reasons'] for row in status['platforms'])


def test_cancelled_catalog_job_cannot_be_revived_by_thread_progress(tmp_path, monkeypatch):
    import acmweb.jobs as jobs_module
    db = Database(tmp_path / 'cancel.sqlite3')
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def refresh(database, on_progress=None):
        entered.set()
        assert release.wait(5)
        on_progress({'count': 1, 'complete': False, 'platforms': []})
        finished.set()
        return {'count': 1, 'complete': True, 'platforms': []}

    monkeypatch.setattr(jobs_module, 'refresh_catalog', refresh)

    async def scenario():
        jobs = Jobs(db, SimpleNamespace(), None)
        jobs.start('catalog')
        assert await asyncio.to_thread(entered.wait, 5)
        try:
            await jobs.close()
            assert jobs.list()[0]['status'] == 'interrupted'
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 5)
        assert jobs.list()[0]['status'] == 'interrupted'

    asyncio.run(scenario())

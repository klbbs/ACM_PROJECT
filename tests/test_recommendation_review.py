"""Independent review: selection eligibility must survive delayed AI and stale UI input."""
import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from acmweb import catalog
from acmweb.ai import AIResult
from acmweb.codex_bridge import AIError
from acmweb.daily import Daily, Roll, Settings
from acmweb.database import Database
from acmweb.jobs import Jobs


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / 'independent-selection.sqlite3')


def session(platform='codeforces'):
    state = {'valid': True}
    accounts = SimpleNamespace(credential_checks=SimpleNamespace(statuses=lambda: {'items': [
        {'platform': platform, 'handle': 'fixture', 'status': 'authenticated', 'login_verified': True}
    ] if state['valid'] else []}))
    return accounts, state


def seed(db, key, timestamp, source='codeforces.contest.startTimeSeconds'):
    row = {'id': 'codeforces:' + key, 'platform': 'codeforces', 'problem_id': key, 'title': key,
           'difficulty': 1000, 'contest_id': int(key.split('-')[0]), 'tags': [],
           'published_at': catalog.publication_time(timestamp), 'published_at_source': source,
           'url': 'https://codeforces.com/problemset/problem/' + key.replace('-', '/')}
    with db.connect() as conn:
        conn.execute('INSERT INTO problem_catalog VALUES(?,?,?)', ('codeforces', row['id'], json.dumps(row)))
    return row


def test_saved_publication_floor_wins_stale_request_and_preferred_ids(db):
    accounts, _ = session()
    old = seed(db, '1000-A', '2024-12-31T15:59:59+00:00')
    exact = seed(db, '1001-A', '2024-12-31T16:00:00+00:00')
    guessed = seed(db, '1002-A', '2025-05-01T00:00:00+00:00', 'guessed')
    unknown = seed(db, '1003-A', None)
    db.set('training', {'published_after': '2025-01-01'})
    pool = catalog.recommendation_candidates(db, accounts, published_after='2020-01-01',
        preferred_ids=[p['id'] for p in [old, exact, guessed, unknown]])
    assert [p['id'] for p in pool] == [exact['id']]
    assert catalog.recommendation_status(db, accounts, '2020-01-01')['published_after'] == '2025-01-01'
    assert catalog.recommendation_candidates(db, accounts, published_after='2026-01-01') == []


@pytest.mark.parametrize('change', ['date', 'login', 'submitted'])
def test_daily_delayed_ai_cannot_save_a_candidate_that_lost_eligibility(db, change):
    accounts, state = session()
    row = seed(db, '1000-A', '2025-05-01T00:00:00+00:00')
    async def select(*args):
        if change == 'date':
            db.set('training', {'published_after': '2026-01-01'})
        elif change == 'login':
            state['valid'] = False
        else:
            with db.connect() as conn:
                conn.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)',
                    ('codeforces', 'fixture', '1', '1000-A', int(time.time()), 'AC', '{}', int(time.time())))
        return AIResult({'selected_problems': [{'id': row['id'], 'reason': 'synthetic'}]}, {})
    daily = Daily(db, SimpleNamespace(complete=AsyncMock(side_effect=select)), accounts)
    daily.configure(Settings())
    with pytest.raises(AIError):
        asyncio.run(daily.roll(Roll(provider='api', model='offline-fixture')))
    assert daily.state()['plan']['status'] == 'failed'
    with db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM daily_plan_tasks').fetchone()[0] == 0
    assert daily.state()['rerolls_remaining'] == 2


def test_recommend_job_does_not_persist_old_date_after_training_changes(db, monkeypatch):
    accounts, _ = session()
    row = seed(db, '1000-A', '2025-05-01T00:00:00+00:00')
    monkeypatch.setattr('acmweb.jobs.analytics.overview', lambda *args: {'stats': {'submissions': 1}, 'days': 30})
    async def delayed_report(*args, **kwargs):
        db.set('training', {'published_after': '2026-01-01'})
        return {'selected_problems': [{'id': row['id'], 'reason': 'synthetic'}]}
    jobs = Jobs(db, None, SimpleNamespace(generate=AsyncMock(side_effect=delayed_report)), accounts)
    with db.connect() as conn:
        conn.execute('INSERT INTO web_jobs VALUES(?,?,?,?,?,?,?)', ('review-job', 'recommend', 'queued', 1, 1, None, None))
    asyncio.run(jobs._run('review-job', 'recommend', {'provider': 'api', 'model': 'offline-fixture'}))
    result = jobs.list()[0]
    assert result['status'] == 'failed' and '资格已变化' in result['error']
    assert db.reports() == []


def test_selection_login_refresh_only_uses_the_supplied_detector(db, monkeypatch):
    # No HTTP client can be constructed in this review test, even for expired evidence.
    monkeypatch.setattr(catalog, 'Client', lambda *args, **kwargs: pytest.fail('unexpected real HTTP client'))
    seen = []
    detector = SimpleNamespace(
        statuses=lambda: {'items': []},
        credential=lambda *args: ('synthetic=offline', 'not-a-real-cookie'),
        check=lambda *args: seen.append(args),
    )
    accounts = SimpleNamespace(credential_checks=detector,
        document=lambda: {'accounts': [{'platform': 'codeforces', 'handle': 'fixture'}]})
    asyncio.run(catalog.refresh_recommendation_sessions(accounts))
    assert seen == [('codeforces', 'fixture')]

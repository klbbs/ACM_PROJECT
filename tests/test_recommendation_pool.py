"""Selection must enforce login evidence and dates before any paid AI call."""
import asyncio
import json
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from acmweb import catalog
from selection_report_fixtures import with_selection_reports

from acmweb.ai import AIResult
from acmweb.codex_bridge import AIError
from acmweb.daily import Daily, Roll, Settings, TaskTime
from acmweb.database import Database
from fastapi import HTTPException


@pytest.fixture
def db(tmp_path): return Database(tmp_path / 'selection.sqlite3')


def accounts(*platforms):
    return SimpleNamespace(credential_checks=SimpleNamespace(statuses=lambda: {'items': [
        {'platform': p, 'status': 'authenticated', 'login_verified': True} for p in platforms]}))


def add(db, platform, key, stamp='2025-06-01T00:00:00+00:00', **extra):
    sources = {'codeforces': 'codeforces.contest.startTimeSeconds', 'atcoder': 'atcoder.archive.time',
               'luogu': 'luogu.problem.createTime', 'nowcoder': 'nowcoder.problem.publishTime'}
    row = catalog._problem(platform, key, key, difficulty=1000 if platform == 'codeforces' else None,
        contest_id=2000 if platform == 'codeforces' else None, published_at=catalog.publication_time(stamp),
        published_at_source=sources[platform], **extra)
    with db.connect() as conn: conn.execute('INSERT OR REPLACE INTO problem_catalog VALUES(?,?,?)', (platform, row['id'], json.dumps(row)))
    return row


def test_all_verified_platforms_mixed_without_native_rating_comparison(db):
    for i in range(50): add(db, 'codeforces', f'2000-{chr(65+i%20)}{1 if i>=20 else ""}')
    add(db, 'atcoder', 'abc400_a', native_difficulty=9999)
    add(db, 'luogu', 'P1001', native_difficulty=1)
    add(db, 'nowcoder', '1000', native_difficulty=3200)
    rows = catalog.recommendation_candidates(db, accounts(*catalog.PLATFORMS), minimum=900, maximum=1100, limit=8)
    assert {p['platform'] for p in rows} == set(catalog.PLATFORMS)
    assert len(rows) == len({p['id'] for p in rows}) == 8


def test_saved_cookie_unknown_or_mismatched_login_never_qualifies(db):
    add(db, 'atcoder', 'abc400_a')
    for source in (None, SimpleNamespace(document=lambda: {'accounts': [{'platform': 'atcoder', 'cookie': 'x=y'}]}),
        SimpleNamespace(credential_checks=SimpleNamespace(statuses=lambda: {'items': [
            {'platform': 'atcoder', 'status': 'account_mismatch', 'login_verified': False}]}))):
        with pytest.raises(AIError, match='检测登录状态'): catalog.recommendation_candidates(db, source)


def test_real_publication_cutoff_rejects_old_unknown_future_and_guessed_source(db):
    expected = add(db, 'codeforces', '1-A')['id']
    add(db, 'codeforces', '99990-B', stamp='2024-12-31T15:59:59+00:00')
    add(db, 'atcoder', 'abc400_a', stamp=None)
    add(db, 'luogu', 'P1001', stamp='2099-01-01T00:00:00+00:00')
    p = add(db, 'nowcoder', '1000')
    with db.connect() as conn:
        p['published_at_source'] = 'guessed_from_problem_id'
        conn.execute('UPDATE problem_catalog SET data=? WHERE id=?', (json.dumps(p), p['id']))
    db.set('training', {'published_after': '2025-01-01'})
    result = catalog.recommendation_candidates(db, accounts(*catalog.PLATFORMS), published_after='2000-01-01')
    assert [p['id'] for p in result] == [expected]
    status = catalog.recommendation_status(db, accounts(*catalog.PLATFORMS))
    assert sum(p['unknown_publication_count'] for p in status['platforms']) == 3
    with pytest.raises(AIError): catalog.recommendation_candidates(db, accounts('codeforces'), published_after='bad')


def test_attempted_feedback_and_battle_excluded_from_every_platform(db):
    for p, key in [('codeforces', '2000-A'), ('atcoder', 'abc400_a'), ('luogu', 'P1001'), ('nowcoder', '1000')]: add(db, p, key)
    with db.connect() as conn:
        conn.execute("INSERT INTO submissions VALUES('atcoder','demo','1','atcoder:abc400_a',1,'WA','{}',1)")
        conn.execute("INSERT INTO practice_feedback VALUES('luogu','P1001','skip','',1)")
    db.set('battle_problem_ids', ['codeforces:2000-A'])
    rows = catalog.recommendation_candidates(db, accounts(*catalog.PLATFORMS))
    assert [p['id'] for p in rows] == ['nowcoder:1000']


def test_cf_refresh_uses_finished_official_contest_timestamp(db, monkeypatch):
    class Fake:
        def json(self, url, **kwargs):
            if url.endswith('problemset.problems'):
                return {'status': 'OK', 'result': {'problems': [
                    {'contestId': 1, 'index': 'A', 'name': 'old id new date', 'rating': 1000},
                    {'contestId': 1, 'index': '14', 'name': 'unsupported numeric API index', 'rating': 1000},
                    {'contestId': 9999, 'index': 'B', 'name': 'running', 'rating': 1000}]}}
            return {'status': 'OK', 'result': [{'id': 1, 'phase': 'FINISHED', 'startTimeSeconds': 1735689600},
                                               {'id': 9999, 'phase': 'CODING', 'startTimeSeconds': 1735689600}]}
        def close(self): pass
    monkeypatch.setattr(catalog, 'Client', lambda **kw: Fake())
    result = catalog.refresh_catalog(db, 'codeforces')
    assert result['complete'] and result['count'] == 1
    row = catalog.candidates(db, published_after='2025-01-01')[0]
    assert row['contest_id'] == 1 and row['published_at'] == 1735689600


def test_discovery_partial_failure_preserves_existing_cache(db, monkeypatch):
    old = add(db, 'luogu', 'P1001')
    monkeypatch.setattr(catalog, 'Client', lambda **kw: SimpleNamespace(close=lambda: None))
    def unavailable(client): raise catalog.CrawlError('upstream secret must not be exposed')
    monkeypatch.setitem(catalog.DISCOVERY, 'luogu', unavailable)
    result = catalog.refresh_catalog(db, 'luogu')
    assert not result['complete'] and result['platforms'][0]['status'] == 'failed'
    assert 'secret' not in json.dumps(result)
    assert catalog.candidates(db, platform='luogu')[0]['id'] == old['id']


def test_official_non_cf_parsers_keep_native_metadata_and_missing_dates():
    archive = '<table><tbody><tr><td><time>2025-06-01 21:00:00+0900</time></td><td><a href="/contests/abc400">ABC</a></td></tr></tbody></table>'
    contest, stamp = catalog.parse_atcoder_archive(archive)[0]
    tasks = '<table><tbody><tr><td><a href="/contests/abc400/tasks/abc400_a">A</a></td><td><a href="/contests/abc400/tasks/abc400_a">A task</a></td></tr></tbody></table>'
    row = catalog.parse_atcoder_tasks(tasks, contest, stamp)[0]
    assert row['title'] == 'A task' and row['difficulty'] is None and row['published_at'] == stamp
    luogu = catalog.parse_luogu_catalog(json.dumps({'data': {'problems': {'result': [{'pid': 'P1001', 'title': 'A+B', 'difficulty': 1, 'tags': [2]}]}}}))[0]
    assert luogu['native_difficulty'] == 1 and luogu['published_at'] is None
    nowcoder = catalog.parse_nowcoder_catalog('<table><tr data-problemid="1000"><td>NC1000</td><td><a class="title" href="/acm/problem/1000">A+B</a></td><td>2400</td></tr></table>')[0]
    assert nowcoder['native_difficulty'] == 2400 and nowcoder['published_at'] is None


def test_daily_and_reroll_share_saved_date_and_login_constraints(db):
    add(db, 'codeforces', '2000-A', stamp='2024-01-01T00:00:00+00:00')
    add(db, 'atcoder', 'abc400_a'); add(db, 'atcoder', 'abc400_b')
    db.set('training', {'published_after': '2025-01-01'})
    ai = AsyncMock()
    async def choose(task, provider, model, payload, schema):
        assert all(p['platform'] == 'atcoder' and p['published_at'] for p in payload['candidates'])
        return AIResult({'selected_problems': [{'id': payload['candidates'][0]['id'], 'reason': '适合'}]}, {})
    ai.complete.side_effect = choose
    service = with_selection_reports(Daily(db, ai, accounts('codeforces', 'atcoder')))
    service.configure(Settings())
    first = asyncio.run(service.roll(Roll(provider='api', model='test')))['plan']['tasks'][0]
    next_task = asyncio.run(service.roll(Roll(provider='api', model='test', task_id=first['task_id'])))['plan']['tasks'][0]
    assert next_task['id'] != first['id'] and next_task['platform'] == 'atcoder'


def test_battle_ac_counts_ordinary_but_never_daily_task_or_k_or_time(db):
    ai = AsyncMock(); service = with_selection_reports(Daily(db, ai, accounts('codeforces')))
    day = service.today().isoformat(); now = int(time.time())
    p = add(db, 'codeforces', '2000-A')
    service.configure(Settings())
    with db.connect() as conn:
        conn.execute('INSERT INTO daily_plans VALUES(?,?,?,?,?,?)', (day, 'ready', 1, day, day, now))
        task_id = conn.execute('INSERT INTO daily_plan_tasks(day,ordinal,problem_id,data,created_at) VALUES(?,?,?,?,?)', (day, 0, p['id'], json.dumps(p), now)).lastrowid
        conn.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)', ('codeforces', 'demo', 's1', '2000-A', now, 'AC', '{}', now))
    db.set('battle_problem_ids', [p['id']])
    state = service.state()
    assert state['completed'] == 1 and state['checkin']['kind'] == 'normal'
    assert not state['plan']['tasks'][0]['completed'] and state['checkin']['all_k'] == 0
    with pytest.raises(HTTPException): service.save_time(TaskTime(task_id=task_id, duration_minutes=10))
    assert service.evidence() == []


def test_saved_ai_choices_remain_visible_only_while_still_eligible(db):
    wanted = add(db, 'codeforces', '2000-A')['id']
    old = add(db, 'atcoder', 'abc400_a', stamp='2024-01-01T00:00:00+00:00')['id']
    unverified = add(db, 'luogu', 'P1001')['id']
    for i in range(1, 40): add(db, 'codeforces', f'{2000+i}-B')
    db.set('training', {'published_after': '2025-01-01'})
    rows = catalog.recommendation_candidates(db, accounts('codeforces', 'atcoder'), limit=4, preferred_ids=[wanted, old, unverified])
    assert rows[0]['id'] == wanted and old not in {p['id'] for p in rows} and unverified not in {p['id'] for p in rows}
    db.set('battle_problem_ids', [wanted])
    assert wanted not in {p['id'] for p in catalog.recommendation_candidates(db, accounts('codeforces'), preferred_ids=[wanted])}


def test_explicit_selection_renews_expired_sessions_only_and_skips_public_accounts(db):
    checked = []
    class Checks:
        def statuses(self): return {'items': [{'platform': 'atcoder', 'handle': 'fresh', 'login_verified': True}]}
        def credential(self, platform, handle): return ('private=value' if handle != 'public' else '', 'fingerprint')
        def check(self, platform, handle): checked.append((platform, handle))
    source = SimpleNamespace(credential_checks=Checks(), document=lambda: {'accounts': [
        {'platform': 'atcoder', 'handle': 'fresh'}, {'platform': 'codeforces', 'handle': 'expired'},
        {'platform': 'luogu', 'handle': 'public'}]})
    asyncio.run(catalog.refresh_recommendation_sessions(source))
    assert checked == [('codeforces', 'expired')]


def test_selection_session_refresh_respects_busy_checks_without_assuming_login(db):
    class Checks:
        def statuses(self): return {'items': []}
        def credential(self, *args): return ('private=value', 'fingerprint')
        def check(self, *args): raise HTTPException(429, 'busy')
    source = SimpleNamespace(credential_checks=Checks(), document=lambda: {'accounts': [{'platform': 'codeforces', 'handle': 'demo'}]})
    asyncio.run(catalog.refresh_recommendation_sessions(source))
    with pytest.raises(AIError, match='没有已验证'): catalog.recommendation_candidates(db, source)


def test_battle_allocated_while_ai_selects_does_not_create_daily_task(db):
    row = add(db, 'codeforces', '2000-A'); ai = AsyncMock()
    async def race(task, provider, model, payload, schema):
        db.set('battle_problem_ids', [row['id']])
        return AIResult({'selected_problems': [{'id': row['id'], 'reason': '适合'}]}, {})
    ai.complete.side_effect = race
    service = with_selection_reports(Daily(db, ai, accounts('codeforces'))); service.configure(Settings())
    with pytest.raises(AIError, match='候选资格已变化'):
        asyncio.run(service.roll(Roll(provider='api', model='test')))
    state = service.state()
    assert state['plan']['status'] == 'failed'
    assert state['plan']['goal_count'] == state['rerolls_limit'] == state['rerolls_remaining'] == 1
    assert state['rerolls_used'] == 0
    with db.connect() as conn: assert conn.execute('SELECT COUNT(*) FROM daily_plan_tasks').fetchone()[0] == 0


def test_legacy_contest_number_requires_explicit_saved_date_or_clear(db):
    add(db, 'codeforces', '2000-A')
    db.set('training', {'min_contest': 1800})
    status = catalog.recommendation_status(db, accounts('codeforces'))
    assert status['requires_publication_date'] and '最小比赛编号' in status['notice']
    assert all(p['date_eligible_count'] == 0 for p in status['platforms'])
    for temporary_date in (None, '2025-01-01'):
        with pytest.raises(AIError, match='保存真实的最早发布日期'):
            catalog.recommendation_candidates(db, accounts('codeforces'), published_after=temporary_date)
    # Saving an actual date is an explicit migration; a request override is not.
    db.set('training', {'min_contest': 1800, 'published_after': '2025-01-01'})
    assert not catalog.recommendation_status(db, accounts('codeforces'))['requires_publication_date']
    assert len(catalog.recommendation_candidates(db, accounts('codeforces'))) == 1
    db.set('training', {'min_contest': 0})
    assert not catalog.recommendation_status(db, accounts('codeforces'))['requires_publication_date']
    assert len(catalog.recommendation_candidates(db, accounts('codeforces'))) == 1


def test_legacy_constraint_blocks_daily_before_any_ai_request(db):
    add(db, 'codeforces', '2000-A'); ai = AsyncMock()
    db.set('training', {'min_contest': 1800})
    daily = with_selection_reports(Daily(db, ai, accounts('codeforces'))); daily.configure(Settings())
    with pytest.raises(AIError, match='最小比赛编号'):
        asyncio.run(daily.roll(Roll(provider='api', model='test')))
    ai.complete.assert_not_called()
    assert daily.state()['plan']['status'] == 'failed'

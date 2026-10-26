"""Local synthetic Gym evidence; no real contest or successful login is claimed."""
import json
import time
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.database import Database
from acmweb.gym_submissions import contest_from_url, collect_contest, parse_page, sync_gym, _timestamp, start_of_day
from acmweb.problem_sources import normalize, source_url, capabilities
from acmweb.settings import Settings
from ojcrawler.models import CrawlError


CONTEST = 700001


def row(sid=101, index='A', *, ts=None, state='OK', handle='demo'):
    return {'id': sid, 'creationTimeSeconds': ts or int(time.time()), 'verdict': state,
            'author': {'participantType': 'VIRTUAL', 'members': [{'handle': handle}]},
            'problem': {'contestId': CONTEST, 'index': index, 'name': 'Synthetic Gym', 'tags': ['dp']}}


def page(*, ts=None, state='OK', handle='demo', zone=True):
    timestamp = f'data-timestamp="{ts or int(time.time())}"' if zone else ''
    return f'''<table class="status-frame-datatable"><tr data-submission-id="101">
    <td>101</td><td><span class="format-time" {timestamp}>Oct/25/2026 10:00</span></td>
    <td><a href="/profile/{handle}">{handle}</a></td>
    <td><a href="/gym/{CONTEST}/problem/A">A - Synthetic Gym</a></td>
    <td>GNU C++20</td><td><span submissionVerdict="{state}">{state}</span></td>
    </tr></table>'''


@pytest.mark.parametrize('suffix', ['', '/submission/101', '/problem/A', '/status', '/my'])
def test_fixed_origin_scope(suffix):
    assert contest_from_url(f'https://codeforces.com/gym/{CONTEST}{suffix}') == CONTEST


@pytest.mark.parametrize('url', ['http://codeforces.com/gym/700001', 'https://evil.example/gym/700001',
    'https://codeforces.com@evil.example/gym/700001', 'https://codeforces.com:443/gym/700001',
    'https://codeforces.com/gym/700001?cookie=x', 'https://codeforces.com/gym/700001#x',
    'https://codeforces.com/contest/700001', 'https://codeforces.com/gym/1',
    'https://codeforces.com/gym/700001/../../admin'])
def test_scope_rejects_untrusted_or_ambiguous_urls(url):
    with pytest.raises(ValueError):
        contest_from_url(url)


def test_public_gym_api_accepts_virtual_but_verifies_owner_contest_time_and_final_state():
    now = int(time.time())
    values = [row(), row(102, 'B', state='TESTING'), row(103, 'C', handle='other'), row(104, 'D', ts=1)]
    client = SimpleNamespace(json=lambda *args, **kwargs: {'status': 'OK', 'result': values})
    result = collect_contest(client, CONTEST, 'DEMO', now - 10)
    assert [(r.problem_id, r.verdict) for r in result] == [('700001-A', 'AC'), ('700001-B', 'PENDING')]
    assert all('/gym/700001/submission/' in r.url for r in result)
    values[0]['problem']['contestId'] = 700002
    client.get = lambda *args, **kwargs: '<div>unrelated page</div>'
    with pytest.raises(CrawlError):
        collect_contest(client, CONTEST, 'demo', now - 10)


def test_private_gym_falls_back_to_official_page_without_accepting_url_as_evidence():
    client = SimpleNamespace(json=lambda *args, **kwargs: (_ for _ in ()).throw(CrawlError('HTTP 400')),
        get=lambda *args, **kwargs: page())
    result = collect_contest(client, CONTEST, 'demo', 0)
    assert len(result) == 1 and result[0].verdict == 'AC'
    assert result[0].raw['source'] == 'official_gym_page'
    assert parse_page(page(handle='other'), CONTEST, 'demo')[0] == []


@pytest.mark.parametrize('html', ['<title>Just a moment...</title>', '<input type="password">',
    '<table><tr><td>OK</td></tr></table>', page(zone=False)])
def test_unreadable_or_unzoned_pages_are_not_empty_success_or_fabricated_ac(html):
    with pytest.raises(CrawlError):
        parse_page(html, CONTEST, 'demo')


def test_explicit_time_offset_and_iso_match_beijing_midnight():
    node = BeautifulSoup('<span>Oct/24/2026 19:00UTC+3</span>', 'html.parser').span
    expected = int(datetime(2026, 10, 25, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())
    assert _timestamp(node) == expected
    node = BeautifulSoup('<time datetime="2026-10-24T16:00:00Z"></time>', 'html.parser').time
    assert _timestamp(node) == expected


@pytest.mark.parametrize('value', ['2099-01-01', '2000-01-01', '2026-1-2', '2026-02-30'])
def test_sync_day_rejects_future_out_of_range_and_noncanonical_dates(value):
    with pytest.raises(ValueError):
        start_of_day(value)


def test_scope_persists_without_overwriting_history_watermark_and_errors_are_safe(tmp_path, monkeypatch):
    import acmweb.gym_submissions as gym
    db = Database(tmp_path / 'db')
    docs = {'accounts': [{'platform': 'codeforces', 'handle': 'demo', 'cookie': 'session=private-fixture'}]}
    accounts = SimpleNamespace(document=lambda: docs)
    settings = SimpleNamespace(isolated=True)
    calls = []
    class Client:
        def __init__(self, **kwargs): pass
        def json(self, url, params):
            calls.append(params['contestId'])
            return {'status': 'OK', 'result': [row()]}
        def close(self): pass
    monkeypatch.setattr(gym, 'Client', Client)
    with db.connect() as connection:
        connection.execute("INSERT INTO accounts VALUES('codeforces','demo',123)")
    result = sync_gym(db, accounts, settings, url='https://codeforces.com/gym/700001/submission/101')
    assert result[0]['count'] == 1 and result[0]['complete']
    assert db.get('cf_gym_scopes') == {'demo': [CONTEST]}
    assert sync_gym(db, accounts, settings)[0]['count'] == 1
    assert calls == [CONTEST, CONTEST]
    with db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM submissions').fetchone()[0] == 1
        assert connection.execute('SELECT watermark FROM accounts').fetchone()[0] == 123
        assert connection.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0] == 0
    monkeypatch.setattr(gym, 'collect_contest', lambda *args, **kwargs: (_ for _ in ()).throw(CrawlError('Cookie=SECRET upstream HTML')))
    result = sync_gym(db, accounts, settings)
    assert not result[0]['complete'] and 'SECRET' not in json.dumps(result)
    with db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM submissions').fetchone()[0] == 1
    other = Database(tmp_path / 'other')
    assert sync_gym(other, accounts, settings) == []


def test_gym_problem_links_are_preserved_and_remote_send_is_not_falsely_enabled():
    pid = normalize('https://codeforces.com/gym/700001/problem/A')
    assert pid == 'codeforces:700001-A'
    assert source_url(pid) == 'https://codeforces.com/gym/700001/problem/A'
    assert capabilities(pid, [])['auto_submit'] is False
    from acmweb.remote_judges import PlatformSession, SubmissionError
    driver = PlatformSession('codeforces', 'JSESSIONID=synthetic-no-network')
    try:
        with pytest.raises(SubmissionError) as error:
            driver.options(pid, 'demo')
        assert error.value.reason == 'unsupported'
    finally:
        driver.close()


def test_gym_query_budget_prevents_fallback_requests_after_deadline():
    client = SimpleNamespace(json=lambda *args, **kwargs: pytest.fail('No request after deadline'),
        get=lambda *args, **kwargs: pytest.fail('No fallback after deadline'))
    with pytest.raises(CrawlError, match='时间超出'):
        collect_contest(client, CONTEST, 'demo', 0, deadline=time.monotonic() - 1)


def test_a_new_scope_saved_during_network_io_is_not_overwritten(tmp_path, monkeypatch):
    import acmweb.gym_submissions as gym
    db = Database(tmp_path/'db')
    accounts = SimpleNamespace(document=lambda: {'accounts': [{'platform': 'codeforces', 'handle': 'demo'}]})
    def another_task_saves(*args, **kwargs):
        db.set('cf_gym_scopes', {'demo': [700001, 700002]})
        return []
    monkeypatch.setattr(gym, 'Client', lambda **kwargs: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(gym, 'collect_contest', another_task_saves)
    sync_gym(db, accounts, SimpleNamespace(isolated=True), url='https://codeforces.com/gym/700001')
    assert db.get('cf_gym_scopes') == {'demo': [700001, 700002]}


def test_26_old_gym_contests_do_not_block_normal_sync_or_become_permanent_scopes(tmp_path, monkeypatch):
    import asyncio
    import acmweb.gym_submissions as gym
    import acmweb.jobs as jobs_module
    from acmweb.jobs import Jobs
    from ojcrawler.storage import Store
    db = Database(tmp_path/'db')
    now = int(time.time())
    with db.connect() as connection:
        for index in range(26):
            connection.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)',
                ('codeforces', 'demo', str(index + 1), f'{700001 + index}-A', now - 30 * 86400, 'AC', '{}', now))
    monkeypatch.setattr(jobs_module, 'load', lambda path: SimpleNamespace())
    def normal_sync(config, args):
        store = Store(config.database)
        try:
            store.save_run({'run_id': 'regular-sync-with-26-old-gyms', 'collected_at': now, 'accounts': [
                {'platform': 'codeforces', 'account': 'demo', 'complete': True, 'error': None, 'warnings': []}]})
        finally:
            store.close()
        return 0
    monkeypatch.setattr(jobs_module, 'crawl', normal_sync)
    monkeypatch.setattr(gym, 'collect_contest', lambda *args, **kwargs: pytest.fail('Inactive history must not be fetched'))
    accounts = SimpleNamespace(effective_path=tmp_path/'accounts.toml',
        document=lambda: {'accounts': [{'platform': 'codeforces', 'handle': 'demo'}]})
    jobs = Jobs(db, SimpleNamespace(database=db.path, data_dir=tmp_path, isolated=True), None, accounts)
    async def run():
        job = jobs.start('crawl')
        await jobs.tasks[job['id']][1]
        return jobs.list()[0]
    result = asyncio.run(run())
    assert result['status'] == 'completed' and result['result']['complete']
    assert result['result']['accounts'][0]['complete']
    assert result['result']['gym_sync'] == []
    assert db.get('cf_gym_scopes') == {'demo': []}
    with db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM submissions').fetchone()[0] == 26


def test_more_than_20_scopes_rotate_without_aborting_and_prioritize_requested_contest(tmp_path, monkeypatch):
    import acmweb.gym_submissions as gym
    db = Database(tmp_path/'db')
    old = list(range(700001, 700027))
    db.set('cf_gym_scopes', {'demo': old})
    accounts = SimpleNamespace(document=lambda: {'accounts': [{'platform': 'codeforces', 'handle': 'demo'}]})
    monkeypatch.setattr(gym, 'Client', lambda **kwargs: SimpleNamespace(close=lambda: None))
    calls = []
    monkeypatch.setattr(gym, 'collect_contest', lambda client, contest, *args, **kwargs: calls.append(contest) or [])
    first = sync_gym(db, accounts, SimpleNamespace(isolated=True))
    assert len(calls) == 20 and calls == old[:20]
    assert first[-1]['error_code'] == 'scope_deferred' and first[-1]['deferred_count'] == 6
    second = sync_gym(db, accounts, SimpleNamespace(isolated=True))
    assert calls[20:26] == old[20:]
    assert set(calls) == set(old) and second[-1]['deferred_count'] == 6
    sync_gym(db, accounts, SimpleNamespace(isolated=True), url='https://codeforces.com/gym/800001')
    assert calls[40] == 800001 and len(calls[40:]) == 20
    assert set(db.get('cf_gym_scopes')['demo']) == set(old + [800001])


def test_crawl_endpoint_validates_scope_before_scheduling_and_keeps_existing_no_body_contract(tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path, config_path=tmp_path/'missing', enable_codex=False))
    monkeypatch.setattr(app.state.jobs, 'start', lambda kind, payload=None: {'id': 'synthetic', 'payload': payload})
    with TestClient(app) as client:
        assert client.put('/api/accounts', json={'items': [{'platform': 'codeforces', 'handle': 'demo'}]}).status_code == 200
        assert client.post('/api/crawl', json={'gym_url': 'https://evil.example/gym/700001'}).status_code == 422
        assert client.post('/api/crawl', json={'code': 'not accepted'}).status_code == 422
        assert client.post('/api/crawl', json={'day': '2099-01-01'}).status_code == 422
        response = client.post('/api/crawl', json={'gym_url': 'https://codeforces.com/gym/700001'})
        assert response.status_code == 200 and response.json()['payload']['gym_url'].endswith('/700001')
        assert client.post('/api/crawl').status_code == 200

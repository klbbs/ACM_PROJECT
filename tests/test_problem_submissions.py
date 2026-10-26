"""No live OJ requests: test one-click sync independently of any editor code."""
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.codex_bridge import AIError
from acmweb.settings import Settings
from ojcrawler.models import Collection, Submission


@pytest.fixture
def site(tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path, config_path=tmp_path/'absent.toml',
                              enable_codex=False, multi_user=False))
    with TestClient(app) as client:
        assert client.put('/api/accounts', json={'items':[{'platform':'codeforces', 'handle':'demo',
                           'cookie':'JSESSIONID=synthetic-only', 'enrich':False}]}).status_code == 200
        service = app.state.problem_submissions
        plan = {'result':Collection('codeforces','demo', submissions=[Submission('12345','4-A',int(time.time()),'WA',language='Python 3')], complete=True)}
        monkeypatch.setattr(service, '_collect', lambda *_: (plan['result'], 'synthetic current problem'))
        yield client, app, service, plan


def test_sync_needs_no_editor_code_and_preserves_watermark_profile(site):
    client, app, service, plan = site
    with app.state.database.connect() as db:
        db.execute('INSERT INTO accounts VALUES(?,?,?)', ('codeforces','demo',456))
    result = client.post('/api/problem-submissions/4-A/sync', json={'handle':'demo'})
    assert result.status_code == 200, result.text
    # Wait for the fixture's local background task, not for a real platform.
    for _ in range(100):
        jobs = client.get('/api/jobs').json()['items']
        job = next(j for j in jobs if j['id'] == result.json()['id'])
        if job['status'] not in {'queued','running'}:
            break
        time.sleep(.01)
    assert job['status'] == 'completed', job
    rows = client.get('/api/problem-submissions/codeforces:4-A?handle=demo').json()
    assert rows['items'][0]['submission_id'] == '12345'
    assert rows['items'][0]['attempt_id'] is None
    assert not rows['items'][0]['has_local_code']
    assert rows['sync_status']['status'] == 'completed'
    assert 'code' not in rows['items'][0]
    with app.state.database.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM wb_drafts').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM wb_attempts').fetchone()[0] == 0
        assert db.execute('SELECT watermark FROM accounts').fetchone()[0] == 456
        assert db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0] == 0
    assert client.post('/api/problem-submissions/4-A/sync', json={'handle':'demo'}).status_code == 429


def test_sync_partial_and_empty_keep_previous_results(site):
    client, app, service, plan = site
    import asyncio
    asyncio.run(service.sync('codeforces:4-A', 'demo'))
    plan['result'] = Collection('codeforces','demo', complete=False, error='Synthetic permission failure')
    with pytest.raises(AIError,match='未能读取'):
        asyncio.run(service.sync('codeforces:4-A', 'demo'))
    rows = service.listing('codeforces:4-A','demo')
    assert len(rows['items']) == 1
    assert rows['sync_status']['status'] == 'failed'
    assert rows['sync_status']['error'] == 'Synthetic permission failure'


def test_sync_rejects_other_accounts_extra_source_and_wrong_problem(site):
    client, app, service, plan = site
    assert client.post('/api/problem-submissions/4-A/sync', json={'handle':'stranger'}).status_code == 400
    assert client.post('/api/problem-submissions/4-A/sync', json={'handle':'demo','code':'do not require code'}).status_code == 422
    assert client.post('/api/problem-submissions/https%3A%2F%2Fevil.test/sync', json={'handle':'demo'}).status_code >= 400
    plan['result'].submissions[0].problem_id = '5-A'
    import asyncio
    with pytest.raises(Exception, match='题号'):
        asyncio.run(service.sync('codeforces:4-A','demo'))
    assert service.listing('codeforces:4-A')['items'] == []


def test_official_source_creates_review_record_without_changing_draft(site, monkeypatch):
    client, app, service, plan = site
    stamp = int(time.time())
    class Driver:
        def source(self, *_):
            return {'id':'12345','account':'demo','problem_id':'4-A','submitted_at':stamp,
                    'verdict':'WA','language':'Python 3','code':'print("official source")',
                    'url':'https://codeforces.com/contest/4/submission/12345'}
        def close(self):
            pass
    monkeypatch.setattr(service.remote,'_driver',lambda *_:Driver())
    response = client.post('/api/problem-submissions/4-A/source', json={'handle':'demo','submission_id':'12345'})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['code_verified'] and value['provenance'] == 'official'
    attempt = app.state.workbench.attempt(value['attempt_id'])
    assert attempt['code'] == 'print("official source")'
    assert attempt['code_verified']
    assert service.listing('4-A')['items'][0]['code_verified']
    with app.state.database.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM wb_drafts').fetchone()[0] == 0
    # Reading a source twice reuses its immutable record; no upstream refetch.
    monkeypatch.setattr(service.remote,'_driver',lambda *_:pytest.fail('duplicate source request'))
    assert client.post('/api/problem-submissions/4-A/source', json={'handle':'demo','submission_id':'12345'}).json()['attempt_id'] == value['attempt_id']


@pytest.mark.parametrize('allowed', [True, False])
def test_source_route_reads_dynamic_cf_entry_without_using_editor_draft(site, monkeypatch, allowed):
    """Real service/driver chain, synthetic upstream HTML and source only."""
    import asyncio
    import json
    from acmweb.remote_judges import PlatformSession
    client, app, service, _ = site
    asyncio.run(service.sync('codeforces:4-A', 'demo'))
    draft = {'language':'python3','code':'print("keep editor draft")','revision':0}
    assert client.put('/api/workbench/problem/4-A/draft', json=draft).status_code == 200
    calls = []
    stamp = int(time.time())
    def driver(*_):
        session = PlatformSession('codeforces', 'JSESSIONID=synthetic-only')
        def request(method, url, *, data=None, headers=None):
            calls.append((method, url, data))
            if '/api/' in url:
                return 200, {}, json.dumps({'status':'OK', 'result':[{'id':12345,
                    'problem':{'contestId':4,'index':'A'}, 'author':{'members':[{'handle':'demo'}]},
                    'creationTimeSeconds':stamp,'verdict':'WRONG_ANSWER','programmingLanguage':'Python 3'}]})
            if '/contest/4/submission/' in url:
                return 403, {}, '<title>Just a moment...</title>'
            if '/submissions/demo' in url:
                ids = '["12345"]' if allowed else '[]'
                return 200, {}, f'''<meta name="X-Csrf-Token" content="{'a'*32}">
                    <div class="lang-chooser"><a href="/profile/demo">demo</a><a href="/logout">Logout</a></div>
                    <table><tr data-submission-id="12345"><td class="id-cell"><span class="hiddenSource">12345</span></td>
                    <td><a href="/profile/demo">demo</a></td><td><a href="/contest/4/problem/A">A</a></td></tr></table>
                    <script>const viewableSubmissionIds = {ids};
                    if (viewableSubmissionIds.indexOf(submissionId) != -1) {{ show = true; }}
                    $.post('/data/submitSource', {{submissionId: id}});</script>'''
            assert method == 'POST' and '/data/submitSource?rv=' in url
            assert data == {'submissionId':'12345','csrf_token':'a'*32}
            return 200, {}, json.dumps({'submissionId':12345,'source':'print("official fixture")'})
        session._request = request
        return session
    monkeypatch.setattr(service.remote, '_driver', driver)
    response = client.post('/api/problem-submissions/4-A/source', json={'handle':'demo','submission_id':'12345'})
    assert response.status_code == (200 if allowed else 400), response.text
    posts = [call for call in calls if call[0] == 'POST']
    assert len(posts) == int(allowed)
    with app.state.database.connect() as db:
        saved = db.execute('SELECT code,revision FROM wb_drafts WHERE problem_id=?', ('codeforces:4-A',)).fetchone()
        assert saved['code'] == draft['code'] and saved['revision'] == 1
        assert db.execute('SELECT count(*) FROM wb_official_sources').fetchone()[0] == int(allowed)
    if allowed:
        result = response.json()
        assert result['code_verified'] and result['provenance'] == 'official'
        assert result['code'] == 'print("official fixture")'
        assert result['code'] != draft['code']
        assert service.listing('4-A','demo')['items'][0]['code_verified']
    else:
        assert '尚不可核验' in response.json()['detail']
        assert '不一致' not in response.json()['detail']


def test_source_upstream_rate_limit_has_retry_after_and_keeps_history(site, monkeypatch):
    import asyncio
    from acmweb.remote_judges import SubmissionError
    client, app, service, _ = site
    asyncio.run(service.sync('codeforces:4-A', 'demo'))
    class Driver:
        def source(self, *_):
            raise SubmissionError('rate_limited')
        def close(self):
            pass
    monkeypatch.setattr(service.remote, '_driver', lambda *_: Driver())
    response = client.post('/api/problem-submissions/4-A/source', json={'handle': 'demo', 'submission_id': '12345'})
    assert response.status_code == 429 and response.headers['Retry-After'] == '60'
    assert '官方平台限制' in response.json()['detail']
    assert len(service.listing('4-A', 'demo')['items']) == 1


def test_source_challenge_explains_source_block_without_requesting_resubmission(site, monkeypatch):
    import asyncio
    from acmweb.remote_judges import SubmissionError
    client, app, service, _ = site
    asyncio.run(service.sync('codeforces:4-A', 'demo'))
    class Log:
        def emit(self, event, **fields):
            events.append((event, fields))
    class Driver:
        def source(self, *_):
            raise SubmissionError('challenge')
        def close(self):
            pass
    events = []
    monkeypatch.setattr(service.jobs, 'event_log', Log())
    monkeypatch.setattr(service.remote, '_driver', lambda *_: Driver())
    response = client.post('/api/problem-submissions/4-A/source', json={'handle':'demo','submission_id':'12345'})
    assert response.status_code == 400
    assert '登录正常时也可能发生' in response.json()['detail']
    assert '无需重新提交' in response.json()['detail']
    assert '请到官方页面提交' not in response.json()['detail']
    assert events == [('platform.source_failed', {'level':'WARNING','platform':'codeforces','reason':'source_challenge'})]
    assert len(service.listing('4-A','demo')['items']) == 1
    with app.state.database.connect() as db:
        assert db.execute('SELECT count(*) FROM wb_official_sources').fetchone()[0] == 0


@pytest.mark.parametrize('reason', ['record_mismatch', 'record_unavailable', 'source_unavailable',
                                   'account_mismatch', 'access_restricted'])
def test_source_diagnostics_keep_specific_safe_reason_and_no_partial_code(site, monkeypatch, reason):
    import asyncio
    from acmweb.remote_judges import SubmissionError
    from acmweb.runtime_logs import REASONS
    client, app, service, _ = site
    asyncio.run(service.sync('codeforces:4-A', 'demo'))
    events = []
    class Log:
        def emit(self, event, **fields):
            events.append((event, fields))
    class Driver:
        def source(self, *_):
            raise SubmissionError(reason)
        def close(self):
            pass
    monkeypatch.setattr(service.jobs, 'event_log', Log())
    monkeypatch.setattr(service.remote, '_driver', lambda *_: Driver())
    response = client.post('/api/problem-submissions/4-A/source', json={'handle':'demo','submission_id':'12345'})
    assert response.status_code == 400
    expected = reason if reason == 'source_unavailable' else 'source_' + reason
    assert expected in REASONS
    assert events == [('platform.source_failed', {'level':'WARNING','platform':'codeforces','reason':expected})]
    if reason == 'source_unavailable':
        assert '尚不可核验' in response.json()['detail']
        assert '不一致' not in response.json()['detail']
    assert len(service.listing('4-A','demo')['items']) == 1
    with app.state.database.connect() as db:
        assert db.execute('SELECT count(*) FROM wb_official_sources').fetchone()[0] == 0


@pytest.mark.parametrize('changed', [ {'account':'stranger'}, {'problem_id':'5-A'}, {'id':'67890'}, {'submitted_at':int(time.time())+3600} ])
def test_official_source_rejects_wrong_evidence(site, monkeypatch, changed):
    client, app, service, plan = site
    class Driver:
        def source(self, *_):
            return {'id':'12345','account':'demo','problem_id':'4-A','submitted_at':int(time.time()),
                    'verdict':'WA','language':'Python 3','code':'print(1)', **changed}
        def close(self):
            pass
    monkeypatch.setattr(service.remote,'_driver',lambda *_:Driver())
    assert client.post('/api/problem-submissions/4-A/source', json={'handle':'demo','submission_id':'12345'}).status_code == 400
    assert service.listing('4-A')['items'] == []


def test_sync_does_not_automatically_analyze_submissions(site, monkeypatch):
    client, app, service, plan = site
    review = AsyncMock(side_effect=RuntimeError('SYNTHETIC SECRET'))
    monkeypatch.setattr(app.state.workbench,'automatic_review',review)
    # Existing immutable local code is associated by the server's send path.
    service.remote.record({'problem_id':'codeforces:4-A','handle':'demo','submission_id':'12345',
        'sent_at':int(time.time()),'created_at':int(time.time()),'language':'python3','code':'print(1)',
        'code_hash':'fixture-hash'}, {'id':'12345','account':'demo','problem_id':'4-A','submitted_at':int(time.time()),'verdict':'WA'})
    import asyncio
    result = asyncio.run(service.sync('codeforces:4-A','demo'))
    assert result['count'] == 1
    assert review.await_count == 0
    assert service.listing('4-A')['items'][0]['verdict'] == 'WA'


@pytest.mark.parametrize('platform,pid,handle', [('atcoder','atcoder:abc001_1','demo'),
    ('luogu','luogu:P1001','123'), ('nowcoder','nowcoder:1000','123')])
def test_other_platform_collection_is_bounded_and_filters_to_current_problem(site,monkeypatch,platform,pid,handle):
    client, app, service, plan = site
    from ojcrawler.adapters import ADAPTERS
    from acmweb.problem_submissions import ProblemSubmissions
    # Restore the real collector; replace only the external adapter I/O.
    monkeypatch.setattr(service,'_collect',ProblemSubmissions._collect.__get__(service))
    captured = {}
    def fake_fetch(adapter, since):
        captured.update(since=since,max_pages=adapter.max_pages,page_size=adapter.page_size)
        return Collection(platform,handle, submissions=[
            Submission('12345',pid.split(':')[1],int(time.time()),'AC'),
            Submission('67890','some-other-problem',int(time.time()),'WA')],complete=True)
    monkeypatch.setattr(ADAPTERS[platform],'collect',fake_fetch)
    out, coverage = service._collect(pid,{'platform':platform,'handle':handle})
    assert len(out.submissions) == 1 and out.submissions[0].id == '12345'
    assert captured['max_pages'] == 3 and captured['page_size'] == 100
    assert (captured['since'] > time.time()-8*86400) if platform == 'atcoder' else captured['since'] == 0
    assert coverage


def test_cf_sync_queries_only_this_contest_and_checks_actual_owner(site,monkeypatch):
    client, app, service, plan = site
    from acmweb.problem_submissions import ProblemSubmissions
    from ojcrawler.http import Client
    monkeypatch.setattr(service,'_collect',ProblemSubmissions._collect.__get__(service))
    requests = []
    def fake_json(client,url,params=None):
        requests.append((url,params))
        return {'status':'OK','result':[{'id':12345,'creationTimeSeconds':int(time.time()),
            'author':{'members':[{'handle':'DEMO'}]},'problem':{'contestId':4,'index':'A'},'verdict':'WRONG_ANSWER'}]}
    monkeypatch.setattr(Client,'json',fake_json)
    out, _ = service._collect('codeforces:4-A',{'platform':'codeforces','handle':'demo'})
    assert out.submissions[0].verdict == 'WA'
    assert requests[0][1]['contestId'] == 4
    assert requests[0][1]['handle'] == 'demo'
    with pytest.raises(AIError, match='账号不匹配'):
        service._collect('codeforces:4-A',{'platform':'codeforces','handle':'someone-else'})


def test_slow_problem_collection_uses_query_start_time_and_preserves_newer_judge(site, monkeypatch):
    """A queued response fetched before a newer AC must not win by saving later."""
    import asyncio
    from ojcrawler.storage import Store
    _, app, service, _ = site
    start = int(time.time())
    clock = [start]
    monkeypatch.setattr('acmweb.problem_submissions.time.time', lambda: clock[0])
    def delayed_collection(*_args):
        clock[0] = start + 1
        newer = Store(app.state.database.path)
        try:
            newer.save('newer-official-poll', clock[0], Collection('codeforces', 'demo', submissions=[
                Submission('12345','4-A',start,'AC',raw={'verdict':'OK','creationTimeSeconds':start})]),
                capture_snapshot=False)
        finally:
            newer.close()
        clock[0] = start + 2
        return Collection('codeforces','demo',submissions=[
            Submission('12345','4-A',start,'PENDING',raw={'verdict':'TESTING','creationTimeSeconds':start})]), 'synthetic delayed response'
    monkeypatch.setattr(service, '_collect', delayed_collection)
    reconcile = AsyncMock()
    monkeypatch.setattr(service.remote, 'reconcile_collected', reconcile)
    result = asyncio.run(service.sync('codeforces:4-A', 'demo'))
    assert result['count'] == 1
    with app.state.database.connect() as db:
        row = db.execute("SELECT verdict,updated_at FROM submissions WHERE id='12345'").fetchone()
        assert tuple(row) == ('AC', start + 1)
    assert reconcile.await_args.kwargs['collected_at'] == start

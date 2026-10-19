import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from acmweb.app import create_app
from acmweb.settings import Settings
from acmweb.daily import Settings as DailySettings
from acmweb.codex_bridge import AIError
from acmweb.workbench import Draft, Link, Coach, normalize, parse_statement, training_evidence
from ojcrawler.models import Collection, Submission, verdict
from ojcrawler.storage import Store

HTML = '''<div class="problem-statement"><div class="header"><div class="title">A. Test</div><div class="time-limit">1 second</div></div><div><p>Find a number.</p><script>evil()</script><img src="https://evil.invalid/x"></div><div class="input-specification">Input n</div><div class="sample-tests"><div class="sample-test"><div class="input"><pre><div class="test-example-line">2</div><div class="test-example-line">3</div></pre></div><div class="output"><pre>5<br/>6</pre></div></div></div></div>'''


@pytest.fixture
def workspace(tmp_path):
    config = tmp_path/'account.toml'
    config.write_text('[[accounts]]\nplatform="codeforces"\nhandle="demo"\ncookie="session=TOP-SECRET"\n')
    app = create_app(Settings(data_dir=tmp_path, config_path=config, enable_codex=False))
    service = app.state.workbench
    with service.db.connect() as db:
        db.execute('INSERT INTO wb_problems VALUES(?,?,?)', ('codeforces:4-A', json.dumps(parse_statement(HTML, 'codeforces:4-A')), int(time.time())))
    with TestClient(app) as client:
        yield client, service, app


def snapshot(service):
    service.draft('4-A', Draft(code='int main() { return 0; }', language='cpp17', revision=0))
    return service.snapshot('4-A', 1)['id']


def mock_judge(monkeypatch, aid=None, status='WRONG_ANSWER', **changes):
    row = {'id': 12345, 'problem': {'contestId': 4, 'index': 'A', 'name': 'Test', 'tags': ['math'], 'rating': 800},
           'author': {'members': [{'handle': 'demo'}]}, 'creationTimeSeconds': int(time.time()),
           'verdict': status, 'programmingLanguage': 'GNU C++17', 'passedTestCount': 2, 'timeConsumedMillis': 10, 'memoryConsumedBytes': 1024}
    row.update(changes)
    monkeypatch.setattr('acmweb.workbench.Client.json', lambda *args, **kwargs: {'status': 'OK', 'result': [row]})
    return row


def test_statement_plaintext_samples_and_url_validation():
    parsed = parse_statement(HTML, 'codeforces:4-A')
    assert parsed['samples'] == [{'input': '2\n3', 'output': '5\n6'}]
    assert 'evil()' not in json.dumps(parsed) and '<script' not in json.dumps(parsed)
    assert parsed['has_images']
    assert normalize('https://codeforces.com/contest/4/problem/a') == 'codeforces:4-A'
    for value in ('https://evil.invalid/4/A', 'https://codeforces.com:443/problemset/problem/4/A', 'https://codeforces.com@127.0.0.1/problemset/problem/4/A', 'https://codeforces.com/problemset/problem/4/A?x=1', '../../account.toml', '100000-A'):
        with pytest.raises(AIError): normalize(value)
    with pytest.raises(AIError): parse_statement('<html>Cloudflare verification</html>', 'codeforces:4-A')


def test_old_host_execution_endpoint_is_removed(workspace):
    client, service, _ = workspace
    assert not hasattr(service, 'self_test')
    assert client.post('/api/workbench/problem/4-A/self-test', json={
        'language': 'cpp17', 'code': 'x', 'cases': [{'input': '', 'output': ''}],
    }).status_code in (404, 405)


def test_personal_translation_only_lives_in_process_memory(workspace):
    _, service, app = workspace
    translations = app.state.translations
    with service.db.connect() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='private_translations'").fetchone()
    assert translations.ephemeral == {}


def test_draft_conflict_snapshot_immutable_and_utf8_limit(workspace):
    client, service, _ = workspace
    assert client.get('/api/workbench/resolve', params={'problem':'https://codeforces.com/problemset/problem/4/A'}).json()['id'] == 'codeforces:4-A'
    aid = snapshot(service)
    response = client.put('/api/workbench/problem/4-A/draft', json={'code': 'other tab', 'language': 'cpp17', 'revision': 0})
    assert response.status_code == 409
    service.draft('4-A', Draft(code='new code', revision=1))
    assert service.attempt(aid)['code'] == 'int main() { return 0; }'
    with pytest.raises(HTTPException): service.snapshot('4-A', 1)
    with pytest.raises(AIError): service.draft('4-A', Draft(code='字'*30000, revision=2))
    assert client.post('/api/workbench/problem/4-A/snapshot', json={'code': 'not saved', 'revision': 2}).status_code == 409
    assert service.state('4-A')['draft']['code'] == 'new code'


def test_fetch_cache_survives_bad_response(workspace, monkeypatch):
    _, service, _ = workspace
    monkeypatch.setattr('acmweb.workbench.Client.get', lambda *args, **kwargs: HTML)
    assert service.statement('4-A')['problem_id'] == 'codeforces:4-A'
    monkeypatch.setattr('acmweb.workbench.Client.get', lambda *args, **kwargs: 'Access denied')
    with pytest.raises(AIError): service.statement('4-A')
    assert service.state('4-A')['statement']['title'] == 'A. Test'


def test_judge_validation_pending_and_rejudge(workspace, monkeypatch):
    _, service, _ = workspace
    aid = snapshot(service)
    mock_judge(monkeypatch, problem={'contestId': 5, 'index': 'A'})
    with pytest.raises(AIError): service.sync(aid, Link(handle='demo', submission_id=12345))
    assert service.attempt(aid)['verdict'] == 'UNLINKED'
    with pytest.raises(AIError): service.sync(aid, Link(handle='someone', submission_id=12345))
    mock_judge(monkeypatch, author={'members': [{'handle': 'someone'}]})
    with pytest.raises(AIError): service.sync(aid, Link(handle='demo', submission_id=12345))
    mock_judge(monkeypatch, creationTimeSeconds=1)
    with pytest.raises(AIError): service.sync(aid, Link(handle='demo', submission_id=12345))
    mock_judge(monkeypatch, status='TESTING')
    assert service.sync(aid, Link(handle='DEMO', submission_id=12345))['verdict'] == 'PENDING'
    with pytest.raises(AIError): service.sync(aid, Link(handle='demo', submission_id=12345))
    with service.db.connect() as db:
        db.execute('UPDATE wb_attempts SET last_synced=0 WHERE id=?', (aid,))
    mock_judge(monkeypatch)
    assert service.sync(aid, Link(handle='demo', submission_id=12345))['verdict'] == 'WA'
    with service.db.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0] == 0  # no crawl checkpoint movement
        db.execute('UPDATE wb_attempts SET last_synced=0,review=? WHERE id=?', (json.dumps({'category': 'unknown'}), aid))
    mock_judge(monkeypatch, status='OK')
    service.sync(aid, Link(handle='demo', submission_id=12345))
    assert service.attempt(aid)['review'] is None and not service.attempt(aid)['code_verified']
    assert training_evidence(service.db)['verified_verdicts'] == {'AC': 1}


def test_duplicate_submission_and_transport_failure(workspace, monkeypatch):
    _, service, _ = workspace
    aid = snapshot(service)
    other = service.snapshot('4-A', 1)['id']
    mock_judge(monkeypatch)
    service.sync(aid, Link(handle='demo', submission_id=12345))
    with pytest.raises(AIError, match='另一份'): service.sync(other, Link(handle='demo', submission_id=12345))
    def broken(*args, **kwargs): raise RuntimeError('private transport data')
    monkeypatch.setattr('acmweb.workbench.Client.json', broken)
    with pytest.raises(RuntimeError): service.sync(other, Link(handle='demo', submission_id=12345))
    assert service.attempt(other)['verdict'] == 'UNLINKED'


def test_handle_casing_change_allows_rejudge_but_not_duplicate_link(workspace, monkeypatch):
    _, service, _ = workspace
    aid = snapshot(service)
    other = service.snapshot('4-A', 1)['id']
    mock_judge(monkeypatch)
    service.sync(aid, Link(handle='demo', submission_id=12345))
    service.accounts.effective_path.write_text('[[accounts]]\nplatform="codeforces"\nhandle="DEMO"\n')
    with service.db.connect() as db:
        db.execute('UPDATE wb_attempts SET last_synced=0 WHERE id=?', (aid,))
    mock_judge(monkeypatch, status='OK')
    assert service.sync(aid, Link(handle='demo', submission_id=12345))['verdict'] == 'AC'
    with service.db.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0] == 1
    with pytest.raises(AIError, match='另一份'):
        service.sync(other, Link(handle='DEMO', submission_id=12345))
    assert service.attempt(other)['verdict'] == 'UNLINKED'


def test_crawl_rejudge_updates_workbench_review_and_training_evidence(workspace, monkeypatch):
    _, service, app = workspace
    aid = snapshot(service)
    row = mock_judge(monkeypatch)
    service.sync(aid, Link(handle='demo', submission_id=12345))
    original = service.attempt(aid)
    review = {'category': 'boundary'}
    with service.db.connect() as db:
        db.execute('UPDATE wb_attempts SET review=? WHERE id=?', (json.dumps(review), aid))
        db.execute('INSERT INTO problem_catalog VALUES(?,?,?)', ('codeforces', 'codeforces:4-A', json.dumps({'tags': ['math']})))
    result = Collection('codeforces', 'DEMO', submissions=[Submission('12345', '4-A', row['creationTimeSeconds'], 'WA', raw=row)])
    store = Store(service.db.path)
    try:
        # An unchanged daily replay keeps the AI review.
        store.save('unchanged', original['last_synced'], result)
        assert service.attempt(aid)['review'] == review
        # A verdict from a different problem/account must not touch this snapshot.
        result.account = 'someone'
        result.submissions[0].verdict = 'AC'
        store.save('other-account', original['last_synced'] + 1, result)
        assert service.attempt(aid)['verdict'] == 'WA'
        result.account = 'DEMO'
        result.submissions[0].problem_id = '4-B'
        store.save('other-problem', original['last_synced'] + 2, result)
        assert service.attempt(aid)['verdict'] == 'WA'
        # The account's daily collection observes a rejudge without explicit WB sync.
        result.submissions[0].problem_id = '4-A'
        result.submissions[0].raw = {**row, 'verdict': 'OK', 'passedTestCount': 20}
        store.save('rejudge', original['last_synced'] + 3, result)
    finally:
        store.close()
    updated = service.attempt(aid)
    assert updated['verdict'] == 'AC' and updated['review'] is None
    assert updated['judge_data']['passedTestCount'] == 20
    assert updated['code'] == original['code'] and updated['code_hash'] == original['code_hash']
    evidence = training_evidence(service.db)
    assert evidence['verified_verdicts'] == {'AC': 1} and evidence['ai_hypotheses'] == []
    assert evidence['topics']['math'] == {'accepted_snapshots': 1, 'non_accepted_snapshots': 0}
    with pytest.raises(AIError, match='仅对本题'):
        asyncio.run(service.coach('4-A', Coach(provider='api', model='mock'), aid))


def test_rejudge_same_verdict_invalidates_inflight_review(workspace, monkeypatch):
    _, service, app = workspace
    aid = snapshot(service)
    row = mock_judge(monkeypatch)
    service.sync(aid, Link(handle='demo', submission_id=12345))
    synced = service.attempt(aid)['last_synced']
    async def generate(*args):
        store = Store(service.db.path)
        try:
            changed = {**row, 'passedTestCount': 3}
            store.save('same-second-rejudge', synced, Collection('codeforces', 'demo', submissions=[
                Submission('12345', '4-A', row['creationTimeSeconds'], 'WA', raw=changed)]))
        finally:
            store.close()
        return json.dumps({'category': 'boundary', 'line': 1, 'evidence': '检查边界。', 'question': '最小输入如何？', 'confidence': 'low'})
    monkeypatch.setattr(app.state.ai, '_api_generate', generate)
    with pytest.raises(AIError, match='判题已改变'):
        asyncio.run(service.coach('4-A', Coach(provider='api', model='mock'), aid))
    assert service.attempt(aid)['review'] is None


@pytest.mark.parametrize('remote_status', ['TESTING', 'QUEUED', 'PRETESTS_PASSED', None])
def test_daily_crawl_unknown_or_running_judge_cannot_be_reviewed(workspace, monkeypatch, remote_status):
    _, service, _ = workspace
    aid = snapshot(service)
    row = mock_judge(monkeypatch)
    service.sync(aid, Link(handle='demo', submission_id=12345))
    with service.db.connect() as db:
        db.execute('UPDATE wb_attempts SET review=? WHERE id=?', (json.dumps({'category': 'boundary'}), aid))
        db.execute('INSERT INTO problem_catalog VALUES(?,?,?)', ('codeforces', 'codeforces:4-A', json.dumps({'tags': ['math']})))
    store = Store(service.db.path)
    try:
        store.save('judge-running', service.attempt(aid)['last_synced'] + 1, Collection('codeforces', 'DEMO', submissions=[
            Submission('12345', '4-A', row['creationTimeSeconds'], verdict(remote_status),
                       raw={**row, 'verdict': remote_status})]))
    finally:
        store.close()
    attempt = service.attempt(aid)
    assert attempt['verdict'] == 'PENDING' and attempt['review'] is None
    assert attempt['judge_data']['verdict'] == remote_status
    assert training_evidence(service.db)['topics'] == {}
    with pytest.raises(AIError, match='仅对本题'):
        asyncio.run(service.coach('4-A', Coach(provider='api', model='mock'), aid))


def test_choices_never_resurrect_attempted_done_or_skipped_ai_recommendations(workspace):
    _, service, _ = workspace
    service.db.set('training', {'minimum': 1000, 'maximum': 1600})
    ids = ['10-A', '10-B', '10-C', '10-D', '10-E']
    with service.db.connect() as db:
        for pid in ids:
            problem = {'id': 'codeforces:' + pid, 'platform': 'codeforces', 'problem_id': pid,
                       'title': pid, 'difficulty': 1800 if pid == '10-E' else 1200, 'tags': [], 'contest_id': 10}
            db.execute('INSERT INTO problem_catalog VALUES(?,?,?)', ('codeforces', problem['id'], json.dumps(problem)))
        db.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)', ('codeforces', 'demo', '1', '10-A', 1, 'WA', '{}', 1))
        db.executemany('INSERT INTO practice_feedback VALUES(?,?,?,?,?)', [
            ('codeforces', '10-B', 'done', '', 1), ('codeforces', '10-C', 'skip', '', 1)])
        db.execute('INSERT INTO ai_reports VALUES(?,?,?,?,?,?)', ('report', 1, 'recommend', 'api', 'mock',
                   json.dumps({'selected_problems': [{'id': 'codeforces:' + pid, 'reason': 'AI recommendation'} for pid in ids]})))
    choices = service.choices()
    assert {item['id'] for item in choices['items']} == {'codeforces:10-D', 'codeforces:10-E'}
    assert all(item['recommendation_source'] == '已保存的 AI 推荐' for item in choices['items'])


def test_hints_and_review_constraints_no_credentials(workspace, monkeypatch):
    _, service, app = workspace
    fake = AsyncMock(return_value=json.dumps({'focus': '范围', 'hint': '先看看输入上界。', 'question': '最小输入会怎样？'}))
    monkeypatch.setattr(app.state.ai, '_api_generate', fake)
    config = Coach(provider='api', model='mock')
    for _ in range(8): asyncio.run(service.coach('4-A', config))
    assert len(service.state('4-A')['hints']) == 8
    with pytest.raises(AIError, match='8 次'): asyncio.run(service.coach('4-A', config))
    prompt = fake.call_args.args[1]
    assert 'TOP-SECRET' not in prompt and 'session=' not in prompt
    aid = snapshot(service)
    with pytest.raises(AIError): asyncio.run(service.coach('4-A', config, aid))
    mock_judge(monkeypatch)
    service.sync(aid, Link(handle='demo', submission_id=12345))
    review = {'category': 'boundary', 'line': 4, 'evidence': '检查边界。', 'question': '最小输入如何？', 'confidence': 'low'}
    fake.return_value = json.dumps(review)
    with pytest.raises(AIError, match='行号越界'): asyncio.run(service.coach('4-A', config, aid))
    review['line'] = 1
    fake.return_value = json.dumps(review)
    asyncio.run(service.coach('4-A', config, aid))
    assert service.attempt(aid)['review']['line'] == 1
    assert 'int main' not in json.dumps(training_evidence(service.db))
    review['evidence'] = 'int main() { return 0; }'
    fake.return_value = json.dumps(review)
    with pytest.raises(AIError, match='包含代码'): asyncio.run(service.coach('4-A', config, aid))


def test_background_job_safe_result_and_optional_auto_review(workspace, monkeypatch):
    client, service, app = workspace
    aid = snapshot(service)
    mock_judge(monkeypatch)
    monkeypatch.setattr(app.state.ai, '_api_generate', AsyncMock(side_effect=AIError('mock AI unavailable')))
    result = client.post(f'/api/workbench/attempt/{aid}/sync', json={'handle': 'demo', 'submission_id': 12345, 'review': {'provider': 'api', 'model': 'mock'}})
    assert result.status_code == 200
    job_id = result.json()['id']
    for _ in range(100):
        job = next(j for j in client.get('/api/jobs').json()['items'] if j['id'] == job_id)
        if job['status'] not in {'queued', 'running'}: break
        time.sleep(.01)
    assert job['status'] == 'completed' and 'review_error' in job['result']
    assert 'int main' not in json.dumps(job) and service.attempt(aid)['verdict'] == 'WA'


def test_verified_accepted_workbench_submission_updates_k_checkin(workspace, monkeypatch):
    _, service, app = workspace
    app.state.daily.configure(DailySettings(daily_goal=1, duration_days=14))
    with service.db.connect() as db:
        day = app.state.daily.today().isoformat()
        db.execute('INSERT INTO daily_plans VALUES(?,?,?,?,?,?)', (day, 'ready', 1, day, day, int(time.time())))
        db.execute('INSERT INTO daily_plan_tasks(day,ordinal,problem_id,data,active,created_at) VALUES(?,?,?,?,1,?)',
                   (day, 0, 'codeforces:4-A', json.dumps({'id': 'codeforces:4-A'}), int(time.time())))
    aid = snapshot(service)
    mock_judge(monkeypatch, status='OK')
    result = asyncio.run(app.state.jobs.run_workbench('wb_sync', {
        'aid': aid, 'handle': 'demo', 'submission_id': 12345,
    }))
    assert result['verdict'] == 'AC' and 'checkin_error' not in result, result
    assert app.state.daily.state()['checkin']['kind'] == 'k'


def test_editor_preferences_defaults_validation_and_persistence(workspace):
    client, service, _ = workspace
    assert client.get('/api/workbench/editor-settings').json() == {'keymap':'standard','font_size':14,'line_wrapping':False}
    settings = {'keymap':'vim','font_size':18,'line_wrapping':True}
    assert client.put('/api/workbench/editor-settings',json=settings).json() == settings
    assert service.db.get('editor_settings') == settings
    for bad in ({**settings,'keymap':'emacs'}, {**settings,'font_size':25}, {**settings,'font_size':True}, {**settings,'line_wrapping':'false'}, {**settings,'source_code':'private'}):
        assert client.put('/api/workbench/editor-settings',json=bad).status_code == 422
    assert client.get('/api/workbench/editor-settings').json() == settings


def test_statement_fallback_agent_and_failure_state(workspace, monkeypatch):
    _, service, _ = workspace
    calls=[]
    def fetch(self,url,**kwargs):
        calls.append((url,kwargs))
        assert self.cookie is None  # stale credentials must not poison public access
        assert 'OJAccountCrawler' not in kwargs['headers']['User-Agent']
        if '/problemset/' in url: raise AIError('verification')
        return HTML
    monkeypatch.setattr('acmweb.workbench.Client.get',fetch)
    service.statement('4-A')
    assert len(calls)==2 and '/contest/4/problem/A' in calls[1][0]
    assert service.state('4-A')['fetch_status']['status']=='completed'
    monkeypatch.setattr('acmweb.workbench.Client.get',lambda *a,**k: '<html>verification</html>')
    with pytest.raises(AIError): service.statement('4-A')
    state=service.state('4-A')
    assert state['fetch_status']['status']=='failed' and state['statement']['title']=='A. Test'
    assert 'TOP-SECRET' not in json.dumps(state['fetch_status'])


def test_statement_import_is_plaintext_and_labeled(workspace):
    client,service,_=workspace
    response=client.post('/api/workbench/problem/4-A/import',json={'html':HTML})
    assert response.status_code==200
    statement=service.state('4-A')['statement']
    assert statement['source']=='user_import' and statement['samples'][0]['input']=='2\n3'
    assert 'evil()' not in json.dumps(statement)
    assert client.post('/api/workbench/problem/4-A/import',json={'html':'<script>evil()</script>'}).status_code==400
    assert service.state('4-A')['statement']==statement


def test_rich_statement_preserves_images_structure_and_math_safely():
    html='''<div class="problem-statement"><div class="header"><div class="title">Diagram</div></div><div><p>Let <span>$$$x^2$$$</span> grow.</p><img src="/predownloaded/a.png" onerror="evil()"/><img src="javascript:evil()"/><ul><li>one</li></ul></div></div>'''
    data=parse_statement(html,'codeforces:4-A')
    assert data['images']==[{'url':'https://codeforces.com/predownloaded/a.png','alt':''}]
    assert '<ul>' in data['body_html'] and '$$$x^2$$$' in data['body_html']
    assert 'onerror' not in data['body_html'] and 'javascript:' not in data['body_html']
    assert data['diagnostics']['warnings'] and data['render_version']==2

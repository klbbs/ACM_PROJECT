"""Synthetic transports through the real API; no official site receives code."""
import asyncio
import threading
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.remote_judges import SubmissionError
from acmweb.remote_submissions import RemoteSubmissions
from acmweb.settings import Settings

SOURCE = 'PRIVATE-SYNTHETIC-SOURCE-v1'
COOKIE = 'SYNTHETIC-COOKIE-DO-NOT-PUBLISH'


@pytest.fixture
def remote_site(tmp_path, monkeypatch):
    plan = {'sent': [], 'mode': 'success', 'judge_account': 'demo', 'verdict': 'WA'}

    class SyntheticDriver:
        before_send = None

        def options(self, pid, handle):
            return [{'id': '89', 'name': 'Synthetic GNU C++20'}]

        def submit(self, pid, handle, source, compiler):
            if self.before_send:
                self.before_send()
            plan['sent'].append((pid, handle, source, compiler))
            if plan['mode'] == 'uncertain':
                raise SubmissionError('uncertain', uncertain=True)
            if plan['mode'] == 'generic_error':
                raise RuntimeError(SOURCE + COOKIE)
            if plan.get('entered'):
                plan['entered'].set()
                assert plan['release'].wait(5), 'synthetic driver was not released'
            return {'id': '12345', 'url': 'https://codeforces.com/contest/4/submission/12345',
                    'compiler_name': 'Synthetic GNU C++20'}

        def judge(self, pid, handle, sid):
            return {'id': sid, 'problem_id': '4-A', 'account': plan['judge_account'],
                    'submitted_at': int(time.time()), 'verdict': plan['verdict'],
                    'language': 'Synthetic GNU C++20',
                    'url': 'https://codeforces.com/contest/4/submission/12345',
                    **plan.get('judge_changes', {})}

        def close(self):
            pass

    monkeypatch.setattr(RemoteSubmissions, '_driver', staticmethod(lambda *_: SyntheticDriver()))
    watch = RemoteSubmissions.watch

    async def single_judge(self, rid, driver, **kwargs):
        return await watch(self, rid, driver, rounds=1)

    monkeypatch.setattr(RemoteSubmissions, 'watch', single_judge)
    settings = Settings(data_dir=tmp_path, config_path=tmp_path/'absent.toml',
                        multi_user=True, enable_codex=False, allow_env_ai_key=False,
                        access_token='synthetic-remote-setup-token', allowed_hosts=('testserver',))
    app = create_app(settings)
    with TestClient(app, base_url='https://testserver') as client:
        def request(who, method, path, body=None, extra=None):
            headers = {'Cookie': '', 'X-ACM-Request': '1', 'Origin': 'https://testserver'}
            if who:
                headers.update({'Cookie': 'acm_user_session='+who['cookie'],
                                'X-CSRF-Token': who['csrf']})
            headers.update(extra or {})
            return client.request(method, path, headers=headers,
                                  **({'json': body} if body is not None else {}))

        def capture(response):
            assert response.status_code == 200, response.text
            cookie = response.cookies['acm_user_session']
            status = client.get('/api/auth/status', headers={'Cookie': 'acm_user_session='+cookie}).json()
            return {'cookie': cookie, 'csrf': status['csrf_token'], 'uid': status['user']['id']}

        owner = capture(request(None, 'POST', '/api/auth/setup', {
            'username': 'kendas', 'password': 'Synthetic-password-123',
            'setup_token': settings.access_token,
        }))
        invite = request(owner, 'POST', '/api/admin/invitations', {}).json()['invitation']
        member = capture(request(None, 'POST', '/api/auth/register', {
            'username': 'member', 'password': 'Synthetic-password-123', 'invitation': invite,
        }))
        assert request(owner, 'PUT', '/api/accounts', {'items': [
            {'platform': 'codeforces', 'handle': 'demo', 'cookie': 'session='+COOKIE},
        ]}).status_code == 200
        assert request(owner, 'PUT', '/api/workbench/problem/4-A/draft', {
            'revision': 0, 'language': 'cpp20', 'code': SOURCE,
        }).status_code == 200
        child = app.state.workspaces.apps[owner['uid']][0]
        yield request, owner, member, child, plan


def payload(key='a'*32):
    return {'problem_id': 'codeforces:4-A', 'handle': 'demo', 'revision': 1,
            'compiler_id': '89', 'request_key': key}


def wait_intent(request, owner, rid, status):
    for _ in range(150):
        response = request(owner, 'GET', '/api/remote-submissions/'+rid)
        assert response.status_code == 200, response.text
        row = response.json()
        if row['status'] == status:
            return row
        time.sleep(.01)
    raise AssertionError(f'intent did not reach {status}: {row}')


def assert_public(response):
    assert SOURCE not in response.text and COOKIE not in response.text
    if isinstance(response.json(), dict):
        assert 'code' not in response.json() and 'account_revision' not in response.json()


def test_real_dispatch_csrf_owner_isolation_and_request_key_idempotency(remote_site):
    request, owner, member, child, plan = remote_site
    assert request(None, 'GET', '/api/remote-submissions').status_code == 401
    assert request(owner, 'POST', '/api/remote-submissions', payload(),
                   {'X-CSRF-Token': ''}).status_code == 403
    assert request(owner, 'POST', '/api/remote-submissions', payload(),
                   {'Origin': 'https://evil.invalid'}).status_code == 403
    options = request(owner, 'POST', '/api/remote-submissions/options',
                      {'problem_id': '4-A', 'handle': 'demo'})
    assert options.status_code == 200 and options.json()['items'][0]['id'] == '89'
    queued = request(owner, 'POST', '/api/remote-submissions', payload())
    assert queued.status_code == 200
    rid = queued.json()['id']
    row = wait_intent(request, owner, rid, 'completed')
    assert row['verdict'] == 'WA' and row['attempt_id']
    replay = request(owner, 'POST', '/api/remote-submissions', payload())
    assert replay.status_code == 200 and replay.json()['id'] == rid
    assert len(plan['sent']) == 1
    assert request(owner, 'POST', '/api/remote-submissions',
                   {**payload(), 'compiler_id': '54'}).status_code == 409
    assert request(member, 'GET', '/api/remote-submissions/'+rid).status_code == 404
    assert request(member, 'POST', '/api/remote-submissions/'+rid+'/refresh', {}).status_code == 404
    assert request(member, 'POST', '/api/remote-submissions/'+rid+'/dismiss',
                   {'confirmed_not_submitted': True}).status_code == 404
    assert request(member, 'GET', '/api/remote-submissions').json()['items'] == []
    for response in (queued, replay, request(owner, 'GET', '/api/remote-submissions'),
                     request(owner, 'GET', '/api/jobs')):
        assert_public(response)


def test_duplicate_executor_cannot_send_a_source_post_twice(remote_site, monkeypatch):
    request, owner, _, child, plan = remote_site
    monkeypatch.setattr(child.state.jobs, 'start', lambda *_: {'id':'queued-only'})
    rid = request(owner,'POST','/api/remote-submissions',payload()).json()['id']
    plan['entered'], plan['release'] = threading.Event(), threading.Event()

    async def race():
        first = asyncio.create_task(child.state.remote_submissions.execute(rid))
        try:
            assert await asyncio.to_thread(plan['entered'].wait,3)
            duplicate = await child.state.remote_submissions.execute(rid)
            assert duplicate['status'] == 'submitting'
            assert len(plan['sent']) == 1
        finally:
            plan['release'].set()
        assert (await first)['status'] == 'completed'
    asyncio.run(race())
    assert len(plan['sent']) == 1


def test_saved_source_stays_immutable_and_ai_failure_preserves_official_verdict(remote_site):
    request, owner, _, child, plan = remote_site
    plan['entered'], plan['release'] = threading.Event(), threading.Event()
    child.state.workbench.automatic_review = AsyncMock(side_effect=RuntimeError(SOURCE+COOKIE))
    try:
        queued = request(owner, 'POST', '/api/remote-submissions', payload())
        assert queued.status_code == 200
        assert plan['entered'].wait(3)
        assert request(owner, 'PUT', '/api/workbench/problem/4-A/draft', {
            'revision': 1, 'language': 'cpp20', 'code': 'new editor version',
        }).status_code == 200
    finally:
        plan['release'].set()
    row = wait_intent(request, owner, queued.json()['id'], 'completed')
    assert plan['sent'][0][2] == SOURCE and row['verdict'] == 'WA'
    attempt = child.state.workbench.attempt(row['attempt_id'])
    assert attempt['code'] == SOURCE and attempt['verdict'] == 'WA'
    assert attempt['judge_data']['source'] == 'official_remote_submission'
    for response in (request(owner, 'GET', '/api/jobs'),
                     request(owner, 'GET', '/api/remote-submissions/'+row['id'])):
        assert_public(response)


@pytest.mark.parametrize('mode', ['uncertain', 'generic_error'])
def test_uncertain_send_never_retries_when_replaying_or_refreshing(remote_site, mode):
    request, owner, _, child, plan = remote_site
    plan['mode'] = mode
    queued = request(owner, 'POST', '/api/remote-submissions', payload())
    assert queued.status_code == 200
    rid = queued.json()['id']
    wait_intent(request, owner, rid, 'uncertain')
    assert request(owner, 'POST', '/api/remote-submissions', payload()).json()['id'] == rid
    assert request(owner, 'POST', '/api/remote-submissions', payload('b'*32)).status_code == 409
    response = request(owner, 'POST', '/api/remote-submissions/'+rid+'/refresh', {})
    assert response.status_code == 409
    assert_public(response)
    assert len(plan['sent']) == 1
    for response in (request(owner, 'GET', '/api/jobs'),
                     request(owner, 'GET', '/api/remote-submissions/'+rid)):
        assert_public(response)
    restarted = RemoteSubmissions(child.state.database, child.state.remote_submissions.accounts,
                                  child.state.workbench, child.state.jobs, child.state.daily)
    assert restarted.get(rid)['status'] == 'uncertain' and len(plan['sent']) == 1
    with child.state.database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM wb_attempts').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM submissions').fetchone()[0] == 0


@pytest.mark.parametrize('changes', [
    {'account': 'not_the_owner'}, {'problem_id': '4-B'}, {'id': '54321'},
    {'submitted_at': 1}, {'submitted_at': 0}, {'submitted_at': 10**12},
])
def test_unverified_official_evidence_never_creates_attempt_or_submission(remote_site, changes):
    request, owner, _, child, plan = remote_site
    plan['judge_changes'] = changes
    queued = request(owner, 'POST', '/api/remote-submissions', payload())
    rid = queued.json()['id']
    for _ in range(150):
        row = request(owner, 'GET', '/api/remote-submissions/'+rid).json()
        if row['reason'] == 'judge_unavailable':
            break
        time.sleep(.01)
    assert row['reason'] == 'judge_unavailable' and row['attempt_id'] is None
    with child.state.database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM wb_attempts').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM submissions').fetchone()[0] == 0
    assert len(plan['sent']) == 1


def test_credential_change_blocks_queued_source_at_send_boundary(remote_site, monkeypatch):
    request, owner, _, child, plan = remote_site
    monkeypatch.setattr(child.state.jobs, 'start', lambda *_: {'id': 'synthetic-queued-only'})
    queued = request(owner, 'POST', '/api/remote-submissions', payload())
    assert queued.status_code == 200 and queued.json()['status'] == 'queued'
    rid = queued.json()['id']
    assert request(owner, 'PUT', '/api/accounts', {'items': [
        {'platform': 'codeforces', 'handle': 'demo', 'cookie': 'session=changed-synthetic-cookie'},
    ]}).status_code == 200
    result = asyncio.run(child.state.remote_submissions.execute(rid))
    assert result['status'] == 'blocked' and result['reason'] == 'configuration_changed'
    replay = request(owner, 'POST', '/api/remote-submissions', payload())
    assert replay.json()['id'] == rid and replay.json()['status'] == 'blocked'
    assert plan['sent'] == []


@pytest.mark.parametrize('before,after', [('queued', 'failed'), ('submitting', 'uncertain')])
def test_restart_never_automatically_resends_source(remote_site, monkeypatch, before, after):
    request, owner, _, child, plan = remote_site
    monkeypatch.setattr(child.state.jobs, 'start', lambda *_: {'id': 'synthetic-queued-only'})
    queued = request(owner, 'POST', '/api/remote-submissions', payload())
    rid = queued.json()['id']
    if before == 'submitting':
        child.state.remote_submissions.update(rid, status='submitting', sent_at=int(time.time()))
    restarted = RemoteSubmissions(child.state.database, child.state.remote_submissions.accounts,
                                  child.state.workbench, child.state.jobs, child.state.daily)
    assert restarted.get(rid)['status'] == after
    assert asyncio.run(restarted.execute(rid))['status'] == after
    replay = request(owner, 'POST', '/api/remote-submissions', payload())
    assert replay.json()['id'] == rid and replay.json()['status'] == after
    assert plan['sent'] == []

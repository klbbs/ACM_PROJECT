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

        def find_receipt(self, pid, handle, sent_at, code_hash, api_credentials=None):
            plan['recovery_credentials'] = api_credentials
            if plan.get('recovery_error'):
                raise SubmissionError(plan['recovery_error'])
            if not plan.get('recover'):
                raise SubmissionError('record_unavailable')
            return {**self.judge(pid,handle,'12345'),'code':plan.get('recovery_code', SOURCE)}

    monkeypatch.setattr(RemoteSubmissions, '_driver', staticmethod(lambda *_: SyntheticDriver()))
    watch = RemoteSubmissions.watch
    plan['original_watch'] = watch

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
            {'platform': 'codeforces', 'handle': 'demo', 'cookie': 'session='+COOKIE,
             'api_key':'a'*32, 'api_secret':'b'*32},
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


def test_problem_sync_reconciles_known_receipt_without_reading_or_resending_code(remote_site, monkeypatch):
    from ojcrawler.models import Collection, Submission
    from test_remote_judges import cf_record
    request, owner, _, child, plan = remote_site
    plan['verdict'] = 'PENDING'
    rid = request(owner, 'POST', '/api/remote-submissions', payload()).json()['id']
    wait_intent(request, owner, rid, 'judging')
    # Let the fixture's one-round poll finish, then the independent synchronization
    # obtains a final official record for that exact already accepted SID.
    for _ in range(100):
        if not child.state.jobs.tasks:
            break
        time.sleep(.01)
    raw = cf_record(12345)
    item = Submission('12345', '4-A', raw['creationTimeSeconds'], 'WA', language='GNU C++20', raw=raw)
    monkeypatch.setattr(child.state.problem_submissions, '_collect',
        lambda *_: (Collection('codeforces', 'demo', submissions=[item], complete=True), 'synthetic official contest'))
    result = asyncio.run(child.state.problem_submissions.sync('codeforces:4-A', 'demo'))
    assert result['complete']
    completed = request(owner, 'GET', '/api/remote-submissions/' + rid).json()
    assert completed['status'] == 'completed' and completed['verdict'] == 'WA'
    attempt = child.state.workbench.attempt(completed['attempt_id'])
    assert attempt['code'] == SOURCE and attempt['code_verified']
    assert len(plan['sent']) == 1  # No second send or unrelated current draft.


def test_read_only_platform_budget_is_separate_from_ai_send_budget(remote_site):
    request, owner, _, _, _ = remote_site
    # Successful options requests used to exhaust the shared six-per-minute
    # cost allowance before reading code or sending once.
    for _ in range(7):
        assert request(owner, 'POST', '/api/remote-submissions/options',
            {'problem_id': '4-A', 'handle': 'demo'}).status_code == 200
    queued = request(owner, 'POST', '/api/remote-submissions', payload())
    assert queued.status_code == 200
    for _ in range(13):
        assert request(owner, 'POST', '/api/remote-submissions/options',
            {'problem_id': '4-A', 'handle': 'demo'}).status_code == 200
    limited = request(owner, 'POST', '/api/remote-submissions/options',
        {'problem_id': '4-A', 'handle': 'demo'})
    assert limited.status_code == 429 and '只读查询' in limited.json()['detail']
    assert int(limited.headers['Retry-After']) > 0


@pytest.mark.parametrize('late_error', [False, True])
def test_late_watch_cannot_undo_a_final_result_from_explicit_problem_sync(remote_site, late_error):
    from ojcrawler.models import Submission
    from test_remote_judges import cf_record
    request, owner, _, child, plan = remote_site
    plan['verdict'] = 'PENDING'
    rid = request(owner, 'POST', '/api/remote-submissions', payload()).json()['id']
    wait_intent(request, owner, rid, 'judging')
    for _ in range(100):
        if not child.state.jobs.tasks:
            break
        time.sleep(.01)
    entered, release = threading.Event(), threading.Event()
    class SlowJudge:
        def judge(self, *_):
            entered.set()
            assert release.wait(5)
            if late_error:
                raise SubmissionError('network')
            return {'id': '12345', 'account': 'demo', 'problem_id': '4-A',
                'submitted_at': int(time.time()), 'verdict': 'PENDING'}
    async def race():
        watcher = asyncio.create_task(child.state.remote_submissions.watch(rid, SlowJudge()))
        while not entered.is_set():
            await asyncio.sleep(.001)
        try:
            raw = cf_record(12345)
            await child.state.remote_submissions.reconcile_collected('codeforces:4-A', 'demo',
                [Submission('12345', '4-A', raw['creationTimeSeconds'], 'WA', raw=raw)])
        finally:
            release.set()
        await watcher
    asyncio.run(race())
    row = request(owner, 'GET', '/api/remote-submissions/' + rid).json()
    assert row['status'] == 'completed' and row['verdict'] == 'WA'
    assert child.state.workbench.attempt(row['attempt_id'])['verdict'] == 'WA'
    assert len(plan['sent']) == 1


def test_upstream_judge_limit_stops_polling_and_enforces_manual_cooldown(remote_site):
    request, owner, _, child, plan = remote_site
    plan['verdict'] = 'PENDING'
    rid = request(owner, 'POST', '/api/remote-submissions', payload()).json()['id']
    wait_intent(request, owner, rid, 'judging')
    for _ in range(100):
        if not child.state.jobs.tasks:
            break
        time.sleep(.01)
    class LimitedJudge:
        calls = 0
        def judge(self, *_):
            self.calls += 1
            raise SubmissionError('rate_limited')
    judge = LimitedJudge()
    asyncio.run(plan['original_watch'](child.state.remote_submissions, rid, judge, rounds=3))
    row = request(owner, 'GET', '/api/remote-submissions/' + rid).json()
    assert row['reason'] == 'rate_limited' and judge.calls == 1
    response = request(owner, 'POST', '/api/remote-submissions/' + rid + '/refresh', {})
    assert response.status_code == 429 and int(response.headers['Retry-After']) > 0
    assert len(plan['sent']) == 1


@pytest.mark.parametrize('evidence', ['unknown_sid', 'other_problem', 'other_author'])
def test_problem_sync_reconciliation_never_guesses_a_receipt(remote_site, evidence):
    """Only the exact already-sent sid for this account/problem may complete it."""
    from ojcrawler.models import Submission
    from test_remote_judges import cf_record
    request, owner, _, child, plan = remote_site
    plan['verdict'] = 'PENDING'
    rid = request(owner, 'POST', '/api/remote-submissions', payload()).json()['id']
    wait_intent(request, owner, rid, 'judging')
    for _ in range(150):
        if not child.state.jobs.tasks:
            break
        time.sleep(.01)
    before = request(owner, 'GET', '/api/remote-submissions/' + rid).json()
    assert before['status'] == 'judging' and before['verdict'] == 'PENDING'
    raw = {'unknown_sid': lambda: cf_record(98765),
           'other_problem': lambda: cf_record(12345, problem={'contestId': 5, 'index': 'A'}),
           'other_author': lambda: cf_record(12345, author={'members': [{'handle': 'stranger'}]}),
           }[evidence]()
    item = Submission(str(raw['id']), '4-A', raw['creationTimeSeconds'], 'WA', raw=raw)
    asyncio.run(child.state.remote_submissions.reconcile_collected('codeforces:4-A', 'demo', [item]))
    after = request(owner, 'GET', '/api/remote-submissions/' + rid).json()
    assert after['status'] == 'judging' and after['verdict'] == before['verdict']
    assert after['attempt_id'] == before['attempt_id']
    assert child.state.workbench.attempt(after['attempt_id'])['verdict'] == 'PENDING'
    assert len(plan['sent']) == 1


def test_late_problem_sync_does_not_replay_a_completed_receipt(remote_site, monkeypatch):
    from ojcrawler.models import Collection, Submission
    from test_remote_judges import cf_record
    request, owner, _, child, plan = remote_site
    completions = []
    monkeypatch.setattr(child.state.daily, 'complete', lambda payload: completions.append(payload.problem_id))
    plan['verdict'] = 'AC'
    rid = request(owner, 'POST', '/api/remote-submissions', payload()).json()['id']
    done = wait_intent(request, owner, rid, 'completed')
    assert done['verdict'] == 'AC' and completions == ['codeforces:4-A']
    raw = cf_record(12345)
    item = Submission('12345', '4-A', raw['creationTimeSeconds'], 'AC', language='GNU C++20', raw=raw)
    monkeypatch.setattr(child.state.problem_submissions, '_collect',
        lambda *_: (Collection('codeforces', 'demo', submissions=[item], complete=True), 'synthetic late sync'))
    result = asyncio.run(child.state.problem_submissions.sync('codeforces:4-A', 'demo'))
    assert result['count'] == 1
    row = request(owner, 'GET', '/api/remote-submissions/' + rid).json()
    assert row['status'] == 'completed' and row['verdict'] == 'AC' and row['attempt_id'] == done['attempt_id']
    assert child.state.workbench.attempt(row['attempt_id'])['code_verified']
    assert completions == ['codeforces:4-A']  # The finished receipt is not replayed.
    with child.state.database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM wb_attempts').fetchone()[0] == 1
    assert len(plan['sent']) == 1


def test_upstream_limit_cooldown_expires_without_resending_code(remote_site):
    request, owner, _, child, plan = remote_site
    plan['verdict'] = 'PENDING'
    rid = request(owner, 'POST', '/api/remote-submissions', payload()).json()['id']
    wait_intent(request, owner, rid, 'judging')
    for _ in range(100):
        if not child.state.jobs.tasks:
            break
        time.sleep(.01)
    class LimitedJudge:
        def judge(self, *_):
            raise SubmissionError('rate_limited')
    asyncio.run(plan['original_watch'](child.state.remote_submissions, rid, LimitedJudge(), rounds=3))
    assert request(owner, 'POST', '/api/remote-submissions/' + rid + '/refresh', {}).status_code == 429
    with child.state.database.connect() as conn:
        conn.execute('UPDATE wb_remote_submissions SET updated_at=? WHERE id=?', (int(time.time()) - 61, rid))
    plan['verdict'] = 'WA'
    assert request(owner, 'POST', '/api/remote-submissions/' + rid + '/refresh', {}).status_code == 200
    done = wait_intent(request, owner, rid, 'completed')
    assert done['verdict'] == 'WA'
    assert len(plan['sent']) == 1


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


def test_saved_source_stays_immutable_without_automatic_ai_review(remote_site):
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
    child.state.workbench.automatic_review.assert_not_awaited()
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
    assert response.status_code == 200
    wait_intent(request, owner, rid, 'uncertain')
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


def test_other_problem_unconfirmed_send_is_visible_and_never_resubmitted(remote_site):
    request, owner, member, child, plan = remote_site
    plan['mode'] = 'uncertain'
    created = request(owner, 'POST', '/api/remote-submissions', payload()).json()
    wait_intent(request, owner, created['id'], 'uncertain')
    visible = request(owner, 'GET', '/api/remote-submissions?problem_id=codeforces:5-A')
    assert visible.status_code == 200
    assert visible.json()['items'] == []
    assert [row['id'] for row in visible.json()['blocking']] == [created['id']]
    assert visible.json()['blocking'][0]['problem_id'] == 'codeforces:4-A'
    assert_public(visible)
    assert request(member, 'GET', '/api/remote-submissions?problem_id=codeforces:5-A').json()['blocking'] == []
    assert request(owner, 'GET', '/api/remote-submissions?problem_id=atcoder:abc100_a').json()['blocking'] == []
    rejected = request(owner, 'POST', '/api/remote-submissions',
                       {**payload('c'*32), 'problem_id': 'codeforces:5-A'})
    assert rejected.status_code == 409
    assert len(plan['sent']) == 1
    with child.state.database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM wb_remote_submissions').fetchone()[0] == 1


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


def test_revoked_session_blocks_queued_send_and_problem_records_are_private(remote_site, monkeypatch):
    request, owner, member, child, plan = remote_site
    captured = {}
    def queue_only(kind, data):
        captured.update(data)
        return {'id':'synthetic-queued-only'}
    monkeypatch.setattr(child.state.jobs,'start',queue_only)
    queued = request(owner,'POST','/api/remote-submissions',payload())
    assert queued.status_code == 200 and captured['owner']
    assert request(None,'GET','/api/problem-submissions/codeforces:4-A').status_code == 401
    assert request(member,'GET','/api/problem-submissions/codeforces:4-A').json()['items'] == []
    assert request(member,'POST','/api/problem-submissions/codeforces:4-A/source',
                   {'handle':'demo','submission_id':'12345'}).status_code == 400
    assert request(owner,'POST','/api/problem-submissions/codeforces:4-A/sync',
                   {'handle':'demo'},extra={'X-CSRF-Token':''}).status_code == 403
    assert request(owner,'POST','/api/auth/logout',{}).status_code == 200
    result = asyncio.run(child.state.remote_submissions.execute(queued.json()['id'],captured['owner']))
    assert result['status'] == 'blocked'
    assert plan['sent'] == []


def test_uncertain_send_recovers_from_matching_official_source_without_new_post(remote_site):
    request, owner, _, child, plan = remote_site
    plan['mode'] = 'uncertain'
    queued = request(owner,'POST','/api/remote-submissions',payload())
    rid = queued.json()['id']
    wait_intent(request,owner,rid,'uncertain')
    plan['recover'] = True
    response = request(owner,'POST','/api/remote-submissions/'+rid+'/refresh',{})
    assert response.status_code == 200
    done = wait_intent(request,owner,rid,'completed')
    assert done['submission_id'] == '12345' and done['verdict'] == 'WA'
    assert len(plan['sent']) == 1
    assert plan['recovery_credentials'] == {'api_key':'a'*32, 'api_secret':'b'*32}
    assert child.state.workbench.attempt(done['attempt_id'])['code_verified']


@pytest.mark.parametrize('reason', ['rate_limited', 'api_rejected', 'source_unavailable'])
def test_receipt_recovery_preserves_upstream_failure_without_resending(remote_site, reason):
    request, owner, _, child, plan = remote_site
    plan['mode'] = 'uncertain'
    rid = request(owner, 'POST', '/api/remote-submissions', payload()).json()['id']
    wait_intent(request, owner, rid, 'uncertain')
    plan['recovery_error'] = reason
    result = asyncio.run(child.state.remote_submissions.reread(rid))
    assert result['status'] == 'uncertain' and result['submission_id'] is None
    assert result['reason'] == reason and len(plan['sent']) == 1
    if reason == 'rate_limited':
        assert request(owner, 'POST', '/api/remote-submissions/'+rid+'/refresh', {}).status_code == 429


def test_receipt_recovery_without_api_gives_configuration_guidance(remote_site, monkeypatch):
    request, owner, _, child, plan = remote_site
    plan['mode'] = 'uncertain'
    rid = request(owner, 'POST', '/api/remote-submissions', payload()).json()['id']
    wait_intent(request, owner, rid, 'uncertain')
    original = child.state.remote_submissions.accounts.document
    def without_api():
        document = original()
        for account in document['accounts']:
            account.pop('api_key', None)
            account.pop('api_secret', None)
        return document
    monkeypatch.setattr(child.state.remote_submissions.accounts, 'document', without_api)
    result = asyncio.run(child.state.remote_submissions.reread(rid))
    assert result['status'] == 'uncertain' and result['submission_id'] is None
    assert result['reason'] == 'api_required' and 'API Key' in result['message']
    assert 'recovery_credentials' not in plan and len(plan['sent']) == 1


def test_manual_receipt_retry_clears_old_blocked_reason_only_after_guard_checks(remote_site, monkeypatch):
    request, owner, _, child, plan = remote_site
    plan['mode'] = 'uncertain'
    rid = request(owner, 'POST', '/api/remote-submissions', payload()).json()['id']
    wait_intent(request, owner, rid, 'uncertain')
    for _ in range(100):
        if not child.state.jobs.tasks:
            break
        time.sleep(.01)
    child.state.remote_submissions.update(rid, reason='record_mismatch', message='old blocked read')
    queued = []
    monkeypatch.setattr(child.state.jobs, 'start', lambda kind, data: queued.append((kind, data)))
    result = request(owner, 'POST', '/api/remote-submissions/'+rid+'/refresh', {})
    assert result.status_code == 200
    assert result.json()['reason'] == '' and result.json()['status'] == 'uncertain'
    assert result.json()['submission_id'] is None and '正在读取官方记录' in result.json()['message']
    assert queued == [('wb_remote_refresh', {'intent_id':rid})] and len(plan['sent']) == 1
    child.state.remote_submissions.update(rid, reason='rate_limited')
    limited = request(owner, 'POST', '/api/remote-submissions/'+rid+'/refresh', {})
    assert limited.status_code == 429
    assert child.state.remote_submissions.get(rid)['reason'] == 'rate_limited'
    assert len(queued) == 1 and len(plan['sent']) == 1


def test_receipt_recovery_rejects_wrong_hash_and_retains_unknown_state(remote_site):
    request, owner, _, child, plan = remote_site
    plan.update(mode='uncertain', recover=True, recovery_code='different source')
    rid = request(owner, 'POST', '/api/remote-submissions', payload()).json()['id']
    wait_intent(request, owner, rid, 'uncertain')
    result = asyncio.run(child.state.remote_submissions.reread(rid))
    assert result['status'] == 'uncertain' and result['submission_id'] is None
    assert result['attempt_id'] is None and result['reason'] == 'record_mismatch'
    assert len(plan['sent']) == 1


@pytest.mark.parametrize('previous_language', ['cpp17', 'cpp20'])
def test_cf_cpp23_receipt_reuses_verified_source_despite_cpp17_draft_mode(remote_site, previous_language):
    """The live incident: CF compiler 91, cpp17 intent, imported cpp20 source."""
    request, owner, _, child, plan = remote_site
    plan.update(mode='uncertain', recover=True, judge_changes={'language':'GNU G++23 14.2'})
    assert request(owner, 'PUT', '/api/workbench/problem/4-A/draft', {
        'revision':1, 'language':'cpp17', 'code':SOURCE,
    }).status_code == 200
    rid = request(owner, 'POST', '/api/remote-submissions', {
        **payload(), 'revision':2, 'compiler_id':'91',
    }).json()['id']
    wait_intent(request, owner, rid, 'uncertain')
    remote = child.state.remote_submissions
    original = remote._row(rid)
    plan['judge_changes']['submitted_at'] = original['sent_at']
    observed = {'id':'12345', 'problem_id':'4-A', 'account':'demo', 'submitted_at':original['sent_at'],
                'language':'GNU G++23 14.2', 'verdict':'WA',
                'url':'https://codeforces.com/contest/4/submission/12345'}
    # This is the same normalization path used when "获取本次代码" imported
    # the source before the ambiguous remote receipt was recovered.
    imported = remote.record({**original, 'submission_id':'12345', 'language':'cpp20'}, observed)
    aid = imported['attempt_id']
    with child.state.database.connect() as conn:
        conn.execute('UPDATE wb_attempts SET language=?,review=? WHERE id=?',
                     (previous_language, '{"category":"old"}', aid))
        conn.execute('UPDATE wb_submission_source_bindings SET language=? WHERE attempt_id=?', (previous_language, aid))
        conn.execute('INSERT INTO wb_submission_reviews VALUES(?,?,?,?)', (aid,'old-fingerprint','{}',int(time.time())))
        conn.execute('INSERT INTO wb_submission_reviews VALUES(?,?,?,?)', ('unrelated','other-fingerprint','{}',int(time.time())))
    result = asyncio.run(remote.reread(rid))
    assert result['status'] == 'completed' and result['submission_id'] == '12345'
    assert result['attempt_id'] == aid and len(plan['sent']) == 1
    current = remote._row(rid)
    assert current['language'] == original['language'] == 'cpp17'
    assert current['compiler_id'] == '91' and current['code_hash'] == original['code_hash']
    assert current['code'] == original['code'] == SOURCE
    attempt = child.state.workbench.attempt(aid)
    assert attempt['language'] == 'cpp20' and attempt['code_verified']
    assert attempt['code'] == SOURCE and attempt['code_hash'] == original['code_hash']
    with child.state.database.connect() as conn:
        binding = conn.execute('SELECT * FROM wb_submission_source_bindings WHERE attempt_id=?', (aid,)).fetchone()
        assert binding['language'] == 'cpp20' and binding['submission_id'] == 12345
        assert binding['code_hash'] == original['code_hash']
        if previous_language == 'cpp17':
            assert attempt['review'] is None
            assert conn.execute('SELECT 1 FROM wb_submission_reviews WHERE attempt_id=?', (aid,)).fetchone() is None
        else:
            assert attempt['review'] == {'category':'old'}
            assert conn.execute('SELECT fingerprint FROM wb_submission_reviews WHERE attempt_id=?', (aid,)).fetchone()[0] == 'old-fingerprint'
        assert conn.execute('SELECT fingerprint FROM wb_submission_reviews WHERE attempt_id=?', ('unrelated',)).fetchone()[0] == 'other-fingerprint'


@pytest.mark.parametrize('conflict', ['other_source', 'corrupt_source', 'other_family', 'unknown_official_language'])
def test_receipt_language_normalization_never_overrides_conflicting_evidence(remote_site, conflict):
    import hashlib
    request, owner, _, child, plan = remote_site
    plan.update(mode='uncertain', recover=True)
    if conflict == 'unknown_official_language':
        plan['judge_changes'] = {'language':'Rust 1.82'}
    rid = request(owner, 'POST', '/api/remote-submissions', payload()).json()['id']
    wait_intent(request, owner, rid, 'uncertain')
    intent = child.state.remote_submissions._row(rid)
    if conflict != 'unknown_official_language':
        code = 'other source' if conflict in {'other_source','corrupt_source'} else SOURCE
        digest = (hashlib.sha256(code.encode()).hexdigest() if conflict == 'other_source' else intent['code_hash'])
        language = 'python3' if conflict == 'other_family' else 'cpp17'
        with child.state.database.connect() as conn:
            conn.execute('INSERT INTO wb_attempts(id,problem_id,language,code,code_hash,created_at,handle,submission_id) VALUES(?,?,?,?,?,?,?,?)',
                         ('conflicting',intent['problem_id'],language,code,digest,intent['created_at'],'demo',12345))
    result = asyncio.run(child.state.remote_submissions.reread(rid))
    assert result['status'] == 'uncertain' and result['submission_id'] is None
    assert result['reason'] == 'record_mismatch' and result['attempt_id'] is None
    assert len(plan['sent']) == 1
    with child.state.database.connect() as conn:
        if conflict == 'unknown_official_language':
            assert conn.execute('SELECT COUNT(*) FROM wb_attempts').fetchone()[0] == 0
        else:
            saved = conn.execute('SELECT * FROM wb_attempts WHERE id=?', ('conflicting',)).fetchone()
            assert saved['language'] == language and saved['code'] == code and saved['code_hash'] == digest
            assert conn.execute('SELECT COUNT(*) FROM wb_submission_source_bindings').fetchone()[0] == 0


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

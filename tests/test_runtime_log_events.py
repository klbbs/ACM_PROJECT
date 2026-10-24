"""Event producers are exercised locally with synthetic jobs and QR responses."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import requests
from fastapi import HTTPException

from acmweb.accounts import Accounts
from acmweb.codex_bridge import AIError
from acmweb.credential_check import CredentialChecks
from acmweb.database import Database
from acmweb.jobs import Jobs
from acmweb.nowcoder_qr import NowcoderQR
from acmweb.settings import Settings


@pytest.fixture
def events(monkeypatch):
    captured = []
    store = object()

    def capture(target, event, *, level='INFO', **details):
        captured.append({'store': target, 'event': event, 'level': level, 'details': details})

    for module in ('jobs', 'nowcoder_qr', 'startup_diagnostics'):
        monkeypatch.setattr(f'acmweb.{module}.emit_event', capture)
    return store, captured


def test_job_lifecycle_logs_queue_and_final_state_without_payload_or_result(tmp_path, events):
    store, captured = events
    jobs = Jobs(Database(tmp_path / 'jobs.sqlite3'), SimpleNamespace(), AsyncMock())
    jobs.event_log = store

    async def workbench(kind, payload):
        assert payload['code'] == 'PRIVATE-SOURCE'
        return {'code': 'PRIVATE-SOURCE', 'api_key': 'PRIVATE-KEY'}

    jobs.run_workbench = workbench

    async def run():
        queued = jobs.start('wb_submission_review', {'code': 'PRIVATE-SOURCE', 'cookie': 'PRIVATE-COOKIE'})
        await jobs.tasks[queued['id']][1]
        jobs.update(queued['id'], 'completed', {'code': 'PRIVATE-REPEATED-RESULT'}, 'PRIVATE-ERROR')
        return queued['id']

    job_id = asyncio.run(run())
    assert [item['event'] for item in captured] == ['job.queued', 'job.completed']
    for item, status in zip(captured, ('queued', 'completed')):
        assert item['store'] is store and item['level'] == 'INFO'
        assert item['details'] == {'job_id': job_id, 'kind': 'wb_submission_review', 'job_status': status}
    assert 'PRIVATE' not in str(captured)


@pytest.mark.parametrize('status,level', [
    ('completed', 'INFO'), ('partial', 'WARNING'), ('failed', 'ERROR'), ('interrupted', 'WARNING'),
])
def test_job_progress_and_repeated_final_updates_do_not_repeat_events(tmp_path, events, status, level):
    store, captured = events
    jobs = Jobs(Database(tmp_path / 'jobs.sqlite3'), SimpleNamespace(), AsyncMock())
    jobs.event_log = store
    job_id = 'a' * 32
    now = int(time.time())
    with jobs.db.connect() as db:
        db.execute('INSERT INTO web_jobs VALUES(?,?,?,?,?,?,?)',
                   (job_id, 'catalog', 'queued', now, now, None, None))
    jobs.update(job_id, 'running', {'progress': 'PRIVATE-PROGRESS'})
    jobs.update(job_id, 'running', {'progress': 'PRIVATE-MORE-PROGRESS'})
    jobs.update(job_id, status, {'code': 'PRIVATE-RESULT'}, 'PRIVATE-ERROR')
    jobs.update(job_id, status, {'code': 'PRIVATE-REPEAT'}, 'PRIVATE-ERROR')
    jobs.update('b' * 32, status, {'code': 'PRIVATE-NONEXISTENT'})
    assert captured == [{'store': store, 'event': 'job.' + status, 'level': level,
                         'details': {'job_id': job_id, 'kind': 'catalog', 'job_status': status}}]
    assert 'PRIVATE' not in str(captured)


def test_failed_job_records_safe_transition_without_exception_text(tmp_path, events):
    store, captured = events
    jobs = Jobs(Database(tmp_path / 'jobs.sqlite3'), SimpleNamespace(), AsyncMock())
    jobs.event_log = store

    async def failure(kind, payload):
        raise AIError('PRIVATE-COOKIE PRIVATE-KEY PRIVATE-SOURCE')

    jobs.run_workbench = failure

    async def run():
        queued = jobs.start('wb_submission_review')
        await jobs.tasks[queued['id']][1]

    asyncio.run(run())
    assert [item['event'] for item in captured] == ['job.queued', 'job.failed']
    assert captured[-1]['level'] == 'ERROR'
    assert 'PRIVATE' not in str(captured)


def test_failed_ai_job_records_only_the_known_diagnostic_code(tmp_path, events):
    from acmweb.ai import AI_NETWORK_ERROR
    store, captured = events
    jobs = Jobs(Database(tmp_path / 'jobs.sqlite3'), SimpleNamespace(), AsyncMock())
    jobs.event_log = store

    async def failure():
        raise AIError(AI_NETWORK_ERROR)
    jobs.ai.test_connection = failure

    async def run():
        queued = jobs.start('ai_test')
        await jobs.tasks[queued['id']][1]
    asyncio.run(run())
    assert captured[-1]['event'] == 'job.failed'
    assert captured[-1]['details']['reason'] == 'ai_network'
    assert set(captured[-1]['details']) == {'job_id', 'kind', 'job_status', 'reason'}


@pytest.fixture
def qr(tmp_path, monkeypatch, events):
    store, _ = events
    import acmweb.nowcoder_qr as module

    class Session:
        def close(self): pass

    accounts = Accounts(Settings(data_dir=tmp_path, config_path=tmp_path / 'missing.toml'))
    jobs = SimpleNamespace(tasks={}, event_log=store)
    manager = NowcoderQR(accounts, jobs, CredentialChecks(accounts), session_factory=Session)
    monkeypatch.setattr(module, '_create', lambda session: (
        'PRIVATE-TICKET', 'data:image/jpeg;base64,/9j/', 120))
    yield manager
    manager.close()


def test_qr_retry_events_are_deduplicated_and_never_include_exception_or_ticket(qr, monkeypatch, events):
    store, captured = events

    def timeout(session, ticket):
        raise requests.Timeout('PRIVATE-TICKET PRIVATE-COOKIE PRIVATE-URL')

    monkeypatch.setattr('acmweb.nowcoder_qr._poll', timeout)

    async def run():
        started = await qr.start('PRIVATE-OWNER', True)
        for _ in range(2):
            qr.flow['last_poll'] = 0
            assert (await qr.poll('PRIVATE-OWNER', started['login_id']))['retryable'] is True
        assert qr.flow is not None

    asyncio.run(run())
    assert captured == [{'store': store, 'event': 'platform.qr.retry', 'level': 'WARNING',
                         'details': {'platform': 'nowcoder', 'reason': 'upstream_timeout',
                                     'upstream_status': None, 'upstream_code': None}}]
    assert 'PRIVATE' not in str(captured)


def test_qr_verified_event_does_not_include_uid_or_credentials(qr, monkeypatch, events):
    store, captured = events
    monkeypatch.setattr('acmweb.nowcoder_qr._poll', lambda *args: ('verified', '123', 'sid=PRIVATE-COOKIE'))

    async def run():
        started = await qr.start('PRIVATE-OWNER', True)
        assert (await qr.poll('PRIVATE-OWNER', started['login_id']))['status'] == 'verified'

    asyncio.run(run())
    assert captured == [{'store': store, 'event': 'platform.qr.verified', 'level': 'INFO',
                         'details': {'platform': 'nowcoder', 'reason': 'identity_verified'}}]
    assert 'PRIVATE' not in str(captured) and '123' not in str(captured)


@pytest.mark.parametrize('failure,reason', [
    ('incomplete', 'account_incomplete'), ('mismatch', 'account_mismatch'),
    ('creation', 'qr_creation_failed'), ('binding', 'binding_failed'), ('crawl', 'crawl_in_progress'),
])
def test_qr_terminal_failures_record_only_fixed_reasons(qr, monkeypatch, events, failure, reason):
    store, captured = events
    monkeypatch.setattr('acmweb.nowcoder_qr._poll', lambda *args: ('verified', '123', 'sid=PRIVATE-COOKIE'))

    def creation_error(*args):
        raise RuntimeError('PRIVATE-TICKET PRIVATE-COOKIE')

    def binding_error(*args, **kwargs):
        raise AIError('PRIVATE-COOKIE PRIVATE-REVISION')

    if failure == 'creation':
        monkeypatch.setattr('acmweb.nowcoder_qr._create', creation_error)
    elif failure == 'binding':
        monkeypatch.setattr(qr.accounts, 'bind_verified_account', binding_error)
    elif failure == 'incomplete':
        monkeypatch.setattr('acmweb.nowcoder_qr._poll', lambda *args: ('incomplete', None, None))
    elif failure == 'crawl':
        qr.jobs.tasks = {'fake': ('crawl', SimpleNamespace(done=lambda: False))}

    async def run():
        try:
            started = await qr.start('PRIVATE-OWNER', True, '456' if failure == 'mismatch' else None)
            result = await qr.poll('PRIVATE-OWNER', started['login_id'])
            assert result['status'] == 'failed'
        except HTTPException as error:
            assert failure in {'creation', 'binding', 'crawl'}
            assert error.status_code in {409, 502}
        assert qr.flow is None
        assert not qr.accounts.path.exists()

    asyncio.run(run())
    assert len(captured) == 1
    assert captured[0]['store'] is store and captured[0]['event'] == 'platform.qr.failed'
    assert captured[0]['level'] == 'WARNING'
    assert captured[0]['details']['platform'] == 'nowcoder'
    assert captured[0]['details']['reason'] == reason
    assert set(captured[0]['details']) <= {'platform', 'reason', 'upstream_code'}
    assert 'PRIVATE' not in str(captured)


@pytest.mark.parametrize('confirmed', [False, True])
def test_qr_expiry_event_is_emitted_once_without_flow_identifiers(qr, events, confirmed):
    store, captured = events

    async def run():
        started = await qr.start('PRIVATE-OWNER', True)
        qr.flow['confirmed'] = confirmed
        qr._expire('PRIVATE-OWNER', started['login_id'])
        for _ in range(2):
            assert (await qr.poll('PRIVATE-OWNER', started['login_id']))['status'] == 'expired'

    asyncio.run(run())
    assert captured == [{'store': store, 'event': 'platform.qr.expired', 'level': 'WARNING',
                         'details': {'platform': 'nowcoder',
                                     'reason': 'identity_verification_timeout' if confirmed else 'qr_expired'}}]
    assert 'PRIVATE' not in str(captured)


@pytest.mark.parametrize('mode', ['single', 'multi'])
def test_startup_events_persist_counts_and_mode_only(monkeypatch, events, mode):
    from acmweb import startup_diagnostics
    store, captured = events
    findings = [{'level': 'warning', 'feature': 'configuration', 'detail': 'Synthetic private setting'},
                {'level': 'info', 'feature': 'frontend', 'detail': 'Synthetic local path'}]
    if mode == 'single':
        monkeypatch.setattr(startup_diagnostics, '_single_findings', lambda *args: findings)
        app = SimpleNamespace(state=SimpleNamespace(settings=None, account_config=None, ai=None,
                              selftest=None, codex=None, event_log=store))
        assert startup_diagnostics.report_single(app) == findings
    else:
        monkeypatch.setattr(startup_diagnostics, '_multiuser_findings', lambda *args: findings)
        assert startup_diagnostics.report_multiuser(None, None, None, event_log=store) == findings
    assert captured == [{'store': store, 'event': 'startup.checked', 'level': 'INFO',
                         'details': {'mode': mode, 'warning_count': 1, 'finding_count': 2}}]

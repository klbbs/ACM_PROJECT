"""Developer regressions for private read-before-selection reports.

All fixtures are synthetic local statements and AI outputs. They do not prove
real platform retrieval, a real model response, submission, or code execution.
"""
import asyncio
import copy
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from acmweb.codex_bridge import AIError
from acmweb.database import Database
from acmweb.prompts import build_prompt
from acmweb.selection_reports import SelectionReports

PID = 'codeforces:4-A'
SECOND = 'luogu:P1001'


def statement(pid=PID, **overrides):
    return {'id': pid, 'source': 'official', 'render_version': 2, 'title': 'Synthetic statement',
            'sections': ['Input n, 1 <= n <= 100. Print the required result.'],
            'body_html': '<p>Input n, 1 &lt;= n &lt;= 100.</p>',
            'samples': [{'input': '8', 'output': 'YES'}], 'images': [], **overrides}


def report(pid=PID, **overrides):
    return {'problem_id': pid, 'suitable': True, 'selection_fit': '符合所选基础练习目标。',
            'problem_summary': '读取给定整数并判断题面中的条件。', 'constraints': ['输入范围为 1 <= n <= 100。'],
            'approach': ['先核对题面要求和条件，再讨论与训练目标有关的判断。'],
            'implementation_notes': ['核对输入格式和整数边界。'], 'complexity': '若只作固定次数判断，时间 O(1)、额外空间 O(1)。',
            'limitations': ['这是基于已提供题面文字的 AI 判断，不是官方题解；未运行或提交代码。'], **overrides}


def save(db, material):
    with db.connect() as conn:
        conn.execute('INSERT INTO wb_problems VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
            (material['id'], json.dumps(material), int(time.time())))


def count(db):
    with db.connect() as conn:
        return conn.execute('SELECT count(*) FROM ai_selection_reports').fetchone()[0]


@pytest.fixture
def setup(tmp_path):
    db = Database(tmp_path / 'user.sqlite3')
    fetches = []
    def fetch(pid):
        fetches.append(pid)
        save(db, statement(pid))
        return {'problem_id': pid}
    async def complete(task, provider, model, payload, schema):
        return SimpleNamespace(data=report(payload['problem_id']), provenance={
            'provider': provider, 'model': model, 'task': task, 'endpoint': 'PRIVATE-ENDPOINT', 'api_key': 'PRIVATE-KEY'})
    ai = SimpleNamespace(complete=AsyncMock(side_effect=complete))
    wb = SimpleNamespace(statement=fetch)
    return SelectionReports(db, ai, wb), db, ai, wb, fetches


def prepare(service, items=None):
    return asyncio.run(service.prepare(items or [{'id': PID, 'reason': '基础练习', 'stage': 'template'}], 'api', 'synthetic-model'))


def test_reads_full_official_statement_then_saves_only_private_report_references(setup):
    service, db, ai, _, fetches = setup
    original = [{'id': PID, 'reason': '基础练习', 'stage': 'template'}]
    selected = prepare(service, original)
    assert original == [{'id': PID, 'reason': '基础练习', 'stage': 'template'}]
    assert set(selected[0]) == set(original[0]) | {'ai_report_id'}
    assert fetches == [PID]
    task, provider, model, payload, schema = ai.complete.call_args.args
    assert task == 'selection_read_report' and provider == 'api' and model == 'synthetic-model'
    assert payload['problem_id'] == PID
    assert payload['statement']['sections'] == statement()['sections']
    assert payload['statement']['samples'] == [{'input': '8', 'output': 'YES'}]
    assert payload['selection_context']['reason'] == '基础练习'
    saved = service.get(selected[0]['ai_report_id'])
    assert saved['problem_id'] == PID and saved['statement_hash'] == payload['statement_hash']
    assert saved['result']['suitable'] and saved['model'] == 'synthetic-model'
    assert 'PRIVATE-ENDPOINT' not in json.dumps(saved) and 'PRIVATE-KEY' not in json.dumps(saved)
    assert count(db) == 1 and ai.complete.await_count == 1
    service.get(selected[0]['ai_report_id'])
    assert ai.complete.await_count == 1  # GET only reads persisted reports.
    assert not service.active_ai


def test_reuses_only_complete_official_cache_and_sends_no_draft_fields(setup):
    service, db, ai, _, fetches = setup
    save(db, statement(draft='PRIVATE-DRAFT', cookie='PRIVATE-COOKIE'))
    prepare(service)
    assert fetches == []
    material = json.dumps(ai.complete.call_args.args[3])
    assert 'PRIVATE-DRAFT' not in material and 'PRIVATE-COOKIE' not in material


@pytest.mark.parametrize('bad', [
    {'source': 'user_import'}, {'sections': [], 'body_html': '<div></div>'},
    {'render_version': 1}, {'samples': [{'input': '8'}]}, {'samples': None},
])
def test_incomplete_imported_or_old_cache_cannot_fake_a_read(setup, bad):
    service, db, ai, wb, _ = setup
    save(db, statement(**bad))
    def blocked(pid):
        raise RuntimeError('Cookie=PRIVATE-SECRET')
    wb.statement = blocked
    with pytest.raises(AIError, match='官方完整题面获取失败') as raised:
        prepare(service)
    assert 'PRIVATE-SECRET' not in str(raised.value)
    assert ai.complete.await_count == 0 and count(db) == 0


def test_missing_statement_acknowledgement_is_not_statement_content(setup):
    service, db, ai, wb, _ = setup
    wb.statement = lambda pid: {'problem_id': pid}
    with pytest.raises(AIError, match='尚未取得官方完整题面'):
        prepare(service)
    assert ai.complete.await_count == 0 and count(db) == 0


@pytest.mark.parametrize('invalid', [
    {'problem_id': SECOND}, {'suitable': False}, {'problem_summary': ''},
    {'implementation_notes': ['int main(){ return 0; }']},
])
def test_wrong_problem_rejection_invalid_schema_or_complete_program_do_not_save(setup, invalid):
    service, db, ai, _, _ = setup
    ai.complete.side_effect = None
    ai.complete.return_value = SimpleNamespace(data=report(**invalid), provenance={})
    with pytest.raises(AIError):
        prepare(service)
    assert count(db) == 0 and not service.active_ai


def test_later_problem_failure_rolls_back_all_new_reports_and_preserves_inputs(setup):
    service, db, ai, _, _ = setup
    existing = prepare(service)[0]['ai_report_id']
    ai.complete.side_effect = [SimpleNamespace(data=report(), provenance={}), AIError('模型暂时不可用')]
    original = [{'id': PID}, {'problem_id': SECOND, 'learning_level': 2}]
    before = copy.deepcopy(original)
    with pytest.raises(AIError, match='模型暂时不可用'):
        prepare(service, original)
    assert original == before and count(db) == 1
    assert service.get(existing)['problem_id'] == PID
    assert not service.active_ai


def test_statement_change_during_ai_request_prevents_stale_batch_persistence(setup):
    service, db, ai, _, _ = setup
    async def changed(*args):
        save(db, statement(sections=['New official range n <= 200.']))
        return SimpleNamespace(data=report(), provenance={})
    ai.complete.side_effect = changed
    with pytest.raises(AIError, match='题面已变化'):
        prepare(service)
    assert count(db) == 0


def test_normalizes_problem_urls_and_rejects_duplicate_aliases_before_any_fetch(setup):
    service, db, ai, _, fetches = setup
    with pytest.raises(AIError, match='重复'):
        prepare(service, [{'id': PID}, {'id': 'https://codeforces.com/problemset/problem/4/A'}])
    assert fetches == [] and ai.complete.await_count == 0
    prepared = prepare(service, [{'problem_id': 'P1001', 'platform': 'luogu'}])
    assert service.get(prepared[0]['ai_report_id'])['problem_id'] == SECOND


def test_report_ids_are_isolated_by_user_database(setup, tmp_path):
    service, _, _, _, _ = setup
    saved_id = prepare(service)[0]['ai_report_id']
    other_db = Database(tmp_path / 'other-user.sqlite3')
    other_ai = SimpleNamespace(complete=AsyncMock())
    other = SelectionReports(other_db, other_ai, SimpleNamespace())
    with pytest.raises(HTTPException) as denied:
        other.get(saved_id)
    assert denied.value.status_code == 404
    assert other_ai.complete.await_count == 0


def test_timeout_does_not_persist_partial_reports_or_leave_busy_flag(setup, monkeypatch):
    service, db, ai, _, _ = setup
    monkeypatch.setattr('acmweb.selection_reports.MAX_PREPARE_SECONDS', 0.01)
    async def slow(*args):
        await asyncio.sleep(1)
    ai.complete.side_effect = slow
    with pytest.raises(AIError, match='安全时限'):
        prepare(service)
    assert count(db) == 0 and not service.active_ai


def test_concurrent_prepare_is_rejected_and_batch_calls_remain_sequential(setup):
    service, db, ai, _, _ = setup
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        active = 0
        async def pending(task, provider, model, payload, schema):
            nonlocal active
            active += 1
            assert active == 1
            entered.set()
            await release.wait()
            active -= 1
            return SimpleNamespace(data=report(payload['problem_id']), provenance={})
        ai.complete.side_effect = pending
        first = asyncio.create_task(service.prepare([{'id': PID}, {'problem_id': SECOND}], 'api', 'model'))
        await entered.wait()
        with pytest.raises(AIError, match='正在逐题阅读'):
            await service.prepare([{'id': PID}], 'api', 'model')
        release.set()
        return await first
    assert len(asyncio.run(scenario())) == 2 and count(db) == 2


def test_selection_prompts_distinguish_metadata_shortlist_from_actual_reading():
    for task in ('specialist_selection', 'daily_selection', 'recommend', 'daily_roll'):
        text = build_prompt(task, {'candidates': [{'id': PID, 'title': 'Metadata only'}]})
        assert '元信息初选' in text and '不能据此宣称已经读过' in text
    reading = build_prompt('selection_read_report', {'problem_id': PID, 'statement': statement()})
    assert 'suitable=false' in reading and '完整程序' in reading and '没有运行或提交代码' in reading


@pytest.mark.parametrize('invalid_items', [
    [{'id': PID, 'problem_id': SECOND}],
    [{'id': 'arbitrary-user-string'}],
    [{'id': PID}] * 51,
])
def test_invalid_or_oversized_batches_never_read_statements_or_send_ai(setup, invalid_items):
    service, db, ai, _, fetches = setup
    with pytest.raises(AIError):
        prepare(service, invalid_items)
    assert fetches == [] and ai.complete.await_count == 0 and count(db) == 0


def test_full_statement_is_never_silently_truncated_to_fit_context(setup, monkeypatch):
    service, db, ai, wb, _ = setup
    monkeypatch.setattr('acmweb.selection_reports.MAX_STATEMENT_BYTES', 100)
    save(db, statement(sections=['X' * 1000]))
    wb.statement = lambda pid: {'problem_id': pid}
    with pytest.raises(AIError, match='超出单题阅读上限'):
        prepare(service)
    assert ai.complete.await_count == 0 and count(db) == 0


# Exercise the real authentication/proxy stack, not only service.get isolation.
from test_multiuser import site


def _seed_report(service, report_id, pid=PID):
    with service.db.connect() as conn:
        conn.execute('INSERT INTO ai_selection_reports VALUES(?,?,?,?,?,?,?,?)',
            (report_id, pid, int(time.time()), 'api', 'synthetic-model', 'a' * 64,
             json.dumps(report(pid)), json.dumps({'task': 'selection_read_report'})))


def test_actual_multiuser_report_http_auth_and_workspace_isolation(site):
    request, admin, member, app, *_ = site
    for who in (admin, member):
        assert request(who, 'GET', '/api/accounts').status_code == 200
    own_service = app.state.workspaces.apps[admin['user']['id']][0].state.selection_reports
    other_service = app.state.workspaces.apps[member['user']['id']][0].state.selection_reports
    own_service.ai = SimpleNamespace(complete=AsyncMock())
    other_service.ai = SimpleNamespace(complete=AsyncMock())
    own_id, other_id = 'a' * 32, 'b' * 32
    _seed_report(own_service, own_id)
    _seed_report(other_service, other_id, SECOND)
    assert request(None, 'GET', f'/api/selection-reports/{own_id}').status_code == 401
    assert request(member, 'GET', f'/api/selection-reports/{own_id}').status_code == 404
    assert request(admin, 'GET', f'/api/selection-reports/{other_id}').status_code == 404
    own = request(admin, 'GET', f'/api/selection-reports/{own_id}')
    assert own.status_code == 200 and own.json()['problem_id'] == PID
    assert own.json()['result']['problem_id'] == PID
    other = request(member, 'GET', f'/api/selection-reports/{other_id}')
    assert other.status_code == 200 and other.json()['problem_id'] == SECOND
    assert own_service.ai.complete.await_count == other_service.ai.complete.await_count == 0
    assert request(admin, 'GET', '/api/selection-reports/not-a-report-id').status_code == 404
    assert request(admin, 'GET', '/api/selection-reports/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaZ').status_code == 404


def test_actual_singleuser_report_http_requires_existing_access_token(tmp_path):
    from fastapi.testclient import TestClient
    from acmweb.app import create_app
    from acmweb.settings import Settings
    token = 'synthetic-private-access-token'
    app = create_app(Settings(data_dir=tmp_path, enable_codex=False, access_token=token))
    service = app.state.selection_reports
    service.ai = SimpleNamespace(complete=AsyncMock())
    rid = 'c' * 32
    _seed_report(service, rid)
    with TestClient(app) as client:
        assert client.get(f'/api/selection-reports/{rid}').status_code == 401
        assert client.get(f'/api/selection-reports/{rid}', headers={'Authorization': 'Bearer wrong'}).status_code == 401
        response = client.get(f'/api/selection-reports/{rid}', headers={'Authorization': 'Bearer ' + token})
        assert response.status_code == 200 and response.json()['id'] == rid
        assert service.ai.complete.await_count == 0

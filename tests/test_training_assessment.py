"""Synthetic, local evidence only: no OJ, execution, submission or real AI calls."""
import asyncio
import hashlib
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from acmweb.codex_bridge import AIError
from acmweb.database import Database
from acmweb.training_assessment import AssessmentRequest, ChatRequest, TrainingAssessment, register
from acmweb.workbench import Workbench

PID = 'codeforces:4-A'
CONFIG = {'provider': 'api', 'model': 'synthetic-model', 'consent': True}
REVIEW = {'summary': '已通过，但仍需根据证据检查质量；未运行代码，结论是推测。',
          'dimensions': [{'category': 'boundaries', 'finding': '需要检查边界',
                          'evidence': '代码使用固定数组', 'suggestion': '核对最大输入与数组长度', 'confidence': 'low'}],
          'strengths': ['结构清晰'], 'next_steps': ['讨论复杂度'], 'overall_confidence': 'low'}


@pytest.fixture
def setup(tmp_path):
    db = Database(tmp_path/'user.sqlite3')
    accounts = SimpleNamespace(document=lambda: {'accounts': [{'platform': 'codeforces', 'handle': 'demo'}]})
    wb = Workbench(db, None, accounts)
    code = 'int main(){int n;return 0;}'
    digest = hashlib.sha256(code.encode()).hexdigest()
    now = int(time.time())
    with db.connect() as conn:
        conn.execute('INSERT INTO wb_problems VALUES(?,?,?)', (PID, json.dumps({'id': PID, 'statement': 'official statement', 'title': 'Watermelon'}), now))
        conn.execute('INSERT INTO wb_attempts(id,problem_id,language,code,code_hash,created_at,handle,submission_id,verdict,judge_data) VALUES(?,?,?,?,?,?,?,?,?,?)',
                     ('attempt-one', PID, 'cpp20', code, digest, now, 'demo', 12345, 'AC', json.dumps({'verdict': 'AC'})))
        conn.execute('INSERT INTO wb_submission_source_bindings VALUES(?,?,?,?,?,?)', ('attempt-one', PID, 'demo', 12345, 'cpp20', digest))
        conn.execute('INSERT INTO wb_drafts VALUES(?,?,?,?,?)', (PID, 'python3', 'NEVER SEND THIS DRAFT', 1, now))
    plan = {'id': 'plan-one', 'topic_id': 'binary-search', 'topic_title': '二分', 'title': '二分专项',
            'items': [{'position': 0, 'problem_id': PID, 'title': 'Watermelon', 'stage': 'template', 'completed': True}]}
    def get_plan(plan_id):
        if plan_id != 'plan-one':
            raise HTTPException(404, '不存在')
        return plan
    training = SimpleNamespace(get_plan=get_plan)
    ai = SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(data=REVIEW, provenance={'provider': 'synthetic'})))
    submissions = SimpleNamespace(source=lambda *_: pytest.fail('unexpected source fetch'))
    service = TrainingAssessment(db, ai, wb, submissions, training)
    return service, db, wb, ai, plan


def assess(service, **extra):
    return asyncio.run(service.assess('plan-one', 0, AssessmentRequest(**(CONFIG | extra))))


def chat(service, message='还有其他解法吗？'):
    return asyncio.run(service.chat('plan-one', 0, ChatRequest(**CONFIG, message=message)))


def test_accepted_evidence_uses_exact_source_and_cached_repeat(setup):
    service, db, wb, ai, _ = setup
    assert service.state('plan-one', 0)['assessment'] is None
    assert ai.complete.await_count == 0
    response = assess(service, attempt_id='attempt-one')
    material = ai.complete.call_args.args[3]
    assert material['submission']['source_code'] == wb.attempt('attempt-one')['code']
    assert 'NEVER SEND THIS DRAFT' not in json.dumps(material)
    assert material['submission']['submission_id'] == '12345'
    assert material['submission']['verdict'] == 'AC'
    assert not response['busy']
    assert response['assessment']['result']['dimensions'][0]['category'] == 'boundaries'
    assert response['assessment']['submission']['attempt_id'] == 'attempt-one'
    assert 'source_code' not in json.dumps(response)
    assess(service, attempt_id='attempt-one')
    assert ai.complete.await_count == 1
    assert not service.active_ai and not service.pending
    with db.connect() as conn:
        assert conn.execute('SELECT code FROM wb_drafts').fetchone()[0] == 'NEVER SEND THIS DRAFT'


@pytest.mark.parametrize('kind', ['source_hash', 'language', 'verdict', 'account', 'statement'])
def test_saved_context_invalidated_for_evidence_changes(setup, kind):
    service, db, wb, ai, _ = setup
    assess(service)
    with db.connect() as conn:
        if kind == 'source_hash':
            conn.execute("UPDATE wb_attempts SET code='different source'")
        elif kind == 'language':
            conn.execute("UPDATE wb_attempts SET language='python3'")
        elif kind == 'verdict':
            conn.execute("UPDATE wb_attempts SET verdict='WA'")
        elif kind == 'statement':
            conn.execute("UPDATE wb_problems SET data='{}'")
        else:
            wb.accounts.document = lambda: {'accounts': [{'platform': 'codeforces', 'handle': 'other'}]}
    state = service.state('plan-one', 0)
    assert state['assessment'] is None
    assert state['messages'] == []
    with pytest.raises(AIError):
        chat(service)
    assert ai.complete.await_count == 1


def test_no_source_does_not_use_claim_or_draft(setup):
    service, db, _, ai, _ = setup
    with db.connect() as conn:
        conn.execute('DELETE FROM wb_submission_source_bindings')
    def unavailable(*_):
        raise HTTPException(400, 'upstream detail must not be exposed')
    service.submissions.source = unavailable
    state = service.state('plan-one', 0)
    assert state['sources'][0]['can_fetch_source']
    with pytest.raises(AIError, match='该次官方源码暂时无法读取'):
        assess(service)
    assert ai.complete.await_count == 0
    assert service.state('plan-one', 0)['assessment'] is None


def test_fetches_only_the_selected_official_source_and_statement(setup):
    service, db, _, ai, _ = setup
    with db.connect() as conn:
        binding = dict(conn.execute('SELECT * FROM wb_submission_source_bindings').fetchone())
        conn.execute('DELETE FROM wb_submission_source_bindings')
        conn.execute('DELETE FROM wb_problems')
    captured = []
    def source(pid, req):
        captured.append((pid, req.handle, req.submission_id))
        with db.connect() as conn:
            conn.execute('INSERT INTO wb_submission_source_bindings VALUES(?,?,?,?,?,?)', tuple(binding.values()))
        return {'attempt_id': 'attempt-one'}
    def statement(pid):
        with db.connect() as conn:
            conn.execute('INSERT INTO wb_problems VALUES(?,?,?)', (pid, json.dumps({'id': pid, 'statement': 'downloaded official statement'}), int(time.time())))
    service.submissions.source = source
    service.wb.statement = statement
    result = assess(service, submission_id='12345', handle='demo')
    assert captured == [(PID, 'demo', '12345')]
    assert result['assessment']
    assert ai.complete.call_args.args[3]['problem']['statement'] == 'downloaded official statement'


def test_statement_failure_not_sent_to_ai(setup):
    service, db, wb, ai, _ = setup
    with db.connect() as conn:
        conn.execute('DELETE FROM wb_problems')
    wb.statement = lambda *_: (_ for _ in ()).throw(RuntimeError('private upstream body'))
    with pytest.raises(AIError, match='尚未发送源码'):
        assess(service)
    assert ai.complete.await_count == 0


@pytest.mark.parametrize('verdict', ['WA', 'PENDING', 'TESTING', 'CE'])
def test_only_official_ac_is_assessed(setup, verdict):
    service, db, _, ai, plan = setup
    with db.connect() as conn:
        conn.execute('UPDATE wb_attempts SET verdict=?', (verdict,))
    assert not service.state('plan-one', 0)['sources'][0]['source_available']
    with pytest.raises(AIError):
        assess(service)
    assert ai.complete.await_count == 0


def test_incomplete_training_and_wrong_submission_are_rejected(setup):
    service, _, _, ai, plan = setup
    with pytest.raises(AIError, match='没有找到'):
        assess(service, submission_id='99999')
    plan['items'][0]['completed'] = False
    with pytest.raises(AIError, match='请先同步'):
        assess(service)
    assert ai.complete.await_count == 0


def test_chat_persists_agent_style_turns_and_failed_retry_keeps_history(setup):
    service, _, _, ai, _ = setup
    assess(service)
    ai.complete.return_value = SimpleNamespace(data={'reply': '可以比较排序与计数的复杂度；这只是基于代码的推测。'}, provenance={})
    result = chat(service)
    assert not result['busy']
    assert [m['role'] for m in result['messages']] == ['user', 'assistant']
    assert ai.complete.call_args.args[0] == 'specialist_chat'
    assert ai.complete.call_args.args[3]['submission']['submission_id'] == '12345'
    ai.complete.side_effect = AIError('synthetic network failure')
    with pytest.raises(AIError):
        chat(service, '继续')
    assert len(service.state('plan-one', 0)['messages']) == 2
    ai.complete.side_effect = None
    assert len(chat(service, '重试')['messages']) == 4


def test_failed_assessment_and_full_implementation_not_saved(setup):
    service, _, _, ai, _ = setup
    ai.complete.side_effect = AIError('synthetic failure')
    with pytest.raises(AIError):
        assess(service)
    assert service.state('plan-one', 0)['assessment'] is None
    ai.complete.side_effect = None
    ai.complete.return_value = SimpleNamespace(data=REVIEW | {'summary': '```cpp\nint main(){}\n```'}, provenance={})
    with pytest.raises(AIError, match='完整实现'):
        assess(service)
    assert service.state('plan-one', 0)['assessment'] is None


def test_rejudge_during_ai_does_not_save_stale_result(setup):
    service, db, _, ai, _ = setup
    async def changed(*_):
        with db.connect() as conn:
            conn.execute("UPDATE wb_attempts SET verdict='WA'")
        return SimpleNamespace(data=REVIEW, provenance={})
    ai.complete.side_effect = changed
    with pytest.raises(AIError):
        assess(service)
    assert service.state('plan-one', 0)['assessment'] is None


def test_duplicate_clicks_and_runtime_guard(setup):
    service, _, _, ai, _ = setup
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def slow(*_):
            assert service.active_ai
            started.set()
            await release.wait()
            return SimpleNamespace(data=REVIEW, provenance={})
        ai.complete.side_effect = slow
        task = asyncio.create_task(service.assess('plan-one', 0, AssessmentRequest(**CONFIG)))
        await started.wait()
        with pytest.raises(HTTPException) as err:
            await service.assess('plan-one', 0, AssessmentRequest(**CONFIG))
        assert err.value.status_code == 409
        release.set()
        await task
    asyncio.run(scenario())
    service.guard = lambda: (_ for _ in ()).throw(HTTPException(429, 'budget exhausted'))
    with pytest.raises(HTTPException) as err:
        assess(service)
    assert err.value.status_code == 429
    assert not service.active_ai


def test_input_consent_and_ownership_404(setup, tmp_path):
    service, _, _, ai, _ = setup
    app = FastAPI()
    register(app, service)
    with TestClient(app) as client:
        assert client.post('/api/training/plans/plan-one/items/0/assessment', json=CONFIG | {'consent': False}).status_code == 422
        assert client.post('/api/training/plans/plan-one/items/0/chat', json=CONFIG | {'message': 'x'*2001}).status_code == 422
        assert client.post('/api/training/plans/plan-one/items/0/assessment', json=CONFIG | {'code': 'untrusted'}).status_code == 422
        assert client.get('/api/training/plans/other-users-plan/items/0/assessment').status_code == 404
        assert client.get('/api/training/plans/plan-one/items/8/assessment').status_code == 404
    assert ai.complete.await_count == 0
    other_db = Database(tmp_path/'other.sqlite3')
    other = TrainingAssessment(other_db, ai, service.wb, service.submissions,
                               SimpleNamespace(get_plan=lambda _: (_ for _ in ()).throw(HTTPException(404))))
    with pytest.raises(HTTPException) as err:
        other.state('plan-one', 0)
    assert err.value.status_code == 404


def test_twelve_turn_limit_does_not_call_ai(setup):
    service, db, _, ai, _ = setup
    assess(service)
    with db.connect() as conn:
        conn.execute('UPDATE specialist_assessments SET messages=?', (json.dumps([{'role': 'user', 'content': 'previous'}]*24),))
    with pytest.raises(AIError, match='12 轮'):
        chat(service)
    assert ai.complete.await_count == 1


def test_official_cf_handle_case_does_not_break_verified_source(setup):
    service, db, wb, ai, _ = setup
    original = wb.history
    def history(pid):
        result = original(pid)
        result['items'][0]['account'] = 'DeMo'
        return result
    wb.history = history
    assert service.state('plan-one', 0)['sources'][0]['source_available']
    assert assess(service)['assessment']


def test_multiple_submissions_require_unambiguous_selection(setup):
    service, db, wb, ai, _ = setup
    with db.connect() as conn:
        conn.execute("INSERT INTO wb_attempts SELECT 'attempt-two',problem_id,language,code,code_hash,created_at,handle,23456,verdict,judge_data,review,last_synced FROM wb_attempts WHERE id='attempt-one'")
        conn.execute("INSERT INTO wb_submission_source_bindings SELECT 'attempt-two',problem_id,handle,23456,language,code_hash FROM wb_submission_source_bindings WHERE attempt_id='attempt-one'")
    with pytest.raises(AIError, match='多次通过'):
        assess(service)
    with pytest.raises(AIError, match='没有找到'):
        assess(service, attempt_id='attempt-one', submission_id='23456')
    assert assess(service, attempt_id='attempt-two')['assessment']['submission']['submission_id'] == '23456'


def test_malformed_ai_output_is_a_safe_retryable_error(setup):
    service, _, _, ai, _ = setup
    ai.complete.return_value = SimpleNamespace(data={'secret': 'private model text'}, provenance={})
    with pytest.raises(AIError, match='格式不符合') as err:
        assess(service)
    assert 'private model text' not in str(err.value)
    assert service.state('plan-one', 0)['assessment'] is None

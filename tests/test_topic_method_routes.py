"""Explicit method probes use saved AI settings and existing user boundaries.

All providers and platform verification in these tests are synthetic.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.codex_bridge import AIError
from acmweb.database import Database
from acmweb.jobs import Jobs
from acmweb.prompts import TASK_POLICIES, build_prompt
from acmweb.settings import Settings
from test_multiuser import site


def test_method_route_validates_auth_scope_and_saved_model_before_queuing(tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path, config_path=tmp_path/'absent',
        enable_codex=False, access_token='synthetic-method-test-token'))
    token = {'Authorization': 'Bearer synthetic-method-test-token'}
    start = Mock(return_value={'id': 'probe', 'status': 'queued'})
    monkeypatch.setattr(app.state.jobs, 'start', start)
    ready = {'ready': True, 'channel': 'api', 'model': 'saved-model'}
    monkeypatch.setattr(app.state.ai, 'selection', lambda: ready)
    with TestClient(app) as client:
        path = '/api/training/method-check'
        assert client.post(path, json={'topic_id': 'dsu'}).status_code == 401
        for payload in ({'topic_id': 'missing'}, {'topic_id': 'dsu', 'provider': 'api'},
                {'topic_id': 'dsu', 'model': 'override'}, {'topic_id': 'dsu', 'cf_minimum': 700},
                {'topic_id': 'dsu', 'cf_minimum': 1800, 'cf_maximum': 1200}):
            assert client.post(path, headers=token, json=payload).status_code == 422
        assert client.post(path, headers=token, json={'topic_id': 'dsu'}).status_code == 409
        monkeypatch.setattr('acmweb.catalog.verified_platforms', lambda _: {'codeforces'})
        ready['ready'] = False
        response = client.post(path, headers=token, json={'topic_id': 'dsu'})
        assert response.status_code == 400 and '默认 AI 模型' in response.text
        assert not start.called
        ready['ready'] = True
        response = client.post(path, headers=token,
            json={'topic_id': 'dsu', 'cf_minimum': 1000, 'cf_maximum': 1800})
        assert response.status_code == 200
        start.assert_called_once_with('topic_method_check', {'topic_id': 'dsu',
            'cf_minimum': 1000, 'cf_maximum': 1800, 'provider': 'api', 'model': 'saved-model'})


def test_method_runner_refuses_changed_saved_model(tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path, config_path=tmp_path/'absent', enable_codex=False))
    runner = AsyncMock(return_value={'checked_count': 1})
    monkeypatch.setattr(app.state.topic_feasibility, 'check', runner)
    monkeypatch.setattr(app.state.ai, 'selection', lambda: {'ready': True, 'channel': 'api', 'model': 'new'})
    payload = {'topic_id': 'dsu', 'provider': 'api', 'model': 'old'}
    with pytest.raises(AIError, match='配置已更改'):
        asyncio.run(app.state.jobs.run_topic_method_check(payload))
    runner.assert_not_called()
    payload['model'] = 'new'
    asyncio.run(app.state.jobs.run_topic_method_check(payload))
    runner.assert_awaited_once_with('dsu', 'api', 'new', cf_minimum=None, cf_maximum=None)


@pytest.mark.parametrize('incomplete,status', [(False, 'completed'), (True, 'partial')])
def test_method_job_keeps_per_item_result_without_retry(tmp_path, incomplete, status):
    jobs = Jobs(Database(tmp_path/'db'), SimpleNamespace(), None)
    result = {'checked_count': 2, 'accepted_count': 1, 'uncertain_count': 1,
        'rejected_count': 0, 'incomplete': incomplete, 'items': [{'decision': 'yes'}]}
    jobs.run_topic_method_check = AsyncMock(return_value=result)
    async def run():
        queued = jobs.start('topic_method_check', {'topic_id': 'dsu'})
        await jobs.tasks[queued['id']][1]
        return jobs.list()[0]
    row = asyncio.run(run())
    assert row['status'] == status and row['result'] == result
    jobs.run_topic_method_check.assert_awaited_once()


def test_method_route_keeps_multiuser_csrf_and_expensive_action_limit(site, monkeypatch):
    request, owner, peer, app, *_ = site
    assert request(owner, 'GET', '/api/training/topics').status_code == 200
    own_app = app.state.workspaces.apps[owner['user']['id']][0]
    monkeypatch.setattr('acmweb.catalog.verified_platforms',
        lambda accounts: {'codeforces'} if accounts is own_app.state.account_config else set())
    monkeypatch.setattr(own_app.state.ai, 'selection',
        lambda: {'ready': True, 'channel': 'api', 'model': 'owner-model'})
    start = Mock(return_value={'id': 'private-job', 'status': 'queued'})
    monkeypatch.setattr(own_app.state.jobs, 'start', start)
    path = '/api/training/method-check'
    assert request(None, 'POST', path, {'topic_id': 'dsu'}).status_code == 401
    assert request(owner, 'POST', path, {'topic_id': 'dsu'}, extra={'X-CSRF-Token': 'bad'}).status_code == 403
    assert request(owner, 'POST', path, {'topic_id': 'dsu'}, extra={'Origin': 'https://evil.example'}).status_code == 403
    assert request(peer, 'POST', path, {'topic_id': 'dsu'}).status_code == 409
    for _ in range(6):
        assert request(owner, 'POST', path, {'topic_id': 'dsu'}).status_code == 200
    assert request(owner, 'POST', path, {'topic_id': 'dsu'}).status_code == 429
    assert start.call_count == 6
    assert request(peer, 'GET', '/api/jobs').json() == {'items': []}


def test_method_prompt_requires_reading_constraints_and_preserves_uncertainty():
    policy = TASK_POLICIES['topic_method_feasibility']
    for phrase in ('官方完整题面', 'uncertain', '复杂度', '模板', '推测', '完整代码', '不可信'):
        assert phrase in policy
    prompt = build_prompt('topic_method_feasibility', {'problem_id': 'codeforces:1-A',
        'statement': {'sections': [{'content': 'UNTRUSTED-STATEMENT'}]}})
    assert 'UNTRUSTED-STATEMENT' in prompt and policy in prompt

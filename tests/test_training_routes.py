"""Authenticated integration boundaries; temporary users and synthetic AI only."""
import json
import time
from unittest.mock import AsyncMock

import pytest

from test_multiuser import site
from acmweb.codex_bridge import AIError


def workspace(site, user):
    request, _, _, app, *_ = site
    assert request(user, 'GET', '/api/training/plans').status_code == 200
    return app.state.workspaces.apps[user['user']['id']][0]


def test_training_routes_preserve_preferences_and_isolate_plans_and_assessments(site):
    request, admin, member, *_ = site
    assert request(None, 'GET', '/api/training/topics').status_code == 401
    own = workspace(site, admin)
    prefs = request(admin, 'GET', '/api/training').json()
    assert 'minimum' in prefs and 'plans' not in prefs
    topics = request(admin, 'GET', '/api/training/topics').json()['groups']
    assert len(topics) >= 20 and sum(len(group['topics']) for group in topics) >= 262
    assert 'dsu' in next(t for g in topics for t in g['topics'] if t['id'] == 'luogu-47')['keywords']
    plan = {'id': 'private-plan', 'topic_id': 'dsu', 'topic_title': '并查集',
            'title': 'PRIVATE-TITLE', 'created_at': int(time.time()),
            'items': [{'position': 0, 'problem_id': 'luogu:P3367', 'completed': False}]}
    with own.state.database.connect() as db:
        db.execute('INSERT INTO training_plans VALUES(?,?,?,?)',
                   (plan['id'], 'dsu', plan['created_at'], json.dumps(plan)))
    assert request(admin, 'GET', '/api/training/plans').json()['plans'][0]['title'] == 'PRIVATE-TITLE'
    assert request(member, 'GET', '/api/training/plans').json()['plans'] == []
    assert request(member, 'GET', '/api/training/plans/private-plan').status_code == 404
    assert request(member, 'GET', '/api/training/plans/private-plan/items/0/assessment').status_code == 404
    assert request(member, 'POST', '/api/training/plans/private-plan/items/0/chat', {
        'provider': 'api', 'model': 'synthetic', 'consent': True, 'message': '讨论他人的题目',
    }).status_code == 404


def test_training_writes_require_csrf_and_reject_owner_or_source_injection(site):
    request, admin, member, *_ = site
    workspace(site, admin)
    payload = {'topic_id': 'dsu', 'count': 4, 'provider': 'api', 'model': 'synthetic'}
    assert request(admin, 'POST', '/api/training/plans', payload,
                   extra={'X-CSRF-Token': 'wrong'}).status_code == 403
    assert request(admin, 'POST', '/api/training/plans', {
        **payload, 'owner_id': member['user']['id'],
    }).status_code == 422
    assert request(admin, 'POST', '/api/training/plans/private/items/0/assessment', {
        'provider': 'api', 'model': 'synthetic', 'consent': True, 'source_code': 'client-forged-code',
    }).status_code == 422
    assert request(admin, 'POST', '/api/training/plans/private/items/0/assessment', {
        'provider': 'api', 'model': 'synthetic', 'consent': False,
    }).status_code == 422


def test_explicit_assessment_and_chat_posts_do_not_require_a_checkbox_field(site):
    request, admin, *_ = site
    own = workspace(site, admin)
    own.state.training_assessment.assess = AsyncMock(return_value={'assessment': {'synthetic': True}})
    own.state.training_assessment.chat = AsyncMock(return_value={'messages': []})
    payload = {'provider': 'api', 'model': 'synthetic'}
    assert request(admin, 'POST', '/api/training/plans/p/items/0/assessment', payload).status_code == 200
    assert request(admin, 'POST', '/api/training/plans/p/items/0/chat', {
        **payload, 'message': '还有哪些边界需要考虑？',
    }).status_code == 200
    assert own.state.training_assessment.assess.await_count == 1
    assert own.state.training_assessment.chat.await_count == 1


def test_training_paid_requests_share_existing_per_user_rate_limit(site):
    request, admin, member, *_ = site
    own, other = workspace(site, admin), workspace(site, member)
    own.state.specialist_training.generate = AsyncMock(return_value={'id': 'synthetic-plan'})
    other.state.specialist_training.generate = AsyncMock(return_value={'id': 'other-plan'})
    payload = {'topic_id': 'dsu', 'count': 4, 'provider': 'api', 'model': 'synthetic'}
    for _ in range(6):
        assert request(admin, 'POST', '/api/training/plans', payload).status_code == 200
    assert request(admin, 'POST', '/api/training/plans', payload).status_code == 429
    assert own.state.specialist_training.generate.await_count == 6
    assert request(member, 'POST', '/api/training/plans', payload).status_code == 200


def test_specialist_ai_is_included_in_shared_server_budget(site):
    _, admin, member, app, *_ = site
    own, other = workspace(site, admin), workspace(site, member)
    own.state.daily.active_ai = True
    own.state.specialist_training.active_ai = True
    own.state.training_assessment.active_ai = True
    other.state.specialist_training.active_ai = True
    with pytest.raises(AIError, match='服务器任务已满'):
        app.state.workspaces.budget()
    other.state.specialist_training.active_ai = False
    app.state.workspaces.budget()

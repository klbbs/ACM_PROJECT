"""Selection/report integration cases, written but not run at user request."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from acmweb.codex_bridge import AIError
from acmweb.database import Database
from acmweb.jobs import Jobs


@pytest.fixture
def job(tmp_path, monkeypatch):
    database = Database(tmp_path / 'reports.sqlite3')
    candidate = {'id': 'codeforces:1000-A', 'platform': 'codeforces', 'title': 'Fixture'}
    eligible = {'valid': True}
    monkeypatch.setattr('acmweb.jobs.analytics.overview',
                        lambda *args: {'stats': {'submissions': 1}, 'days': 30})
    monkeypatch.setattr('acmweb.jobs.recommendation_status', lambda *a, **k: {})
    monkeypatch.setattr('acmweb.jobs.recommendation_candidates',
                        lambda *a, **k: [candidate] if eligible['valid'] else [])
    monkeypatch.setattr('acmweb.jobs.refresh_recommendation_sessions', AsyncMock())
    ai = SimpleNamespace(generate=AsyncMock(return_value={
        'selected_problems': [{**candidate, 'reason': 'Training reason'}]}))
    service = Jobs(database, SimpleNamespace(), ai, SimpleNamespace())
    service.selection_reports = SimpleNamespace(prepare=AsyncMock(return_value=[
        {**candidate, 'reason': 'Training reason', 'ai_report_id': 'a' * 32}]))
    now = int(time.time())
    with database.connect() as conn:
        conn.execute('INSERT INTO web_jobs VALUES(?,?,?,?,?,?,?)',
                     ('selection-job', 'recommend', 'queued', now, now, None, None))
    return service, eligible


def run(service):
    asyncio.run(service._run('selection-job', 'recommend', {'provider': 'api', 'model': 'fixture'}))
    return service.list()[0]


def test_recommendation_attaches_only_matching_report_reference(job):
    service, _ = job
    assert run(service)['status'] == 'completed'
    service.selection_reports.prepare.assert_awaited_once()
    saved = service.db.report(service.db.reports()[0]['id'])['data']['selected_problems'][0]
    assert saved['id'] == 'codeforces:1000-A'
    assert saved['ai_report_id'] == 'a' * 32
    assert 'result' not in saved and 'approach' not in saved


def test_report_failure_keeps_existing_recommendation(job):
    service, _ = job
    with service.db.connect() as conn:
        conn.execute('INSERT INTO ai_reports VALUES(?,?,?,?,?,?)',
                     ('previous', 1, 'recommend', 'api', 'fixture', '{"selected_problems":[]}'))
    service.selection_reports.prepare.side_effect = AIError('无法获取官方题面，未确认选题')
    assert run(service)['status'] == 'failed'
    assert [row['id'] for row in service.db.reports()] == ['previous']


def test_report_delay_does_not_save_newly_ineligible_candidate(job):
    service, eligible = job

    async def prepare(items, provider, model):
        eligible['valid'] = False
        return [{**item, 'ai_report_id': 'a' * 32} for item in items]

    service.selection_reports.prepare.side_effect = prepare
    result = run(service)
    assert result['status'] == 'failed' and '资格已变化' in result['error']
    assert service.db.reports() == []


def test_uninitialized_report_service_cannot_publish_unread_recommendation(job):
    service, _ = job
    service.selection_reports = None
    result = run(service)
    assert result['status'] == 'failed' and '报告服务尚未初始化' in result['error']
    assert service.db.reports() == []


def test_workbench_preserves_report_only_for_exact_saved_ai_choice(job, monkeypatch):
    from acmweb.workbench import Workbench
    import json

    service, _ = job
    selected = {'id': 'codeforces:1000-A', 'reason': 'Chosen for training', 'ai_report_id': 'a' * 32}
    with service.db.connect() as conn:
        conn.execute('INSERT INTO ai_reports VALUES(?,?,?,?,?,?)',
                     ('saved', 1, 'recommend', 'api', 'fixture',
                      json.dumps({'selected_problems': [selected]})))
    pool = [{'id': 'codeforces:1000-A', 'platform': 'codeforces', 'difficulty': 1000},
            {'id': 'codeforces:1001-A', 'platform': 'codeforces', 'difficulty': 1000,
             'ai_report_id': 'UNRELATED-CATALOG-REPORT'}]
    monkeypatch.setattr('acmweb.workbench.recommendation_candidates', lambda *a, **k: pool)
    monkeypatch.setattr('acmweb.workbench.recommendation_status', lambda *a, **k: {})
    accounts = SimpleNamespace(document=lambda: {'accounts': []})
    items = Workbench(service.db, None, accounts).choices()['items']
    chosen = next(row for row in items if row['id'] == selected['id'])
    ordinary = next(row for row in items if row['id'] != selected['id'])
    assert chosen['ai_report_id'] == 'a' * 32
    assert chosen['recommendation_source'] == '已保存的 AI 推荐'
    assert ordinary['recommendation_source'] == '训练规则匹配'
    assert 'ai_report_id' not in ordinary

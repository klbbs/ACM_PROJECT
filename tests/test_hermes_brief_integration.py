"""Offline integration of aggregate metadata with multi-topic Hermes queries.

These tests use synthetic catalog rows and do not validate real OJ submissions.
"""
import json

import pytest
from fastapi.testclient import TestClient

from acmhermes.difficulty import score_candidate
from acmhermes.models import Draw
from acmweb.app import create_app
from acmweb.catalog_briefs import CF_STATISTICS, brief_statistics
from acmweb.settings import Settings
from test_hermes_api import setup_pipeline, PID


@pytest.fixture
def metadata(tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path, config_path=tmp_path / 'missing.toml',
                              enable_codex=False, enable_hermes=True))
    service, ai = setup_pipeline(app, monkeypatch)
    briefs = {}
    with service.db.connect() as db:
        for number, (pid, tags) in enumerate(((PID, ['dp', 'games']),
                        ('codeforces:4-B', ['dp']), ('codeforces:4-C', ['games']))):
            brief = brief_statistics(source=CF_STATISTICS, scope='all_time_users',
                                     solved_count=1000 + number, observed_at=1700000000)
            briefs[pid] = brief
            db.execute('INSERT OR REPLACE INTO problem_catalog VALUES(?,?,?)',
                       ('codeforces', pid, json.dumps({'id': pid, 'platform': 'codeforces',
                        'difficulty': 1000, 'tags': tags, 'title': 'Synthetic metadata task',
                        'contest_id': 4, 'brief_statistics': brief})))
    with TestClient(app) as client:
        yield client, service, ai, briefs


@pytest.mark.parametrize('query, expected', [
    ('dp,博弈论', {PID}), ('dp&博弈论', {PID}),
    ('dp|博弈论', {PID, 'codeforces:4-B', 'codeforces:4-C'}),
    ('dp|博弈论&math', {PID, 'codeforces:4-B'}),
])
def test_brief_survives_metadata_and_expression_filtering_without_network(metadata, query, expected):
    client, service, ai, briefs = metadata
    request = Draw(score_range=(800, 1200), topic=query)
    rows = service.pool(request)
    assert {row['id'] for row in rows} == expected
    assert all(row['brief_statistics'] == briefs[row['id']] for row in rows)
    response = client.get('/api/hermes/candidates', params={
        'minimum': 800, 'maximum': 1200, 'topic': query})
    assert response.status_code == 200
    data = response.json()
    assert data['diagnostics']['candidate_count'] == len(expected)
    assert {row['id'] for row in data['items']} == expected
    # Query paths only read stored metadata; they never send it to AI.
    ai.assert_not_awaited()
    service.ensure_catalog.assert_not_awaited()
    service.refresh_evidence.assert_not_awaited()
    service.sync_history.assert_not_awaited()


def test_solved_people_are_exposure_not_a_denominator_or_rating():
    row = {'id': 'codeforces:4-A', 'platform': 'codeforces', 'contest_id': 4,
           'difficulty': 1800, 'published_at': 1700000000}
    baseline = score_candidate(row, 1800, 1800, now=1800000000)
    for solved in (0, 1, 1000000):
        brief = brief_statistics(source=CF_STATISTICS, scope='all_time_users',
                                 solved_count=solved, observed_at=1700000000)
        result = score_candidate({**row, 'brief_statistics': brief}, 1800, 1800, now=1800000000)
        assert result['estimated_rating'] == baseline['estimated_rating'] == 1800
        assert result['empirical_rating'] is None
        assert result['eligible'] is True
        assert 'pass_rate' not in result['evidence']
        assert 'acceptance_rate' not in brief

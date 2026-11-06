"""Synthetic aggregate-list regression tests; no real OJ submission claims."""
import json

import pytest

from acmweb import catalog
from acmweb.catalog_briefs import (brief_statistics, CF_STATISTICS, LUOGU_STATISTICS,
                                   NOWCODER_STATISTICS)
from acmweb.database import Database
from acmweb.expanded_topic_sources import _parse_nowcoder_page


def test_cf_batch_preserves_solved_users_without_invented_acceptance_rate():
    class PublicClient:
        def __init__(self):
            self.calls = []

        def json(self, url, params=None):
            self.calls.append((url, params))
            if url == CF_STATISTICS:
                return {'status': 'OK', 'result': {
                    'problems': [{'contestId': 100, 'index': 'A', 'name': 'Task A',
                                  'rating': 1800, 'tags': ['dp']},
                                 {'contestId': 100, 'index': 'B', 'name': 'Task B',
                                  'rating': 2000, 'tags': ['games']}],
                    'problemStatistics': [{'contestId': 100, 'index': 'B', 'solvedCount': 234},
                                          {'contestId': 100, 'index': 'A', 'solvedCount': 1234}]}}
            assert url == 'https://codeforces.com/api/contest.list'
            return {'status': 'OK', 'result': [
                {'id': 100, 'phase': 'FINISHED', 'startTimeSeconds': 1700000000}]}
    client = PublicClient()
    rows = catalog._cf(client)
    assert len(rows) == 2
    assert [row['brief_statistics']['solved_count'] for row in rows] == [1234, 234]
    for row in rows:
        brief = row['brief_statistics']
        assert brief['scope'] == 'all_time_users' and brief['source'] == CF_STATISTICS
        assert 'acceptance_rate' not in brief and 'submitted_count' not in brief
        assert row['published_at_source'] == 'codeforces.contest.startTimeSeconds'
    # One bulk problemset and one contest list, never per-problem requests.
    assert client.calls == [(CF_STATISTICS, None),
                            ('https://codeforces.com/api/contest.list', {'gym': 'false'})]


@pytest.mark.parametrize('value', [True, -1, '123', float('nan'), 10**13])
def test_invalid_counts_cannot_enter_brief_statistics(value):
    assert brief_statistics(source=CF_STATISTICS, scope='all_time_users', solved_count=value) is None


def test_cumulative_submission_rate_requires_a_real_valid_denominator():
    base = dict(source=LUOGU_STATISTICS, scope='all_time_submissions')
    assert 'acceptance_rate' not in brief_statistics(**base, accepted_count=10)
    assert 'acceptance_rate' not in brief_statistics(**base, submitted_count=0, accepted_count=0)
    invalid = brief_statistics(**base, submitted_count=10, accepted_count=20)
    assert invalid['submitted_count'] == 10 and 'accepted_count' not in invalid
    assert 'acceptance_rate' not in invalid
    valid = brief_statistics(**base, submitted_count=40, accepted_count=10)
    assert valid['acceptance_rate'] == .25
    assert brief_statistics(source='https://evil.example', scope='all_time_users', solved_count=1) is None


def test_luogu_list_retains_native_metadata_and_submission_aggregates(monkeypatch):
    monkeypatch.setattr(catalog, 'page_data', lambda _: {'problems': {'result': [
        {'pid': 'P1001', 'name': 'Task', 'difficulty': 1, 'tags': [1, 2],
         'totalSubmit': 100, 'totalAccepted': 25},
        {'pid': 'P1002', 'name': 'Unknown statistics', 'difficulty': 2, 'tags': []}]}})
    rows = catalog.parse_luogu_catalog('synthetic list')
    assert rows[0]['tags'] == ['1', '2'] and rows[0]['native_difficulty'] == 1
    assert rows[0]['brief_statistics']['acceptance_rate'] == .25
    assert rows[0]['brief_statistics']['source'] == LUOGU_STATISTICS
    assert 'brief_statistics' not in rows[1]


def test_nowcoder_matches_real_header_and_colspan_keeps_only_solved_users():
    html = '''<table><tbody><tr><th>题号</th><th>标题</th><th>知识点</th>
      <th data-type="difficulty">难度</th><th data-type="acceptPerson">通过人数</th><th>操作</th></tr>
      <tr data-problemid="1001"><td>NC1001</td><td colspan="2">
      <a class="title" href="/acm/problem/1001">Task</a></td><td>1800</td><td>1,234</td><td></td></tr>
      </tbody></table>'''
    rows, _, _, _ = _parse_nowcoder_page(html)
    brief = rows[0]['brief_statistics']
    assert brief['solved_count'] == 1234 and brief['source'] == NOWCODER_STATISTICS
    assert brief['scope'] == 'all_time_users' and 'acceptance_rate' not in brief
    # A bare numeric column without a known header has no proved semantics.
    rows, _, _, _ = _parse_nowcoder_page(html.replace('data-type="acceptPerson"', 'data-type="unknown"'))
    assert 'brief_statistics' not in rows[0]


def test_resumed_catalog_merge_keeps_previous_evidence_without_mixing_snapshots(tmp_path):
    db = Database(tmp_path / 'catalog.sqlite3')
    old = catalog._problem('luogu', 'P1001', 'First', tags=['1'],
        tags_source='luogu.official_problem_list',
        contest_statistics={'source': 'existing_public_contest_evidence'},
        brief_statistics=brief_statistics(source=LUOGU_STATISTICS, scope='all_time_submissions',
                                         submitted_count=100, accepted_count=25, observed_at=1700000000))
    catalog._store_catalog_rows(db, 'luogu', [old])
    catalog._store_catalog_rows(db, 'luogu', [catalog._problem('luogu', 'P1001', 'Refreshed')])
    with db.connect() as con:
        saved = json.loads(con.execute('SELECT data FROM problem_catalog').fetchone()[0])
    assert saved['brief_statistics'] == old['brief_statistics']
    assert saved['contest_statistics'] == old['contest_statistics']
    # New numerator-only snapshot must not reuse an older denominator.
    fresh = catalog._problem('luogu', 'P1001', 'Refreshed',
        brief_statistics=brief_statistics(source=LUOGU_STATISTICS, scope='all_time_submissions',
                                         accepted_count=30, observed_at=1700000010))
    catalog._store_catalog_rows(db, 'luogu', [fresh])
    with db.connect() as con:
        saved = json.loads(con.execute('SELECT data FROM problem_catalog').fetchone()[0])
    assert saved['brief_statistics']['accepted_count'] == 30
    assert 'submitted_count' not in saved['brief_statistics']
    assert 'acceptance_rate' not in saved['brief_statistics']
    assert saved['contest_statistics'] == old['contest_statistics']


def test_atcoder_bulk_model_is_reused_and_native_points_remain(monkeypatch):
    from types import SimpleNamespace
    from acmweb.difficulty_estimates import ATCODER_POINTS
    calls = []
    def get_json(url):
        calls.append(url)
        assert url == ATCODER_POINTS  # No second model-index request.
        return [{'id': 'abc400_a', 'point': 100}]
    client = SimpleNamespace(catalog_atcoder_models={'abc400_a': 150}, json=get_json)
    rows = [catalog._problem('atcoder', 'abc400_a', 'Task')]
    enriched = catalog._enrich_atcoder_difficulties(client, rows)
    assert enriched[0]['atcoder_problem_difficulty'] == 150
    assert enriched[0]['atcoder_points'] == 100
    assert calls == [ATCODER_POINTS]

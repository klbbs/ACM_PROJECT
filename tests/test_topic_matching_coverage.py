"""Synthetic metadata regressions, not live platform or paid AI acceptance."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from acmweb import catalog
from acmweb.codex_bridge import AIError
from acmweb.database import Database
from acmweb.training import Training
from acmweb.training_topics import TOPICS, is_template, matches_topic, topic_evidence
from selection_report_fixtures import with_selection_reports


def accounts(*platforms, document=False):
    result = SimpleNamespace(credential_checks=SimpleNamespace(statuses=lambda: {'items': [
        {'platform': p, 'status': 'authenticated', 'login_verified': True} for p in platforms]}))
    if document:
        result.document = lambda: {'accounts': [{'platform': p, 'handle': 'test-user'} for p in platforms]}
    return result


def save(db, platform, key, title, **metadata):
    problem = catalog._problem(platform, key, title, **metadata)
    with db.connect() as con:
        con.execute('INSERT INTO problem_catalog VALUES(?,?,?)', (platform, problem['id'], json.dumps(problem)))
    return problem


@pytest.mark.parametrize(('tag', 'target'), [
    ('flows', 79), ('trees', 230), ('two pointers', 345),
    ('divide and conquer', 112), ('ternary search', 67),
    ('constructive algorithms', 318), ('fft', 313),
])
def test_cf_precise_official_tag_mapping_and_parent_coverage(tag, target):
    problem = {'platform': 'codeforces', 'title': 'An ordinary task', 'tags': [tag]}
    assert matches_topic(problem, TOPICS[f'luogu-{target}'])
    evidence = topic_evidence(problem, TOPICS[f'luogu-{target}'])
    assert evidence['source'] == 'official_codeforces_tag'
    assert evidence['terms'] == [tag]


@pytest.mark.parametrize(('platform', 'title', 'tags', 'target'), [
    ('codeforces', 'A task', ['digit dp'], 141),
    ('codeforces', 'SCC', ['graphs'], 179),
    ('atcoder', 'Fenwick Tree', [], 53),
    ('nowcoder', '连通性', ['Union-Find'], 47),
    ('nowcoder', '字符串练习', ['后缀自动机'], 101),
    ('atcoder', 'Matrix Multiplication', [], 272),
    ('codeforces', 'Count Paths', ['线性动态规划'], 444),
])
def test_exact_algorithm_names_across_platform_schemas(platform, title, tags, target):
    problem = {'platform': platform, 'title': title, 'tags': tags}
    assert matches_topic(problem, TOPICS[f'luogu-{target}'])
    assert topic_evidence(problem, TOPICS[f'luogu-{target}'])['source'] == 'official_title_or_text_tag'


def test_parent_includes_child_alias_but_child_does_not_accept_parent_or_sibling():
    tree = {'platform': 'nowcoder', 'title': 'A', 'tags': ['tree dp']}
    assert matches_topic(tree, TOPICS['luogu-3'])
    assert matches_topic(tree, TOPICS['luogu-152'])
    assert not matches_topic(tree, TOPICS['luogu-444'])
    assert not matches_topic({'platform': 'codeforces', 'title': 'A', 'tags': ['dp', 'trees']}, TOPICS['luogu-152'])
    assert not matches_topic({'platform': 'codeforces', 'title': 'A', 'tags': ['data structures']}, TOPICS['luogu-53'])
    assert not matches_topic({'platform': 'codeforces', 'title': 'Samurai', 'tags': ['strings']}, TOPICS['luogu-101'])
    assert not matches_topic({'platform': 'nowcoder', 'title': 'No metadata', 'tags': [47, '47']}, TOPICS['luogu-47'])
    assert not matches_topic({'platform': 'atcoder', 'title': 'Binary', 'tags': ['search']}, TOPICS['luogu-45'])
    assert topic_evidence({'platform': 'atcoder', 'title': 'Problem A'}, TOPICS['luogu-47']) == {
        'matched': False, 'source': 'unknown', 'terms': []}


def test_four_verified_platforms_merge_with_unknowns_visible_no_network_or_ai(tmp_path):
    db, ai = Database(tmp_path / 'mine.db'), AsyncMock()
    service = Training(db, ai, accounts(*catalog.PLATFORMS))
    save(db, 'luogu', 'P3367', '【模板】并查集', tags=[47])
    save(db, 'codeforces', '2000-A', 'A task', tags=['dsu'], difficulty=1500)
    save(db, 'atcoder', 'practice2_a', 'Disjoint Set Union')
    save(db, 'nowcoder', '1000', 'A task', tags=['union find'])
    save(db, 'atcoder', 'abc100_a', 'Ordinary untagged task')
    coverage = service.candidate_coverage('luogu-47')
    rows = {row['platform']: row for row in coverage['platforms']}
    assert coverage['eligible_count'] == 4 and coverage['template_count'] == 2
    assert rows['atcoder']['cached_count'] == 2 and rows['atcoder']['topic_count'] == 1
    assert rows['atcoder']['unknown_topic_count'] == 1
    assert rows['atcoder']['conditions'][0]['code'] == 'unknown_topic'
    assert all(row['verified'] and row['eligible_count'] == 1 for row in rows.values())
    assert {p['platform'] for p in service._pool(TOPICS['luogu-47'])} == set(catalog.PLATFORMS)
    assert '不代表平台全站无题' in coverage['notice']
    ai.complete.assert_not_called()
    # Another user's sessions are not a fallback, and even public caches cannot
    # make an unverified platform eligible for this workspace.
    service.accounts = accounts('codeforces')
    coverage = service.candidate_coverage('luogu-47')
    assert coverage['eligible_count'] == 1 and coverage['template_count'] == 0
    assert {p['platform'] for p in service._pool(TOPICS['luogu-47'])} == {'codeforces'}


def test_coverage_filter_stages_preserve_date_difficulty_completion_and_discovery(tmp_path):
    db = Database(tmp_path / 'filters.db')
    db.set('training', {'published_after': '2024-01-01', 'minimum': 1200, 'maximum': 1600})
    service = Training(db, AsyncMock(), accounts('luogu', 'codeforces'))
    save(db, 'luogu', 'P3367', '【模板】并查集', tags=[47])  # No invented date.
    save(db, 'luogu', 'P1', '并查集', tags=[47], published_at=1600000000, published_at_source='luogu.problem.createTime')
    save(db, 'codeforces', '1000-A', 'A', tags=['dsu'], difficulty=800)
    save(db, 'codeforces', '2000-B', 'B', tags=['dsu'], difficulty=1400,
         published_at=1800000000, published_at_source='codeforces.contest.startTimeSeconds')
    with db.connect() as con:
        con.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)', ('codeforces', 'mine', '123', '2000-B', 1, 'AC', '{}', 1))
    db.set('specialist_catalog_status:luogu:luogu-47', {'status': 'partial', 'count': 1,
        'message': '官方列表仅部分可访问', 'cookie': 'never-return-this'})
    coverage = service.candidate_coverage('luogu-47')
    rows = {row['platform']: row for row in coverage['platforms']}
    assert coverage['eligible_count'] == 0
    counts = {c['code']: c['count'] for c in rows['luogu']['conditions']}
    assert counts == {'unknown_date': 1, 'before_date': 1}
    counts = {c['code']: c['count'] for c in rows['codeforces']['conditions']}
    assert counts == {'difficulty': 1, 'completed_or_reserved': 1}
    assert rows['luogu']['discovery'] == {'status': 'partial', 'count': 1, 'message': '官方列表仅部分可访问'}
    assert 'never-return-this' not in json.dumps(coverage)
    assert db.get('training')['published_after'] == '2024-01-01'
    for row in coverage['platforms']:
        assert row['eligible_count'] + sum(c['count'] for c in row['conditions']) == row['cached_count']


def test_no_evidence_never_becomes_template_or_paid_analysis_and_old_plan_stays(tmp_path, monkeypatch):
    db, ai = Database(tmp_path / 'safe.db'), AsyncMock()
    service = with_selection_reports(Training(db, ai, accounts('codeforces', document=True)))
    save(db, 'codeforces', '2000-A', 'Task', tags=['dp'], difficulty=1600)
    with db.connect() as con:
        con.execute('INSERT INTO training_plans VALUES(?,?,?,?)', ('old-plan', 'luogu-444', 1,
            json.dumps({'id': 'old-plan', 'items': [{'problem_id': 'codeforces:old'}]})))
    monkeypatch.setattr('acmweb.training.refresh_specialist_candidates', lambda *args: [
        {'platform': 'codeforces', 'status': 'failed', 'message': '平台暂时不可访问'}])
    with pytest.raises(AIError, match='模板'):
        asyncio.run(service.select_items('luogu-444', 'learn', 1, 'api', 'mock'))
    ai.complete.assert_not_called()
    assert service.get_plan('old-plan')['items'][0]['problem_id'] == 'codeforces:old'
    assert not is_template({'platform': 'codeforces', 'title': '[Template] DP', 'tags': ['dp']}, TOPICS['luogu-444'])


def test_coverage_legacy_date_guard_matches_generation_without_mutating_preferences(tmp_path):
    db = Database(tmp_path / 'legacy.db')
    db.set('training', {'min_contest': 1800})
    service = Training(db, AsyncMock(), accounts('codeforces'))
    save(db, 'codeforces', '2000-A', 'Task', tags=['dsu'], difficulty=1600)
    result = service.candidate_coverage('luogu-47')
    assert result['eligible_count'] == 0
    row = next(row for row in result['platforms'] if row['platform'] == 'codeforces')
    assert row['conditions'][0]['code'] == 'legacy_date'
    assert '真实的最早发布日期' in row['conditions'][0]['message']
    with pytest.raises(AIError, match='最早发布日期'):
        service._pool(TOPICS['luogu-47'])
    assert db.get('training') == {'min_contest': 1800}

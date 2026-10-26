"""Synthetic discovery regressions. No live platform/OJ success is asserted."""
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from acmweb import catalog
from acmweb.database import Database
from acmweb.training_topics import TOPICS


def accounts(*names):
    return SimpleNamespace(credential_checks=SimpleNamespace(statuses=lambda: {'items': [
        {'platform': platform, 'status': 'authenticated', 'login_verified': True} for platform in names]}))


def envelope(rows, count, per_page=50):
    return json.dumps({'data': {'problems': {'result': rows, 'count': count, 'perPage': per_page}}})


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / 'coverage.sqlite3')


def stub_sources(monkeypatch, discover):
    module = ModuleType('acmweb.expanded_topic_sources')
    module.discover_atcoder = lambda client, topic: discover('atcoder', client, topic)
    module.discover_nowcoder = lambda client, topic: discover('nowcoder', client, topic)
    monkeypatch.setitem(sys.modules, 'acmweb.expanded_topic_sources', module)


def test_cf_auto_discovery_bootstraps_empty_catalog_and_reports_skipped(db, monkeypatch):
    created, called, progress = [], [], []
    class Public:
        def json(self, url, **kwargs):
            called.append(url)
            if url.endswith('problemset.problems'):
                return {'status': 'OK', 'result': {'problems': [{'contestId': 2000, 'index': 'A',
                    'name': 'DSU', 'rating': 1200, 'tags': ['dsu']}]}}
            return {'status': 'OK', 'result': [{'id': 2000, 'phase': 'FINISHED', 'startTimeSeconds': 1735689600}]}
        def close(self): pass
    def factory(**kwargs):
        created.append(kwargs)
        return Public()
    monkeypatch.setattr(catalog, 'Client', factory)
    result = catalog.refresh_specialist_candidates(db, accounts('codeforces'), TOPICS['luogu-47'], progress.append)
    assert len(called) == 2 and 'cookie' not in created[0]
    assert result[0]['platform'] == 'codeforces' and result[0]['status'] == 'updated'
    assert result[0]['truncated'] is False
    assert len([row for row in result if row['status'] == 'skipped']) == 3
    assert catalog._pool(db, 'codeforces')[0]['tags'] == ['dsu']
    assert progress[-1]['completed_platforms'] == progress[-1]['total_platforms'] == 4
    assert progress[-1]['count'] == 1
    # A new topic reuses the recently fetched complete official metadata.
    result = catalog.refresh_specialist_candidates(db, accounts('codeforces'), TOPICS['luogu-7'])
    assert len(called) == 2 and result[0]['status'] == 'updated'


def test_all_verified_platform_sources_are_merged_without_credentials_or_invented_dates(db, monkeypatch):
    seen, options = [], []
    class Public:
        def close(self): pass
        def json(self, *args, **kwargs): return {}
        def get(self, url, params=None):
            seen.append('luogu')
            return envelope([{'pid': 'P3367', 'name': '【模板】并查集', 'tags': [47]}], 1)
    monkeypatch.setattr(catalog, 'Client', lambda **kw: options.append(kw) or Public())
    monkeypatch.setattr(catalog, '_cf', lambda client: seen.append('codeforces') or [catalog._problem(
        'codeforces', '2000-A', 'DSU', tags=['dsu'], difficulty=1200)])
    def discover(platform, client, topic):
        seen.append(platform)
        key = 'practice2_a' if platform == 'atcoder' else '1000'
        return {'items': [catalog._problem(platform, key, 'Disjoint Set Union')],
                'coverage': 'bounded fixture source', 'truncated': True, 'warnings': []}
    stub_sources(monkeypatch, discover)
    result = catalog.refresh_specialist_candidates(db, accounts(*catalog.PLATFORMS), TOPICS['luogu-47'])
    assert seen[0] == 'codeforces'  # A slow supplemental source cannot preempt CF.
    assert {row['platform'] for row in result if row['status'] == 'updated'} == set(catalog.PLATFORMS)
    assert all('cookie' not in kwargs and 'cookie_host' not in kwargs for kwargs in options)
    assert all(len(catalog._pool(db, name)) == 1 for name in catalog.PLATFORMS)
    assert all(catalog._pool(db, name)[0]['published_at'] is None for name in catalog.PLATFORMS)


def test_luogu_uses_actual_page_size_and_later_page_topic_metadata():
    requested = []
    class Public:
        def get(self, url, params):
            requested.append(params['page'])
            return envelope([{'pid': f"P{1000 + params['page']}", 'name': '并查集', 'tags': [47]}], 3, 1)
    found = catalog._luogu_page_batch(Public(), {'tag': '47'}, max_pages=4)
    assert requested == [1, 2, 3]
    assert len(found['items']) == 3 and not found['truncated']
    assert found['next_page'] == 1


def test_luogu_broad_topic_rotates_all_descendants_and_continues_bounded_pages(db):
    topic = {'id': 'fixture-wide-topic', 'title': '数据结构', 'luogu_tags': set(range(1, 15))}
    requested = []
    class Public:
        def get(self, url, params):
            requested.append(dict(params))
            return envelope([{'pid': f"P{int(params['tag']) * 1000 + params['page']}", 'name': '模板练习'}], 1000, 1)
    first = catalog._discover_luogu_topic(db, Public(), topic)
    second = catalog._discover_luogu_topic(db, Public(), topic)
    third = catalog._discover_luogu_topic(db, Public(), topic)
    assert all(row['truncated'] for row in (first, second, third))
    assert {int(params['tag']) for params in requested} == set(range(1, 15))
    assert len(requested) <= 54
    # The fourth refresh returns to old tags and continues their pages.
    catalog._discover_luogu_topic(db, Public(), topic)
    assert any(params['page'] > 2 for params in requested)


def test_partial_luogu_failure_keeps_first_page_and_uses_safe_diagnostics(db, monkeypatch):
    calls = []
    class Public:
        def get(self, url, params):
            calls.append(params)
            if len(calls) > 1: raise catalog.CrawlError('HTTP 403 Cookie=PRIVATE-SECRET')
            return envelope([{'pid': 'P3367', 'name': '【模板】并查集', 'tags': [47]}], 5, 1)
        def close(self): pass
    monkeypatch.setattr(catalog, 'Client', lambda **kwargs: Public())
    result = catalog.refresh_specialist_candidates(db, accounts('luogu'), TOPICS['luogu-47'])
    row = next(row for row in result if row['platform'] == 'luogu')
    assert row['status'] == 'partial' and row['count'] == 1 and row['truncated']
    assert row['error_code'] == 'access_denied'
    assert 'PRIVATE-SECRET' not in json.dumps(result)
    assert len(catalog._pool(db, 'luogu')) == 1
    assert not db.get('specialist_catalog:luogu:luogu-47')


def test_empty_source_is_retryable_without_success_ttl(db, monkeypatch):
    calls = []
    stub_sources(monkeypatch, lambda name, client, topic: calls.append(name) or {
        'items': [], 'coverage': 'no exact upstream mapping', 'truncated': True, 'warnings': []})
    monkeypatch.setattr(catalog, 'Client', lambda **kwargs: SimpleNamespace(close=lambda: None))
    for _ in range(2):
        result = catalog.refresh_specialist_candidates(db, accounts('nowcoder'), TOPICS['luogu-47'])
        assert result[0]['status'] == 'empty'
        assert '不代表平台没有' in result[0]['message']
    assert calls == ['nowcoder', 'nowcoder']
    assert not db.get('specialist_catalog:nowcoder:luogu-47')


def test_catalog_refresh_preserves_topic_rows_and_sparse_official_evidence(db, monkeypatch):
    known = catalog._problem('luogu', 'P3367', '【模板】并查集', tags=['47'],
        published_at=1735689600, published_at_source='luogu.problem.createTime')
    other = catalog._problem('luogu', 'P9999', '专题第三页', tags=['47'])
    catalog._store_catalog_rows(db, 'luogu', [known, other])
    monkeypatch.setattr(catalog, 'Client', lambda **kwargs: SimpleNamespace(close=lambda: None))
    monkeypatch.setitem(catalog.DISCOVERY, 'luogu', lambda client: [catalog._problem('luogu', 'P3367', '更新标题')])
    result = catalog.refresh_catalog(db, 'luogu')
    assert result['complete']
    rows = {row['id']: row for row in catalog._pool(db, 'luogu')}
    assert len(rows) == 2 and rows['luogu:P3367']['title'] == '更新标题'
    assert rows['luogu:P3367']['tags'] == ['47']
    assert rows['luogu:P3367']['published_at'] == 1735689600
    assert rows['luogu:P3367']['published_at_source'] == 'luogu.problem.createTime'


def test_deadline_stops_slow_source_but_reserves_later_platform(db, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(catalog.time, 'monotonic', lambda: clock[0])
    calls = []
    class Public:
        def get(self, *args, **kwargs):
            calls.append('get')
            clock[0] += 40
            return ''
        def close(self): pass
    monkeypatch.setattr(catalog, 'Client', lambda **kwargs: Public())
    def discover(platform, client, topic):
        if platform == 'atcoder':
            for _ in range(5): client.get('https://atcoder.jp/contests/archive')
        calls.append(platform)
        return {'items': [catalog._problem(platform, '1000', 'Task')],
                'coverage': 'fixture', 'truncated': True, 'warnings': []}
    stub_sources(monkeypatch, discover)
    result = catalog.refresh_specialist_candidates(db, accounts('atcoder', 'nowcoder'), TOPICS['luogu-47'])
    assert result[0]['status'] == 'failed' and result[0]['error_code'] == 'timeout'
    assert result[1]['status'] == 'updated' and result[1]['platform'] == 'nowcoder'
    assert calls == ['get', 'get', 'nowcoder']


def test_legacy_empty_success_marker_does_not_block_explicit_retry(db, monkeypatch):
    db.set('specialist_catalog:nowcoder:luogu-47', int(catalog.time.time()))
    calls = []
    stub_sources(monkeypatch, lambda name, client, topic: calls.append(name) or {
        'items': [catalog._problem('nowcoder', '1000', '并查集')],
        'coverage': 'official tag fixture', 'truncated': True, 'warnings': []})
    monkeypatch.setattr(catalog, 'Client', lambda **kwargs: SimpleNamespace(close=lambda: None))
    assert catalog.refresh_specialist_candidates(db, accounts('nowcoder'), TOPICS['luogu-47'])[0]['status'] == 'updated'
    cached = catalog.refresh_specialist_candidates(db, accounts('nowcoder'), TOPICS['luogu-47'])[0]
    assert cached['status'] == 'cached' and cached['coverage'] == 'official tag fixture'
    assert calls == ['nowcoder']

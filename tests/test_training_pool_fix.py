"""Local synthetic regressions; these do not constitute live OJ acceptance."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from acmweb import catalog
from selection_report_fixtures import with_selection_reports

from acmweb.ai import AIResult
from acmweb.codex_bridge import AIError
from acmweb.database import Database
from acmweb.training import Training
from acmweb.training_topics import TOPICS, is_template, matches_topic


def accounts(*platforms, document=False):
    result = SimpleNamespace(credential_checks=SimpleNamespace(statuses=lambda: {'items': [
        {'platform': p, 'status': 'authenticated', 'login_verified': True} for p in platforms]}))
    if document:
        result.document = lambda: {'accounts': [{'platform': p, 'handle': 'local-fixture'} for p in platforms]}
    return result


def save(db, platform, key, title, **metadata):
    row = catalog._problem(platform, key, title, **metadata)
    with db.connect() as con:
        con.execute('INSERT INTO problem_catalog VALUES(?,?,?)', (platform, row['id'], json.dumps(row)))
    return row


def envelope(rows, total=None):
    return json.dumps({'data': {'problems': {'result': rows, 'count': len(rows) if total is None else total, 'perPage': 50}}})


def choosing_ai():
    ai = AsyncMock()
    async def choose(task, provider, model, payload, schema):
        templates = set(payload['template_candidate_ids'])
        selected = ([p for p in payload['candidates'] if p['id'] in templates][:1]
                    + [p for p in payload['candidates'] if p['id'] not in templates])
        return AIResult({'selected_problems': [{'id': p['id'], 'reason': '官方算法练习', 'learning_level': i + 1}
            for i, p in enumerate(selected[:payload['requested_count']])]}, {'model': model})
    ai.complete.side_effect = choose
    return ai


def test_current_luogu_name_schema_keeps_template_evidence_and_old_title_works():
    rows = catalog.parse_luogu_catalog(envelope([
        {'pid': 'P3375', 'name': '【模板】KMP', 'tags': [2, 302], 'difficulty': 4},
        {'pid': 'P1001', 'title': 'A+B', 'tags': []},
    ]))
    assert rows[0]['title'] == '【模板】KMP'
    assert is_template(rows[0], TOPICS['luogu-302'])
    assert rows[1]['title'] == 'A+B'
    assert rows[0]['published_at'] is None  # Never manufacture a publication date.


def test_catalog_refresh_reads_more_than_first_template_page():
    requested = []
    class PublicClient:
        def get(self, url, params):
            requested.append(dict(params))
            if not params.get('keyword'):
                return envelope([{'pid': 'P1001', 'name': 'A+B'}])
            if params['page'] == 1:
                return envelope([{'pid': f'P{2000 + i}', 'name': '【模板】字符串', 'tags': [2]} for i in range(50)], total=51)
            return envelope([{'pid': 'P9999', 'name': '【模板】并查集', 'tags': [47]}], total=51)
    rows = catalog._luogu(PublicClient())
    assert len(rows) == 52
    assert any(p['id'] == 'luogu:P9999' for p in rows)
    assert [p['page'] for p in requested if p.get('keyword')] == [1, 2]


def test_atcoder_library_template_uses_actual_contest_time_and_topic():
    html = '''<div class="contest-duration"><span class="fixtime-full">2020-09-07 22:31:51+0900</span></div>
      <table><tbody><tr><td><a href="/contests/practice2/tasks/practice2_g">G</a></td>
      <td><a href="/contests/practice2/tasks/practice2_g">SCC</a></td></tr></tbody></table>'''
    client = SimpleNamespace(get=lambda *args, **kwargs: html)
    row = catalog._atcoder_practice(client)[0]
    assert row['published_at'] == 1599485511
    assert row['published_at_source'] in catalog.PUBLISHED_SOURCES
    assert is_template(row, TOPICS['luogu-179'])
    assert not is_template(row, TOPICS['luogu-47'])


def test_external_platform_exact_text_tags_are_usable_but_numeric_tags_are_not():
    topic = TOPICS['luogu-47']
    assert matches_topic({'platform': 'atcoder', 'title': 'Disjoint Set Union', 'tags': []}, topic)
    assert matches_topic({'platform': 'nowcoder', 'title': '连通性', 'tags': ['并查集']}, topic)
    assert not matches_topic({'platform': 'nowcoder', 'title': 'Random Task', 'tags': [47, '47', 'data structures']}, topic)
    assert not matches_topic({'platform': 'atcoder', 'title': 'Fenwick Tree', 'tags': []}, topic)
    assert not matches_topic({'platform': 'nowcoder', 'title': 'DP Challenge', 'tags': ['dp']}, TOPICS['luogu-444'])


def test_specialist_shortlist_contains_all_of_this_users_verified_platforms(tmp_path):
    db, ai = Database(tmp_path / 'mine.sqlite3'), choosing_ai()
    service = with_selection_reports(Training(db, ai, accounts(*catalog.PLATFORMS)))
    save(db, 'luogu', 'P3367', '【模板】并查集', tags=[47])
    save(db, 'atcoder', 'practice2_a', 'Disjoint Set Union')
    save(db, 'codeforces', '2000-A', 'CF Task', difficulty=1800, tags=['dsu'])
    save(db, 'nowcoder', '1000', '并查集连通性', tags=['并查集'])
    asyncio.run(service.select_items('luogu-47', 'learn', 3, 'api', 'mock'))
    payload = ai.complete.call_args.args[3]
    assert {p['platform'] for p in payload['candidates']} == set(catalog.PLATFORMS)
    # A platform without this user's verified session is never borrowed from
    # another user's public/saved metadata, even when its candidates exist.
    service.accounts = accounts('codeforces')
    asyncio.run(service.select_items('luogu-47', 'consolidate', 1, 'api', 'mock'))
    assert {p['platform'] for p in ai.complete.call_args.args[3]['candidates']} == {'codeforces'}


def test_attempted_wa_remains_trainable_while_official_ac_is_excluded(tmp_path):
    db = Database(tmp_path / 'retry.sqlite3')
    service = with_selection_reports(Training(db, choosing_ai(), accounts('luogu')))
    save(db, 'luogu', 'P3367', '【模板】并查集', tags=[47])
    save(db, 'luogu', 'P9999', '并查集练习', tags=[47])
    with db.connect() as con:
        con.executemany('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)', [
            ('luogu', 'mine', '1', 'P3367', 1, 'WA', '{}', 1),
            ('luogu', 'mine', '2', 'P9999', 1, 'AC', '{}', 1)])
    assert [p['id'] for p in service._pool(TOPICS['luogu-47'])] == ['luogu:P3367']
    assert catalog.recommendation_candidates(db, service.accounts) == []  # General recommendation policy unchanged.


def test_topic_and_template_priority_applied_before_random_cap(tmp_path, monkeypatch):
    db = Database(tmp_path / 'large.sqlite3')
    service = with_selection_reports(Training(db, choosing_ai(), accounts('luogu')))
    monkeypatch.setattr(catalog.secrets, 'SystemRandom', lambda: SimpleNamespace(shuffle=lambda rows: None))
    # A non-hardcoded template can be first in storage and beyond the end of a
    # random 3000-item pool; topic filtering and template priority must precede it.
    save(db, 'luogu', 'P99999', '【模板】并查集扩展', tags=[47])
    with db.connect() as con:
        rows = [catalog._problem('luogu', f'P{10000 + i}', '普通题', tags=[47 if i % 2 else 3]) for i in range(6100)]
        con.executemany('INSERT INTO problem_catalog VALUES(?,?,?)', [('luogu', p['id'], json.dumps(p)) for p in rows])
    pool = service._pool(TOPICS['luogu-47'])
    assert len(pool) == 3000
    assert all(matches_topic(p, TOPICS['luogu-47']) for p in pool)
    assert pool[0]['id'] == 'luogu:P99999'


def test_explicit_generation_fetches_topic_metadata_and_keeps_other_catalog(tmp_path, monkeypatch):
    db, ai = Database(tmp_path / 'discover.sqlite3'), choosing_ai()
    service = with_selection_reports(Training(db, ai, accounts('luogu', document=True)))
    save(db, 'luogu', 'P1001', 'A+B')
    requested = []
    class PublicClient:
        def get(self, url, params):
            requested.append((url, dict(params)))
            return envelope([{'pid': 'P3367', 'name': '【模板】并查集', 'tags': [47]},
                             {'pid': 'P9999', 'name': '连通性', 'tags': [47]}])
        def close(self): pass
    monkeypatch.setattr(catalog, 'Client', lambda **kwargs: PublicClient())
    items = asyncio.run(service.select_items('luogu-47', 'learn', 2, 'api', 'mock'))
    assert items[0]['problem_id'] == 'luogu:P3367'
    assert len(items) == 2 and len(requested) == 2
    assert all(p['tag'] == '47' for _, p in requested)
    assert any(p['id'] == 'luogu:P1001' for p in catalog._pool(db, 'luogu'))
    # A restored plan/GET never triggers network or AI.
    before = ai.complete.call_count
    service.plans()
    assert ai.complete.call_count == before and len(requested) == 2


def test_date_failure_names_exact_constraint_and_never_calls_ai(tmp_path):
    db, ai = Database(tmp_path / 'date.sqlite3'), choosing_ai()
    service = with_selection_reports(Training(db, ai, accounts('luogu', 'atcoder')))
    save(db, 'luogu', 'P3367', '【模板】并查集', tags=[47])
    save(db, 'atcoder', 'practice2_a', 'Disjoint Set Union',
         published_at=1599485511, published_at_source='atcoder.contest.startTime')
    db.set('training', {'published_after': '2022-10-14', 'minimum': 1600, 'maximum': 2000})
    with pytest.raises(AIError) as error:
        asyncio.run(service.select_items('luogu-47', 'learn', 1, 'api', 'mock'))
    text = str(error.value)
    assert '洛谷：1 道模板缺少可靠发布日期' in text
    assert 'AtCoder：1 道模板早于 2022-10-14' in text
    assert '不会跳过日期限制' in text and '普通题替代' in text
    ai.complete.assert_not_called()
    assert db.get('training')['published_after'] == '2022-10-14'


def test_discovery_failure_is_safe_and_does_not_erase_old_metadata(tmp_path, monkeypatch):
    db = Database(tmp_path / 'safe.sqlite3')
    save(db, 'luogu', 'P3367', '【模板】并查集', tags=[47])
    class Denied:
        def get(self, *args, **kwargs): raise catalog.CrawlError('raw-upstream-secret HTTP 403')
        def close(self): pass
    monkeypatch.setattr(catalog, 'Client', lambda **kwargs: Denied())
    result = catalog.refresh_specialist_candidates(db, accounts('luogu'), TOPICS['luogu-47'])
    assert result[0]['status'] == 'failed'
    assert 'raw-upstream-secret' not in json.dumps(result)
    assert catalog._pool(db, 'luogu')[0]['id'] == 'luogu:P3367'


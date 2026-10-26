"""Synthetic offline regressions for bounded AtCoder/Nowcoder discovery.

These feed hand-written public-page shapes into the discovery functions. They
are not live online-judge acceptance and do not claim any real submission.
"""
import json

import pytest

from acmweb import expanded_topic_sources as sources
from ojcrawler.models import CrawlError


class FakeClient:
    """First matching (url, params subset) route wins; responses are synthetic."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None):
        params = dict(params or {})
        self.calls.append((url, params))
        for (route_url, subset), value in self.routes:
            if route_url == url and all(params.get(k) == v for k, v in subset.items()):
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f'unexpected public request: {url} {params}')


def problems(*rows):
    return json.dumps([
        {'id': pid, 'contest_id': contest,
         'name': title, 'title': f'A. {title}' if title else ''}
        for pid, contest, title in rows
    ])


def archive_page(*rows):
    body = ''.join(
        f'<tr><td><a href="http://www.timeanddate.com/..."><time class="fixtime fixtime-full">'
        f'{stamp}</time></a></td><td><a href="/contests/{contest}">Contest</a></td></tr>'
        for contest, stamp in rows)
    return f'<html><body><table><tbody>{body}</tbody></table></body></html>'


def tag_id(name):
    return str(2000 + sum(ord(char) for char in name))


def nowcoder_row(key, title, difficulty='800', tags=('并查集',), href=None):
    links = ''.join(
        f'<a href="javascript:void(0);" class="tag-label js-tag" data-id="{tag_id(name)}">{name}</a>'
        for name in tags)
    return (f'<tr data-problemId="{key}"><td><a href="/acm/problem/{key}">NC{key}</a></td>'
            f'<td class="fn-right" colspan="2"><a href="{href or "/acm/problem/" + key}" '
            f'class="title">{title}</a>{links}</td><td>{difficulty}</td><td>1</td><td></td></tr>')


def nowcoder_page(rows, total=None):
    marker = f'<div class="pagination">共 {total} 条</div>' if total is not None else ''
    return f'<html><body><table><tbody>{"".join(rows)}</tbody></table>{marker}</body></html>'


def atcoder_routes(problem_rows, contests, models=None, archive=None):
    routes = [
        ((sources.KENKOO_PROBLEMS, {}), problems(*problem_rows)),
        ((sources.KENKOO_CONTESTS, {}), json.dumps(contests)),
        ((sources.KENKOO_MODELS, {}), json.dumps(models or {})),
    ]
    archive = dict(archive or {})
    for page in range(1, sources.MAX_ARCHIVE_PAGES + 1):
        routes.append(((sources.ATCODER_ARCHIVE, {'page': page}),
                       archive.get(page, '<html></html>')))
    return routes


def test_atcoder_full_public_index_keeps_honest_provenance():
    client = FakeClient(atcoder_routes(
        [('abc001_1', 'abc001', 'A+B'), ('abc999_a', 'abc999', 'Recent Task'),
         ('practice2_a', 'practice2', 'Disjoint Set Union'),
         ('math_and_algorithm_a', 'math-and-algorithm', 'Print 5+N'),
         ('APG4b_a', 'APG4b', 'Unsupported case id')],
        [{'id': 'abc001', 'start_epoch_second': 1381579200},
         {'id': 'abc999', 'start_epoch_second': 1700000000},
         {'id': 'practice2', 'start_epoch_second': 0},
         {'id': 'math-and-algorithm', 'start_epoch_second': 0}],
        {'abc001_1': {'difficulty': 100, 'is_experimental': False},
         'abc999_a': {'difficulty': 800, 'is_experimental': False},
         'practice2_a': {'difficulty': 1200, 'is_experimental': False},
         'APG4b_a': {'difficulty': 50, 'is_experimental': False}},
        {1: archive_page(('abc999', '2025-10-09 21:00:00+0900'))}))
    result = sources.discover_atcoder(client, topic={'id': 'luogu-47', 'title': '并查集'})

    items = {item['id']: item for item in result['items']}
    assert set(items) == {'atcoder:abc001_1', 'atcoder:abc999_a',
                          'atcoder:practice2_a', 'atcoder:math_and_algorithm_a'}
    assert items['atcoder:abc001_1']['published_at'] == 1381579200
    assert items['atcoder:abc001_1']['published_at_source'] == 'kenkoooo.contests.start_epoch_second'
    # The bounded official archive wins over the third-party contest start.
    assert items['atcoder:abc999_a']['published_at'] == 1760011200
    assert items['atcoder:abc999_a']['published_at_source'] == 'atcoder.archive.time'
    # No official date and no guessing for editorial/practice collections.
    assert items['atcoder:practice2_a']['published_at'] is None
    assert items['atcoder:practice2_a']['published_at_source'] is None
    # URL follows the official contest id, not the task-id prefix.
    assert items['atcoder:math_and_algorithm_a']['url'] == (
        'https://atcoder.jp/contests/math-and-algorithm/tasks/math_and_algorithm_a')
    for item in items.values():
        assert item['platform'] == 'atcoder'
        assert item['difficulty'] is None and item['native_difficulty'] is None
        assert item['tags'] == []  # AtCoder publishes no per-task tags.
        assert 'Kenkoooo' in item['candidate_source']
        assert item['url'].startswith('https://atcoder.jp/contests/')
    assert items['atcoder:abc999_a']['atcoder_problem_difficulty'] == 800
    assert items['atcoder:abc999_a']['atcoder_statistics_source'] == sources.KENKOO_MODELS
    assert 0 <= items['atcoder:abc999_a']['atcoder_difficulty_percentile'] <= 1
    assert 'Kenkoooo' in items['atcoder:abc999_a']['difficulty_source']
    assert any('第三方比赛索引' in warning for warning in result['warnings'])
    assert any('跳过' in warning for warning in result['warnings'])
    assert result['truncated'] is False
    assert '官方未提供标签/主题检索' in result['coverage']
    assert 'APG4b' not in json.dumps(result, ensure_ascii=False)


def test_atcoder_archive_and_contest_failures_retain_candidates():
    """A broken date/difficulty source must not discard the public index."""
    client = FakeClient([
        ((sources.KENKOO_PROBLEMS, {}), problems(('abc001_1', 'abc001', 'A+B'),
                                                 ('abc999_a', 'abc999', 'Recent'))),
        ((sources.KENKOO_CONTESTS, {}), CrawlError('HTTP 403 raw-upstream-secret')),
        ((sources.KENKOO_MODELS, {}), CrawlError('secret html <body>')),
        ((sources.ATCODER_ARCHIVE, {'page': 1}), CrawlError('HTTP 429 raw-secret')),
    ])
    result = sources.discover_atcoder(client)
    assert {item['id'] for item in result['items']} == {'atcoder:abc001_1', 'atcoder:abc999_a'}
    assert all(item['published_at'] is None for item in result['items'])
    assert all('atcoder_problem_difficulty' not in item for item in result['items'])
    serialized = json.dumps(result, ensure_ascii=False)
    assert 'raw-upstream-secret' not in serialized and 'raw-secret' not in serialized
    assert any('比赛索引获取失败' in warning for warning in result['warnings'])
    assert any('难度估计获取失败' in warning for warning in result['warnings'])
    assert any('官方比赛归档' in warning for warning in result['warnings'])
    # Bounded: at most three archive page requests, never unbounded crawling.
    archive_calls = [call for call in client.calls if call[0] == sources.ATCODER_ARCHIVE]
    assert 1 <= len(archive_calls) <= sources.MAX_ARCHIVE_PAGES


def test_atcoder_index_failure_is_safe_and_empty():
    client = FakeClient([((sources.KENKOO_PROBLEMS, {}), '<html>login challenge raw-secret</html>')])
    result = sources.discover_atcoder(client)
    assert result['items'] == [] and result['truncated'] is False
    assert '索引不可用' in result['coverage']
    assert 'raw-secret' not in json.dumps(result, ensure_ascii=False)

    denied = FakeClient([((sources.KENKOO_PROBLEMS, {}), CrawlError('HTTP 403 raw-secret'))])
    result = sources.discover_atcoder(denied)
    assert result['items'] == [] and 'raw-secret' not in json.dumps(result, ensure_ascii=False)


def test_atcoder_rejects_suspicious_rows_and_oversized_response(monkeypatch):
    unsafe = problems(('../../etc/passwd', 'abc001', 'Traversal'),
                      ('abc001_1', '../contest', 'Traversal'),
                      ('abc001_2', 'abc001', ''),
                      ('abc001_3', 'abc001', 'Valid'))
    routes = [((sources.KENKOO_PROBLEMS, {}), unsafe)] + [
        route for route in atcoder_routes([], []) if route[0][0] != sources.KENKOO_PROBLEMS]
    client = FakeClient(routes)
    result = sources.discover_atcoder(client, topic={'title': 'x'})
    assert [item['id'] for item in result['items']] == ['atcoder:abc001_3']
    assert all(item['url'].startswith('https://atcoder.jp/contests/abc001/tasks/') for item in result['items'])
    assert any('跳过' in warning for warning in result['warnings'])

    monkeypatch.setattr(sources, 'MAX_JSON_BYTES', 20)
    result = sources.discover_atcoder(FakeClient([((sources.KENKOO_PROBLEMS, {}), problems(
        ('abc001_1', 'abc001', 'A+B')))]))
    assert result['items'] == []
    assert '索引不可用' in result['coverage']


def test_atcoder_row_cap_is_reported_as_truncated(monkeypatch):
    monkeypatch.setattr(sources, 'MAX_ATCODER_ROWS', 2)
    rows = [('abc001_1', 'abc001', 'A'), ('abc001_2', 'abc001', 'B'), ('abc001_3', 'abc001', 'C')]
    client = FakeClient(atcoder_routes(rows, [{'id': 'abc001', 'start_epoch_second': 1381579200}]))
    result = sources.discover_atcoder(client)
    assert len(result['items']) == 2 and result['truncated'] is True
    assert any('截断' in warning for warning in result['warnings'])


def test_nowcoder_pagination_collects_tags_and_never_invents_dates():
    page_one = nowcoder_page([nowcoder_row(str(1000 + i), f'Task {i}',
                                            tags=('并查集', '树状数组') if i % 2 else ())
                              for i in range(50)], total=120)
    page_two = nowcoder_page([nowcoder_row(str(2000 + i), f'Task {i}') for i in range(50)], total=120)
    page_three = nowcoder_page([nowcoder_row(str(3000 + i), f'Task {i}') for i in range(20)], total=120)
    client = FakeClient([
        ((sources.NOWCODER_LIST, {'page': 1}), page_one),
        ((sources.NOWCODER_LIST, {'page': 2}), page_two),
        ((sources.NOWCODER_LIST, {'page': 3}), page_three),
    ])
    result = sources.discover_nowcoder(client)

    assert len(result['items']) == 120 and result['truncated'] is False
    assert [call[1]['page'] for call in client.calls] == [1, 2, 3]
    assert all('keyword' not in call[1] for call in client.calls)
    first = result['items'][0]
    assert first == {
        'id': 'nowcoder:1000', 'platform': 'nowcoder', 'problem_id': '1000',
        'title': 'Task 0', 'url': 'https://ac.nowcoder.com/acm/problem/1000',
        'difficulty': None, 'native_difficulty': 800,
        'difficulty_source': '牛客官方难度（与 CF rating 不可比）',
        'tags': [],
        'candidate_source': '牛客官方题库列表（含官方知识点标签）',
        'published_at': None, 'published_at_source': None,
    }
    assert result['items'][1]['tags'] == ['并查集', '树状数组']
    assert any('未提供逐题发布日期' in warning for warning in result['warnings'])


def test_nowcoder_page_cap_reports_exact_truncation():
    routes = []
    for page in range(1, sources.MAX_NOWCODER_PAGES + 1):
        routes.append(((sources.NOWCODER_LIST, {'page': page}),
                       nowcoder_page([nowcoder_row(str(page * 100 + i), f'T{i}')
                                      for i in range(50)], total=1000)))
    client = FakeClient(routes)
    result = sources.discover_nowcoder(client)
    assert len(result['items']) == sources.MAX_NOWCODER_PAGES * 50
    assert result['truncated'] is True
    assert any('共 1000 道' in warning and '400 道' in warning for warning in result['warnings'])
    assert len(client.calls) == sources.MAX_NOWCODER_PAGES


def test_nowcoder_topic_uses_official_keyword_search_and_verified_evidence():
    page = nowcoder_page([
        nowcoder_row('4101', '【模板】并查集', tags=('并查集', '数据结构')),
        nowcoder_row('4102', '普通树状数组题', tags=('树状数组',)),
        nowcoder_row('4103', '并查集连通块', tags=()),
    ])
    filtered = nowcoder_page([
        nowcoder_row('4101', '【模板】并查集', tags=('并查集', '数据结构')),
        nowcoder_row('4200', '并查集扩展练习', tags=('并查集',)),
    ], total=330)
    client = FakeClient([
        ((sources.NOWCODER_LIST, {'keyword': '并查集', 'page': 1}), page),
        ((sources.NOWCODER_LIST, {'keyword': 'disjoint set', 'page': 1}),
         nowcoder_page([])),
        ((sources.NOWCODER_LIST, {'tagId': tag_id('并查集'), 'page': 1}), filtered),
    ])
    topic = {'id': 'luogu-47', 'title': '并查集', 'text_keywords': ['并查集', 'disjoint set'],
             'keywords': ['并查集']}
    result = sources.discover_nowcoder(client, topic=topic)

    assert {item['id'] for item in result['items']} == {
        'nowcoder:4101', 'nowcoder:4103', 'nowcoder:4200'}
    keywords = {call[1].get('keyword') for call in client.calls if 'keyword' in call[1]}
    assert keywords == {'并查集', 'disjoint set'}
    # The official tag ID is taken from the published tag label, not guessed.
    assert any(call[1].get('tagId') == tag_id('并查集') for call in client.calls)
    assert not any(call[1].get('tagId') == tag_id('树状数组') for call in client.calls)
    assert all('tagId' not in call[1] or call[1]['tagId'] == tag_id('并查集')
               for call in client.calls)
    assert len(client.calls) <= (sources.MAX_TOPIC_QUERIES * sources.MAX_TOPIC_KEYWORD_PAGES
                                 + sources.MAX_TOPIC_TAGS * sources.MAX_TOPIC_TAG_PAGES)
    assert result['items'][0]['tags'] == ['并查集', '数据结构']
    assert result['truncated'] is False
    assert '官方标签筛选 1 个' in result['coverage']
    assert '保留 3 道' in result['coverage']


def test_nowcoder_topic_never_guesses_tag_ids_without_official_evidence():
    page = nowcoder_page([nowcoder_row('4300', '并查集基础', tags=())])
    client = FakeClient([
        ((sources.NOWCODER_LIST, {'keyword': '并查集', 'page': 1}), page),
        ((sources.NOWCODER_LIST, {'keyword': 'disjoint set', 'page': 1}), nowcoder_page([])),
    ])
    result = sources.discover_nowcoder(client, topic={
        'title': '并查集', 'text_keywords': ['并查集', 'disjoint set']})
    assert [item['id'] for item in result['items']] == ['nowcoder:4300']
    assert all('tagId' not in call[1] for call in client.calls)
    assert '官方标签筛选 0 个' in result['coverage']


def test_nowcoder_topic_tag_filter_truncation_is_explicit():
    keyword_page = nowcoder_page([nowcoder_row('5001', '并查集总览', tags=('并查集',))])
    routes = [
        ((sources.NOWCODER_LIST, {'keyword': '并查集', 'page': 1}), keyword_page),
        ((sources.NOWCODER_LIST, {'keyword': 'disjoint set', 'page': 1}), nowcoder_page([])),
    ]
    for page in range(1, sources.MAX_TOPIC_TAG_PAGES + 1):
        routes.append(((sources.NOWCODER_LIST,
                        {'tagId': tag_id('并查集'), 'page': page}),
                       nowcoder_page([nowcoder_row(str(9000 + page * 100 + i), f'并查集 {i}')
                                      for i in range(50)], total=330)))
    result = sources.discover_nowcoder(FakeClient(routes), topic={
        'title': '并查集', 'text_keywords': ['并查集', 'disjoint set']})
    assert len(result['items']) == sources.MAX_TOPIC_TAG_PAGES * 50 + 1
    assert result['truncated'] is True
    assert any('共 330 道' in warning and '151 道' in warning
               for warning in result['warnings'])
    assert '未声称全量扫描' in json.dumps(result['warnings'], ensure_ascii=False)


def test_nowcoder_partial_page_failure_keeps_earlier_candidates():
    page_one = nowcoder_page([nowcoder_row(str(5000 + i), f'Task {i}') for i in range(50)], total=200)
    client = FakeClient([
        ((sources.NOWCODER_LIST, {'page': 1}), page_one),
        ((sources.NOWCODER_LIST, {'page': 2}), CrawlError('HTTP 403 raw-secret <html>')),
    ])
    result = sources.discover_nowcoder(client)
    assert len(result['items']) == 50
    assert result['truncated'] is False
    serialized = json.dumps(result, ensure_ascii=False)
    assert 'raw-secret' not in serialized and '<html>' not in serialized
    assert any('第 2 页请求失败' in warning for warning in result['warnings'])


def test_nowcoder_first_page_failure_and_oversized_html_are_safe(monkeypatch):
    denied = FakeClient([((sources.NOWCODER_LIST, {'page': 1}), CrawlError('HTTP 403 raw-secret'))])
    result = sources.discover_nowcoder(denied)
    assert result['items'] == [] and 'raw-secret' not in json.dumps(result, ensure_ascii=False)

    monkeypatch.setattr(sources, 'MAX_HTML_BYTES', 10)
    huge = FakeClient([((sources.NOWCODER_LIST, {'page': 1}), nowcoder_page([]))])
    result = sources.discover_nowcoder(huge)
    assert result['items'] == []
    assert any('获取失败' in warning for warning in result['warnings'])


def test_nowcoder_rejects_suspicious_rows_and_unsupported_topic():
    page = nowcoder_page([
        nowcoder_row('abc', 'Bad id'),
        nowcoder_row('6001', 'Wrong href', href='/acm/problem/9999'),
        nowcoder_row('6002', 'Good'),
        f'<tr data-problemId="6003"><td><a href="/acm/problem/6003">NC</a></td>'
        f'<td colspan="2"><a href="/acm/problem/6003" class="title">\x00{"x" * 400}</a>'
        f'<a href="javascript:void(0);" class="tag-label js-tag" data-id="1">12345</a></td>'
        f'<td>999999</td><td>1</td><td></td></tr>',
    ])
    client = FakeClient([((sources.NOWCODER_LIST, {'page': 1}), page)])
    result = sources.discover_nowcoder(client)
    assert [item['id'] for item in result['items']] == ['nowcoder:6002', 'nowcoder:6003']
    assert all(item['native_difficulty'] in (None, 800) for item in result['items'])
    assert result['items'][1]['tags'] == []  # Numeric ids are not tag evidence.
    assert len(result['items'][1]['title']) <= 300
    assert any('跳过' in warning for warning in result['warnings'])

    fallback = FakeClient([((sources.NOWCODER_LIST, {'page': 1}),
                            nowcoder_page([nowcoder_row('7001', 'General')], total=1))])
    result = sources.discover_nowcoder(fallback, topic={'id': 'luogu-1', 'title': ''})
    assert [item['id'] for item in result['items']] == ['nowcoder:7001']
    assert all('keyword' not in call[1] for call in fallback.calls)
    assert any('退回牛客通用题库分页' in warning for warning in result['warnings'])


def test_invalid_and_injection_like_topics_are_not_echoed():
    client = FakeClient([
        ((sources.KENKOO_PROBLEMS, {}), problems(('abc001_1', 'abc001', 'A+B'))),
        ((sources.KENKOO_CONTESTS, {}), json.dumps([{'id': 'abc001', 'start_epoch_second': 1381579200}])),
        ((sources.KENKOO_MODELS, {}), '{}'),
        ((sources.ATCODER_ARCHIVE, {'page': 1}), '<html></html>'),
    ])
    for topic in ('not-a-topic', 123):
        result = sources.discover_atcoder(client, topic=topic)
        text = json.dumps(result, ensure_ascii=False)
        assert '<script>' not in text
        assert any('主题参数无效' in warning for warning in result['warnings'])
    result = sources.discover_atcoder(client, topic={'title': '<script>alert(1)</script>'})
    text = json.dumps(result, ensure_ascii=False)
    assert '<script>' not in text and 'alert(1)' not in text
    assert all('主题参数无效' not in warning for warning in result['warnings'])
    # A valid topic never becomes a fabricated AtCoder tag or search parameter.
    result = sources.discover_atcoder(client, topic={'title': '并查集'})
    assert all(item['tags'] == [] for item in result['items'])
    assert all('keyword' not in call[1] for call in client.calls)


def test_all_requests_stay_on_allowlisted_public_origins():
    client = FakeClient(atcoder_routes(
        [('abc001_1', 'abc001', 'A+B')],
        [{'id': 'abc001', 'start_epoch_second': 1381579200}],
        {'abc001_1': {'difficulty': 100}},
        {1: archive_page(('abc001', '2013-10-12 21:00:00+0900'))}) + [
        ((sources.NOWCODER_LIST, {'page': 1}), nowcoder_page([nowcoder_row('1001', 'A')], total=1))])
    sources.discover_atcoder(client, topic={'title': '并查集'})
    sources.discover_nowcoder(client)
    hosts = {call[0].split('/')[2] for call in client.calls}
    assert hosts == {'kenkoooo.com', 'atcoder.jp', 'ac.nowcoder.com'}
    assert all(call[0].startswith('https://') for call in client.calls)
    assert not any(key for _, params in client.calls for key in params if 'cookie' in key.lower())


def test_source_never_raises_for_client_that_cannot_fetch():
    result = sources.discover_atcoder(object())
    assert result['items'] == [] and result['warnings']
    result = sources.discover_nowcoder(object())
    assert result['items'] == [] and result['warnings']

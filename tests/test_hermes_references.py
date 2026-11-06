"""Synthetic HTTP contracts; these tests are not real official-site acceptance."""
import asyncio

import httpx
import pytest

from acmhermes.references import gather


def run(coro):
    return asyncio.run(coro)


def transport(pages, requests):
    def handler(request):
        requests.append(request)
        assert request.method == 'GET'
        assert 'cookie' not in request.headers and 'authorization' not in request.headers
        value = pages.get(str(request.url), '')
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, text=value, headers={'content-type': 'text/html'})
    return httpx.MockTransport(handler)


AC_BASE = 'https://atcoder.jp/contests/abc400'
CF_BASE = 'https://codeforces.com/contest/2000'


def test_atcoder_exact_task_section_and_prefer_official():
    requests = []
    pages = {AC_BASE + '/editorial': '''
      <h3><a href="/contests/abc400/tasks/abc400_b">B</a></h3>
      <ul><li><a href="/contests/abc400/editorial/11">Wrong B</a></li></ul>
      <h3><a href="/contests/abc400/tasks/abc400_a">A</a></h3>
      <ul><li><a href="/contests/abc400/editorial/12">Community</a></li>
      <li>[Official]<a href="/contests/abc400/editorial/13">A approach</a></li></ul>''',
      AC_BASE + '/editorial/13': '<div id="editorial-body">Exact A reasoning<script>secret()</script></div>'}
    result = run(gather('atcoder:abc400_a', {'id': 'atcoder:abc400_a'},
                        transport=transport(pages, requests), include_public_code=False))
    assert len(requests) == 2
    assert result[0]['url'].endswith('/13')
    assert result[0]['excerpt'] == 'Exact A reasoning'
    assert result[0]['problem_id'] == 'atcoder:abc400_a'
    assert result[0]['limitations']


@pytest.mark.parametrize('href', ['https://evil.invalid/x', '//evil.invalid/x',
    'https://atcoder.jp:443/contests/abc400/editorial/13',
    'https://atcoder.jp@evil.invalid/contests/abc400/editorial/13',
    '/contests/abc401/editorial/13', '/contests/abc400/editorial/13?redirect=1'])
def test_atcoder_discovery_rejects_untrusted_destinations(href):
    requests = []
    pages = {AC_BASE + '/editorial': f'<h3><a href="/contests/abc400/tasks/abc400_a">A</a></h3><ul><li><a href="{href}">Official</a></li></ul>'}
    assert run(gather('atcoder:abc400_a', {'id': 'atcoder:abc400_a'},
        transport=transport(pages, requests), include_public_code=False)) == []
    assert len(requests) == 1


def test_cf_only_extracts_link_verified_exact_problem_section():
    requests = []
    pages = {CF_BASE: '<a href="/blog/entry/101">Tutorial</a>',
      'https://codeforces.com/blog/entry/101': '''<div class="ttypography">
      <h2><a href="/contest/2000/problem/A">A</a></h2><p>A reasoning</p>
      <h2><a href="/contest/2000/problem/B">B</a></h2><p>Do not import B</p></div>'''}
    result = run(gather('codeforces:2000-A', {'id':'codeforces:2000-A'},
      transport=transport(pages, requests), include_public_code=False))
    assert 'A reasoning' in result[0]['excerpt']
    assert 'Do not import B' not in result[0]['excerpt']
    assert result[0]['kind'] == 'editorial'


def test_cf_missing_problem_evidence_is_skipped():
    requests = []
    pages = {CF_BASE: '<a href="/blog/entry/101">Tutorial</a>',
      'https://codeforces.com/blog/entry/101': '<div class="ttypography"><h2>Generic reasoning</h2>Do not guess</div>'}
    assert run(gather('codeforces:2000-A', {'id':'codeforces:2000-A'},
      transport=transport(pages, requests), include_public_code=False)) == []


@pytest.mark.parametrize('mismatch', [False, True])
def test_atcoder_public_ac_code_verified_on_detail(mismatch):
    requests = []
    path = 'abc400_b' if mismatch else 'abc400_a'
    pages = {AC_BASE + '/submissions?f.Task=abc400_a&f.Status=AC': '''<table><tbody><tr>
      <td><a href="/contests/abc400/tasks/abc400_a">A</a></td><td>AC</td>
      <td><a href="/contests/abc400/submissions/123">Detail</a></td></tr></tbody></table>''',
      AC_BASE + '/submissions/123': f'''<h2>Submission #123</h2><table><tr><th>Task</th><td><a href="/contests/abc400/tasks/{path}">Task</a></td></tr>
      <tr><th>Status</th><td>AC</td></tr></table><pre id="submission-code">int main() {{}}</pre>'''}
    result = run(gather('atcoder:abc400_a', {'id':'atcoder:abc400_a'}, transport=transport(pages, requests)))
    assert len(requests) == 3
    assert len(result) == (0 if mismatch else 1)
    if result:
        assert result[0]['kind'] == 'accepted_public_code'
        assert result[0]['url'].endswith('/123')


def test_cf_source_is_exact_accepted_submission_on_both_pages():
    requests = []
    row = '<tr data-submission-id="987"><td><a href="/contest/2000/problem/A">A</a></td><td submissionverdict="OK">Accepted</td><td><a href="/contest/2000/submission/987">987</a></td></tr>'
    pages = {CF_BASE + '/status?submittedProblemIndex=A&verdictName=OK': '<table>' + row + '</table>',
      CF_BASE + '/submission/987': '<table>' + row + '</table><pre id="program-source-text">int main() {}</pre>'}
    result = run(gather('codeforces:2000-A', {'id':'codeforces:2000-A'}, transport=transport(pages, requests)))
    assert result[0]['kind'] == 'accepted_public_code'
    assert len(requests) <= 4


def test_redirect_is_not_followed_and_statement_url_ignored():
    requests = []
    pages = {AC_BASE + '/editorial': httpx.Response(302, headers={'location': 'https://evil.invalid/'})}
    assert run(gather('atcoder:abc400_a', {'id':'atcoder:abc400_a', 'url':'https://evil.invalid/'},
      transport=transport(pages, requests), include_public_code=False)) == []
    assert len(requests) == 1


def test_statement_id_mismatch_and_unsupported_id_do_no_network():
    requests = []
    tr = transport({}, requests)
    for pid, statement in [('atcoder:abc400_a', {'id':'atcoder:abc400_b'}),
                           ('https://evil.invalid/', {'id':'x'}),
                           ('luogu:P1001', {'id':'luogu:P1001'})]:
        assert run(gather(pid, statement, transport=tr)) == []
    assert not requests


def test_total_excerpt_and_request_budget_are_bounded():
    requests = []
    pages = {AC_BASE + '/editorial': '<h3><a href="/contests/abc400/tasks/abc400_a">A</a></h3><ul><li><a href="/contests/abc400/editorial/13">Official</a></li></ul>',
      AC_BASE + '/editorial/13': '<div id="editorial-body">' + 'a' * 20000 + '</div>'}
    result = run(gather('atcoder:abc400_a', {'id':'atcoder:abc400_a'}, transport=transport(pages, requests)))
    assert sum(len(item['excerpt']) for item in result) == 8000
    assert len(requests) <= 4


def test_network_failure_is_optional():
    def handler(request):
        raise httpx.ConnectError('synthetic failure')
    assert run(gather('atcoder:abc400_a', {'id':'atcoder:abc400_a'}, transport=httpx.MockTransport(handler))) == []


def test_timeout_is_bounded_and_cancellation_propagates():
    async def handler(request):
        await asyncio.sleep(10)
    async def exercise():
        tr = httpx.MockTransport(handler)
        assert await gather('atcoder:abc400_a', {'id':'atcoder:abc400_a'}, transport=tr, timeout=.01) == []
        task = asyncio.create_task(gather('atcoder:abc400_a', {'id':'atcoder:abc400_a'}, transport=tr))
        await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    run(exercise())


def test_success_and_empty_results_have_separate_short_lived_cache(monkeypatch):
    import acmhermes.references as module
    module._CACHE.clear()
    calls, now = [], [100]
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    async def collect(pid, client, include_public_code):
        calls.append(pid)
        if pid.endswith('_a'):
            return [{'problem_id':pid, 'excerpt':'safe public text'}]
        return []
    monkeypatch.setattr(module, '_collect', collect)
    statement = {'id':'atcoder:abc400_a'}
    first = run(gather(statement['id'], statement))
    first[0]['excerpt'] = 'do not mutate cache'
    assert run(gather(statement['id'], statement))[0]['excerpt'] == 'safe public text'
    empty = {'id':'atcoder:abc400_b'}
    assert run(gather(empty['id'], empty)) == []
    assert run(gather(empty['id'], empty)) == []
    assert len(calls) == 2
    now[0] = 131
    assert run(gather(empty['id'], empty)) == []
    assert len(calls) == 3
    assert run(gather(statement['id'], statement))[0]['excerpt'] == 'safe public text'
    now[0] = 401
    run(gather(statement['id'], statement))
    assert len(calls) == 4
    module._CACHE.clear()


def test_oversized_public_document_is_rejected():
    requests = []
    pages = {AC_BASE + '/editorial': 'x' * (1024 * 1024 + 1)}
    assert run(gather('atcoder:abc400_a', {'id':'atcoder:abc400_a'},
        transport=transport(pages, requests), include_public_code=False)) == []


def test_response_cookie_never_reused_for_another_public_get():
    requests = []
    pages = {AC_BASE + '/editorial': httpx.Response(200,
        headers={'content-type':'text/html','set-cookie':'private=never-share; Path=/'},
        text='<h3><a href="/contests/abc400/tasks/abc400_a">A</a></h3><ul><li><a href="/contests/abc400/editorial/13">Official</a></li></ul>'),
        AC_BASE + '/editorial/13': '<div id="editorial-body">Reason</div>'}
    assert run(gather('atcoder:abc400_a', {'id':'atcoder:abc400_a'},
        transport=transport(pages, requests), include_public_code=False))
    assert len(requests) == 2


@pytest.mark.parametrize('heading,verdict', [('Submission #124','AC'), ('','AC'), ('Submission #123','WA')])
def test_public_source_rejects_wrong_receipt_or_failed_verdict(heading, verdict):
    requests = []
    pages = {AC_BASE + '/submissions?f.Task=abc400_a&f.Status=AC': '<table><tbody><tr><td><a href="/contests/abc400/tasks/abc400_a">A</a></td><td>AC</td><td><a href="/contests/abc400/submissions/123">Detail</a></td></tr></tbody></table>',
        AC_BASE + '/submissions/123': f'<h2>{heading}</h2><table><tr><th>Task</th><td><a href="/contests/abc400/tasks/abc400_a">A</a></td></tr><tr><th>Status</th><td>{verdict}</td></tr></table><pre id="submission-code">int main() {{}}</pre>'}
    assert run(gather('atcoder:abc400_a', {'id':'atcoder:abc400_a'}, transport=transport(pages, requests))) == []


def test_cf_nested_next_task_heading_does_not_leak_other_solution():
    requests = []
    pages = {CF_BASE: '<a href="/blog/entry/101">Tutorial</a>',
      'https://codeforces.com/blog/entry/101': '<div class="ttypography"><section><h2><a href="/contest/2000/problem/A">A</a></h2><p>A reasoning</p></section><section><h3><a href="/contest/2000/problem/B">B</a></h3><p>Wrong B reasoning</p></section></div>'}
    result = run(gather('codeforces:2000-A', {'id':'codeforces:2000-A'},
      transport=transport(pages, requests), include_public_code=False))
    assert 'A reasoning' in result[0]['excerpt']
    assert 'Wrong B reasoning' not in result[0]['excerpt']

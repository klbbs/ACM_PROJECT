"""Synthetic official-page markup, never a claim of live CF source retrieval."""
import pytest
from bs4 import BeautifulSoup

from ojcrawler.codeforces_pages import parse_submissions, source_reference
from ojcrawler.models import CrawlError


def page(source, script='', *, problem='/contest/2264/problem/C', extra=''):
    return BeautifulSoup(f'''<table class="status-frame-datatable"><tr data-submission-id="123">
      <td class="id-cell">{source}</td><td><time data-timestamp="1700000000"></time></td>
      <td><a href="/profile/kendas">kendas</a></td><td><a href="{problem}">Problem</a></td>
      <td>GNU C++20</td><td><span submissionverdict="WRONG_ANSWER">Wrong answer</span></td>
    </tr>{extra}</table>{script}''', 'html.parser')


def check(soup, contest='2264', sid='123'):
    return source_reference(soup.select_one('tr[data-submission-id]'), soup, contest, sid)


@pytest.mark.parametrize('source', [
    '<a href="/contest/2264/submission/123">123</a>',
    '<a href="/gym/2264/submission/123">123</a>',
    '<a class="view-source" href="#" submissionId="123">123</a>',
])
def test_exact_link_and_modal_references(source):
    soup = page(source)
    assert check(soup)
    rows, last = parse_submissions(str(soup), 'KENDAS')
    assert last == 1 and rows[0]['id'] == 123
    assert rows[0]['contestId'] == 2264 and rows[0]['problem']['index'] == 'C'
    assert rows[0]['creationTimeSeconds'] == 1700000000


@pytest.mark.parametrize('declaration', [
    'const viewableSubmissionIds = ["123", "456"];',
    'let viewableSubmissionIds = [123, 456];',
    'var viewableSubmissionIds = [\n "123"\n ]; otherFunction();',
])
def test_hidden_source_requires_exact_id_cell_and_json_whitelist(declaration):
    soup = page('<span class="hiddenSource">123</span>', f'<script>{declaration}</script>')
    assert check(soup)
    assert parse_submissions(str(soup), 'kendas')[0][0]['id'] == 123


@pytest.mark.parametrize('script', [
    '',
    '<script src="/static/status.js"></script>',
    '<script>const viewableSubmissionIds = ["999"];</script>',
    '<script>const viewableSubmissionIds = ["123", "123"];</script>',
    '<script>const viewableSubmissionIds = ["123", 123];</script>',
    '<script>const viewableSubmissionIds = [\'123\'];</script>',
    '<script>const viewableSubmissionIds = ["123",];</script>',
    '<script>const viewableSubmissionIds = {"123":true};</script>',
    '<script>const viewableSubmissionIds = [true, "123"];</script>',
    '<script>const viewableSubmissionIds = [123.0];</script>',
    '<script>const viewableSubmissionIds = ["0123"];</script>',
    '<script>const viewableSubmissionIds = ["123"] + ["999"];</script>',
    '<script>const viewableSubmissionIds = ["123"]; viewableSubmissionIds = [];</script>',
    '<script>const viewableSubmissionIds = ["123"]; const viewableSubmissionIds = ["123"];</script>',
    '<script>const viewableSubmissionIds = ["123"];</script><script>var viewableSubmissionIds = [];</script>',
    '<script>/* const viewableSubmissionIds = ["123"]; */</script>',
    '<script>// const viewableSubmissionIds = ["123"];\n</script>',
    '<script>const example = `const viewableSubmissionIds = ["123"];`;</script>',
    '<script type="text/plain">const viewableSubmissionIds = ["123"];</script>',
    '<script>const pattern = /const viewableSubmissionIds = [123];/;</script>',
    '<script>const viewableSubmissionIds = ["123"]; viewableSubmissionIds.length = 0;</script>',
    '<script>const viewableSubmissionIds = ["123"]; viewableSubmissionIds.splice(0);</script>',
    '<script>const viewableSubmissionIds = ["123"]; viewableSubmissionIds[0] = "999";</script>',
    '<script>const viewableSubmissionIds = ["123"]; mutate(viewableSubmissionIds);</script>',
    '<script>const viewableSubmissionIds = ["123"]; const text = `${viewableSubmissionIds.splice(0)}`;</script>',
    '<script>const viewableSubmissionIds = ["123"]; const text = `${identity(`nested`)}`;</script>',
])
def test_unknown_malformed_or_duplicate_whitelist_is_not_evidence(script):
    soup = page('<span class="hiddenSource">123</span>', script)
    assert not check(soup)
    with pytest.raises(CrawlError):
        parse_submissions(str(soup), 'kendas')


@pytest.mark.parametrize('source', [
    '<span class="hiddenSource">999</span>',
    'other text <span class="hiddenSource">123</span>',
    '<div class="hiddenSource">123</div>',
    '<span class="hiddenSource">123</span><span class="hiddenSource">123</span>',
    '<span class="hiddenSource">123</span><a href="/contest/2264/submission/123">123</a>',
    '<a class="view-source" href="#" submissionId="999">123</a>',
    '<a class="view-source" href="#">123</a>',
    '<a class="view-source" href="javascript:void(0)" submissionId="123">123</a>',
    '<a href="/contest/999/submission/123">123</a>',
    '<a href="/contest/2264/submission/999">123</a>',
    '<a href="https://example.test/contest/2264/submission/123">123</a>',
    '<a href="/contest/2264/submission/123?other=999">123</a>',
    '<a href="/contest/2264/submission/123">123</a><a href="/contest/2264/submission/123">123</a>',
    '<a class="view-source" href="/contest/2264/submission/123" submissionId="999">123</a>',
])
def test_incorrect_or_ambiguous_source_entries_fail_closed(source):
    soup = page(source, '<script>const viewableSubmissionIds = ["123"];</script>')
    assert not check(soup)


def test_hidden_source_requires_id_cell_and_current_row_ownership():
    soup = page('<span class="hiddenSource">123</span>', '<script>const viewableSubmissionIds = ["123"];</script>')
    soup.select_one('.id-cell')['class'] = ['other-cell']
    assert not check(soup)
    soup.select_one('.other-cell')['class'] = ['id-cell']
    assert not check(soup, contest='999')
    assert not check(soup, sid='999')
    assert not source_reference(soup.select_one('tr'), BeautifulSoup('', 'html.parser'), '2264', '123')


def test_official_whitelist_membership_reads_remain_supported():
    soup = page('<span class="hiddenSource">123</span>', '''<script>
        const viewableSubmissionIds = ["123"];
        if (viewableSubmissionIds.indexOf(submissionId) != -1) { show = true; }
        if (viewableSubmissionIds.includes(submissionId)) { show = true; }
    </script>''')
    assert check(soup)


def test_multiple_problem_or_receipt_rows_are_ambiguous():
    soup = page('<a href="/contest/2264/submission/123">123</a>')
    row = soup.select_one('tr')
    row.append(BeautifulSoup('<a href="/contest/999/problem/A">Other problem</a>', 'html.parser'))
    assert not check(soup)
    soup = page('<a href="/contest/2264/submission/123">123</a>', extra='<tr data-submission-id="123"></tr>')
    assert not check(soup)


def test_modals_do_not_relax_owner_or_timestamp_checks():
    soup = page('<a class="view-source" href="#" submissionId="123">123</a>')
    with pytest.raises(CrawlError, match='账号不匹配'):
        parse_submissions(str(soup), 'another-user')
    soup.select_one('time').attrs = {}
    with pytest.raises(CrawlError, match='时区'):
        parse_submissions(str(soup), 'kendas')

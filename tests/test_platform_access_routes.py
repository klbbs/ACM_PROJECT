"""Offline route/protection fixtures, never real OJ or CAPTCHA acceptance."""
import json
import time
from types import SimpleNamespace

import pytest

from ojcrawler.access import PlatformAccessError, protection_reason
from ojcrawler.codeforces_pages import parse_submissions, read_submissions
from ojcrawler.http import Client
from ojcrawler.models import CrawlError
from ojcrawler.adapters.codeforces import Codeforces
from ojcrawler.adapters.atcoder import AtCoder
from ojcrawler.adapters.luogu import Luogu
from acmweb.remote_judges import PlatformSession, SubmissionError


def listing(sid=123, owner='demo', *, timestamp=True, last=1, contest=4):
    date = f'<span class="format-time" data-timestamp="{int(time.time())-60}">time</span>' if timestamp else '<span class="format-time">Oct/26/2026 12:00</span>'
    return f'''<meta name="X-Csrf-Token" content="{'a'*32}">
    <div class="lang-chooser"><a href="/profile/{owner}">{owner}</a><a href="/logout">Logout</a></div>
    <table class="status-frame-datatable"><tr data-submission-id="{sid}">
    <td><a href="/contest/{contest}/submission/{sid}">{sid}</a></td><td>{date}</td>
    <td><a href="/profile/{owner}">{owner}</a></td><td><a href="/contest/{contest}/problem/A">A</a></td>
    <td>GNU C++20</td><td submissionverdict="WRONG_ANSWER">WA</td></tr></table>
    <div class="pagination"><a>{last}</a></div><script>$.post("/data/submitSource", {{submissionId: id}})</script>'''


@pytest.mark.parametrize('host',['codeforces.com','atcoder.jp','www.luogu.com.cn','ac.nowcoder.com'])
@pytest.mark.parametrize('status',[200,503])
def test_each_platform_challenge_stops_current_route_without_retry(host,status,monkeypatch):
    calls=[]
    client=Client(retries=3,interval=0)
    monkeypatch.setattr(client.session,'get',lambda *a,**k: calls.append(a[0]) or SimpleNamespace(
        status_code=status,text='<title>Just a moment...</title><form id="challenge-form"></form>',headers={},encoding='utf-8'))
    try:
        with pytest.raises(PlatformAccessError) as error:
            client.get(f'https://{host}/read')
        assert error.value.reason == 'challenge' and len(calls)==1
        assert '<form' not in str(error.value)
    finally:client.close()


@pytest.mark.parametrize('text',[
    '<title>A - Just a moment</title><div id="task-statement">正常题面</div>',
    '<pre id="program-source-text">print("cf-chl- checking your browser")</pre>',
    json.dumps({'source':'<form id="challenge-form">source text</form>'}),
])
def test_content_that_mentions_challenges_is_not_a_gate(text):
    assert protection_reason(text)==None


def test_plain_forbidden_is_not_expired_login_or_captcha():
    assert protection_reason('<title>Forbidden</title>',403)=='access_restricted'
    assert protection_reason('{}',429)=='rate_limited'


def test_rate_limit_takes_priority_over_challenge_markup():
    assert protection_reason('<form id="challenge-form"></form>', 429) == 'rate_limited'


def test_optional_cf_rating_rate_limit_stops_further_requests():
    calls = []
    def api(url, params=None):
        calls.append(url.rsplit('/', 1)[-1])
        if url.endswith('user.info'):
            return {'status': 'OK', 'result': [{'handle': 'demo'}]}
        raise PlatformAccessError('codeforces.com', 'rate_limited', 429)
    out = Codeforces(SimpleNamespace(json=api), 'demo').collect()
    assert calls == ['user.info', 'user.rating']
    assert not out.complete and out.error


def test_cf_json_failed_rate_limit_also_stops_after_ratings():
    calls = []
    def api(url, params=None):
        calls.append(url.rsplit('/', 1)[-1])
        return ({'status': 'OK', 'result': [{'handle': 'demo'}]} if url.endswith('user.info')
                else {'status': 'FAILED', 'comment': 'Call limit exceeded'})
    out = Codeforces(SimpleNamespace(json=api), 'demo').collect()
    assert calls == ['user.info', 'user.rating']
    assert not out.complete and out.error


def test_atcoder_optional_history_rate_limit_stops_profile_reads():
    calls = []
    def api(url, params=None):
        calls.append(url)
        raise PlatformAccessError('atcoder.jp', 'rate_limited', 429)
    out = AtCoder(SimpleNamespace(json=api, get=lambda url: calls.append(url)), 'demo').collect()
    assert len(calls) == 1 and calls[0].endswith('/history/json')
    assert not out.complete and out.error


def test_cf_page_checks_owner_problem_submission_and_explicit_time():
    rows,last=parse_submissions(listing(), 'DEMO')
    assert last==1 and rows[0]['id']==123
    assert rows[0]['problem']['contestId']==4 and rows[0]['author']['members']==[{'handle':'demo'}]
    with pytest.raises(CrawlError,match='账号'):
        parse_submissions(listing(owner='other'),'demo')
    with pytest.raises(CrawlError,match='时区'):
        parse_submissions(listing(timestamp=False),'demo')
    with pytest.raises(CrawlError):
        parse_submissions(listing().replace('/contest/4/submission/123','/contest/5/submission/123'),'demo')


def test_cf_empty_table_requires_explicit_official_empty_result():
    with pytest.raises(CrawlError, match='空结果'):
        parse_submissions('<table class="status-frame-datatable"></table>', 'demo')
    rows, last = parse_submissions('<table class="status-frame-datatable"><tr><td>No submissions found.</td></tr></table>', 'demo')
    assert rows == [] and last == 1


def test_cf_page_cap_and_repeated_pages_never_claim_complete():
    calls=[]
    client=SimpleNamespace(get=lambda url: calls.append(url) or listing(sid=200-len(calls),last=8))
    rows,complete=read_submissions(client,'demo',max_pages=3)
    assert len(rows)==len(calls)==3 and not complete
    with pytest.raises(CrawlError,match='分页'):
        read_submissions(SimpleNamespace(get=lambda _:listing(last=2)),'demo')


def test_cf_collector_uses_public_page_on_block_and_preserves_provenance():
    calls=[]
    def api(url,params=None):
        calls.append(url)
        if url.endswith('user.info'):return {'status':'OK','result':[{'handle':'demo'}]}
        if url.endswith('user.rating'):return {'status':'OK','result':[]}
        raise PlatformAccessError('codeforces.com','challenge')
    client=SimpleNamespace(json=api,get=lambda url: calls.append(url) or listing())
    out=Codeforces(client,'demo').collect()
    assert out.complete and out.submissions[0].id=='123'
    assert out.submissions[0].raw['source']=='official_user_submissions_page'
    assert calls[-1]=='https://codeforces.com/submissions/demo'


def test_cf_rate_limit_does_not_route_hop():
    def api(url,params=None):raise PlatformAccessError('codeforces.com','rate_limited',429)
    reads=[]
    result=Codeforces(SimpleNamespace(json=api,get=lambda url:reads.append(url)),'demo').collect()
    assert not result.complete and not reads


def test_luogu_negotiates_json_once_and_still_requires_official_schema():
    calls=[]
    def read(url,params,headers):
        calls.append(headers)
        if len(calls)==1:raise PlatformAccessError('www.luogu.com.cn','challenge')
        return '{"currentData":{"user":{"uid":42}}}'
    service=Luogu(SimpleNamespace(get=read),'42')
    assert service.data('/user/42')['user']['uid']==42
    assert len(calls)==2 and calls[1]['Accept']=='application/json'


def test_atcoder_public_index_is_not_blocked_by_profile_gate_or_used_as_login_proof():
    def read(url):raise PlatformAccessError('atcoder.jp','challenge')
    def api(url,params=None):
        return [] if url.endswith('/history/json') else [{'id':123,'epoch_second':int(time.time())-60,
            'problem_id':'abc001_1','contest_id':'abc001','result':'WA','user_id':'demo'}]
    result=AtCoder(SimpleNamespace(get=read,json=api),'demo').collect()
    assert result.complete and len(result.submissions)==1
    assert result.profile['profile_unavailable'] and 'fields' not in result.profile
    assert any('未确认' in s for s in result.warnings)


def test_remote_judge_uses_user_page_after_api_challenge(monkeypatch):
    driver=PlatformSession('codeforces','session=synthetic-only')
    calls=[]
    def request(method,url,**kw):
        calls.append((method,url))
        return (503,{},'<title>Just a moment...</title>') if '/api/' in url else (200,{},listing())
    monkeypatch.setattr(driver,'_request',request)
    try:
        record=driver.judge('4-A','demo','123')
        assert record['id']=='123' and record['verdict']=='WA'
        assert len(calls)==2 and all(method=='GET' for method,_ in calls)
        driver.judge('4-A','demo','123')
        assert len([url for _,url in calls if '/api/' in url])==1
        assert len(calls)==3
    finally:driver.close()


@pytest.mark.parametrize('outcome',['success','wrong_sid','blocked','wrong_owner'])
def test_source_popup_fallback_is_bound_to_exact_record_and_never_submits_code(monkeypatch,outcome):
    driver=PlatformSession('codeforces','session=synthetic-only')
    calls=[]
    def request(method,url,*,data=None,headers=None):
        calls.append((method,url,data,headers))
        if '/api/' in url:
            row={'id':123,'creationTimeSeconds':int(time.time())-60,'problem':{'contestId':4,'index':'A'},
                 'author':{'members':[{'handle':'demo'}]},'verdict':'WRONG_ANSWER','programmingLanguage':'GNU C++20'}
            return 200,{},json.dumps({'status':'OK','result':[row]})
        if '/contest/4/submission/' in url:return 403,{},'<title>Just a moment...</title>'
        if '/submissions/demo' in url:return 200,{},listing(owner='other' if outcome=='wrong_owner' else 'demo')
        assert url=='https://codeforces.com/data/submitSource'
        assert data=={'submissionId':'123','csrf_token':'a'*32}
        assert headers['Referer'].endswith('/submissions/demo')
        if outcome=='blocked':return 403,{},'<title>Forbidden</title>'
        return 200,{},json.dumps({'submissionId':456 if outcome=='wrong_sid' else 123,'source':'int main() {}'})
    monkeypatch.setattr(driver,'_request',request)
    try:
        if outcome=='success':assert driver.source('4-A','demo','123')['code']=='int main() {}'
        else:
            with pytest.raises(SubmissionError):driver.source('4-A','demo','123')
        posts=[c for c in calls if c[0]=='POST']
        assert len(posts)==(0 if outcome=='wrong_owner' else 1)
        assert all(c[1].endswith('/data/submitSource') and 'source' not in c[2] and 'sourceCode' not in c[2] for c in posts)
    finally:driver.close()


@pytest.mark.parametrize('wrong_owner',[False,True])
def test_atcoder_judge_can_use_official_list_but_never_treats_it_as_source(monkeypatch,wrong_owner):
    driver=PlatformSession('atcoder','session=synthetic-only'); calls=[]
    owner='other' if wrong_owner else 'demo'
    date='2026-01-01T12:00:00+09:00'
    page=f'''<table><thead><tr><th>Submission Time</th><th>Task</th><th>User</th><th>Language</th><th>Status</th><th>Details</th></tr></thead>
      <tbody><tr><td><time datetime="{date}">date</time></td><td><a href="/contests/abs/tasks/practice_1">A</a></td>
      <td><a href="/users/{owner}">{owner}</a></td><td>C++20</td><td>WA</td><td><a href="/contests/abs/submissions/123">Details</a></td></tr></tbody></table>'''
    def request(method,url,**kwargs):
        calls.append(url)
        return (403,{},'<title>Just a moment...</title>') if url.endswith('/submissions/123') else (200,{},page)
    monkeypatch.setattr(driver,'_request',request)
    try:
        if wrong_owner:
            with pytest.raises(SubmissionError) as error:driver.judge('atcoder:practice_1','demo','123')
            assert error.value.reason=='record_mismatch'
        else:
            record=driver.judge('atcoder:practice_1','demo','123')
            assert record['verdict']=='WA' and record['source_block_reason']=='challenge'
            calls.clear()
            with pytest.raises(SubmissionError) as error:driver.source('atcoder:practice_1','demo','123')
            assert error.value.reason=='challenge' and len(calls)==1
        assert all('/submissions' in url for url in calls)
    finally:driver.close()


def test_cf_profile_can_use_only_the_actual_public_profile_on_api_gate():
    calls=[]
    def api(url,params=None):
        if url.endswith('/user.info'):raise PlatformAccessError('codeforces.com','challenge')
        return {'status':'OK','result':[]}
    html='<div class="info"><div class="main-info"><h1><a href="/profile/demo">demo</a></h1></div><ul><li>Contest rating: 1200</li></ul></div>'
    result=Codeforces(SimpleNamespace(json=api,get=lambda url:calls.append(url) or html),'demo').collect()
    assert result.complete and result.profile['rating']==1200
    assert result.profile['source']=='official_profile_page'
    assert calls==['https://codeforces.com/profile/demo']


def test_luogu_two_blocked_representations_remain_failure_not_empty_success():
    calls=[]
    def read(*args):
        calls.append(args)
        raise PlatformAccessError('www.luogu.com.cn','challenge')
    result=Luogu(SimpleNamespace(get=read),'42').collect()
    assert not result.complete and result.error and not result.submissions
    assert len(calls)==2


def test_atcoder_statement_tries_an_official_locale_then_returns_verified_structure(monkeypatch):
    from acmweb.problem_sources import fetch_statement
    calls=[]
    class Transport:
        def __init__(self,**kwargs):pass
        def close(self):pass
        def get(self,url,params=None,headers=None):
            calls.append((url,params))
            if params['lang']=='en':raise PlatformAccessError('atcoder.jp','challenge')
            return '<span class="h2">A</span><div id="task-statement"><div class="lang-ja">本文</div></div>'
    monkeypatch.setattr('acmweb.problem_sources.Client',Transport)
    result=fetch_statement('atcoder:practice_1',[])
    assert result['id']=='atcoder:practice_1' and len(calls)==2
    assert calls[0][0]==calls[1][0]=='https://atcoder.jp/contests/abs/tasks/practice_1'
    assert calls[1][1]=={'lang':'ja'}


def test_statement_rate_limit_never_retries_an_alias_or_saved_cookie(monkeypatch):
    from acmweb.problem_sources import fetch_statement
    from acmweb.codex_bridge import AIError
    calls=[]
    class Transport:
        def __init__(self,**kwargs):pass
        def close(self):pass
        def get(self,*args,**kwargs):
            calls.append(args)
            raise PlatformAccessError('atcoder.jp','rate_limited',429)
    monkeypatch.setattr('acmweb.problem_sources.Client',Transport)
    with pytest.raises(AIError,match='停止切换'):
        fetch_statement('atcoder:practice_1',[{'platform':'atcoder','cookie':'session=synthetic'}])
    assert len(calls)==1


def test_catalog_gate_has_a_fixed_readable_reason_instead_of_schema_error():
    from acmweb.catalog import _sync_error
    reason,message=_sync_error(PlatformAccessError('ac.nowcoder.com','challenge'))
    assert reason=='browser_challenge' and '旧缓存保留' in message


def test_catalog_block_preserves_the_previous_rows(tmp_path,monkeypatch):
    from acmweb.database import Database
    from acmweb import catalog
    db=Database(tmp_path/'catalog.sqlite3')
    with db.connect() as connection:
        connection.execute('INSERT INTO problem_catalog VALUES(?,?,?)',('codeforces','codeforces:4-A','{"id":"codeforces:4-A","title":"cached"}'))
    def blocked(_):raise PlatformAccessError('codeforces.com','challenge')
    monkeypatch.setitem(catalog.DISCOVERY,'codeforces',blocked)
    result=catalog.refresh_catalog(db,'codeforces')
    assert not result['complete'] and result['platforms'][0]['error_code']=='browser_challenge'
    with db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM problem_catalog').fetchone()[0]==1


@pytest.mark.parametrize('json_limit',[False,True])
def test_gym_rate_limit_does_not_move_to_an_html_route(json_limit):
    from acmweb.gym_submissions import collect_contest
    reads=[]
    def api(*args,**kwargs):
        if json_limit:return {'status':'FAILED','comment':'Call limit exceeded'}
        raise PlatformAccessError('codeforces.com','rate_limited',429)
    with pytest.raises(PlatformAccessError):
        collect_contest(SimpleNamespace(json=api,get=lambda *a,**k: reads.append(a)),727347,'demo',0)
    assert not reads


def test_failed_cf_fallback_preserves_the_gate_so_polling_can_stop(monkeypatch):
    driver=PlatformSession('codeforces','session=synthetic-only')
    monkeypatch.setattr(driver,'_request',lambda method,url,**kwargs:
        (503,{},'<title>Just a moment...</title>') if '/api/' in url else (200,{},listing(timestamp=False)))
    try:
        with pytest.raises(SubmissionError) as error:driver.judge('4-A','demo','123')
        assert error.value.reason=='challenge'
    finally:driver.close()

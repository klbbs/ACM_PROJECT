"""Offline protocol fixtures; these tests do not claim a live submission passed."""
import json
import time
from datetime import datetime, timezone

import pytest
import requests

from acmweb.remote_judges import PlatformSession, SubmissionError


def cf_form(handle='demo'):
    return f'''<div class="lang-chooser"><a href="/profile/{handle}">{handle}</a><a href="/logout">Logout</a></div>
    <form method="post" action="/problemset/submit"><input type="hidden" name="csrf_token" value="SECRET-CSRF"><input type="hidden" name="ftaa" value="SECRET-FTAA"><input type="hidden" name="bfaa" value="SECRET-BFAA"><select name="programTypeId"><option value="54">GNU C++17</option><option value="89">GNU C++20</option></select><textarea name="source"></textarea></form>'''


def ac_nav(handle='demo'):
    return f'<div id="navbar-collapse"><a href="/users/{handle}">{handle}</a></div><form action="/logout" method="post"></form>'


def ac_form(contest='abs', task='practice_1'):
    return ac_nav() + f'''<form method="post" action="/contests/{contest}/submit"><input type="hidden" name="csrf_token" value="SECRET-CSRF"><select name="data.TaskScreenName"><option value="{task}">{task}</option></select><div id="select-lang"><div id="select-lang-wrong_task"><select><option value="9999">Wrong task language</option></select></div><div id="select-lang-{task}"><select><option value="6001">C++ 23</option><option value="6002">Python 3.13</option></select></div></div><textarea name="sourceCode"></textarea></form>'''


def cf_record(sid=123, **changes):
    row = {'id': sid, 'problem': {'contestId': 4, 'index': 'A'}, 'author': {'members': [{'handle': 'demo'}]}, 'creationTimeSeconds': int(time.time()), 'verdict': 'WRONG_ANSWER', 'programmingLanguage': 'GNU C++20', 'passedTestCount': 1, 'source': 'MUST-NOT-LEAK', 'csrf_token': 'MUST-NOT-LEAK'}
    row.update(changes)
    return row


def ac_record(handle='demo', task='practice_1', contest='abs'):
    stamp = datetime.now(timezone.utc).isoformat()
    return f'''<table><tr><th>Submission Time</th><td><time datetime="{stamp}">{stamp}</time></td></tr><tr><th>Task</th><td><a href="/contests/{contest}/tasks/{task}">{task}</a></td></tr><tr><th>User</th><td><a href="/users/{handle}">{handle}</a></td></tr><tr><th>Language</th><td>C++ 23</td></tr><tr><th>Status</th><td><span>WA</span></td></tr></table><pre id="submission-code">SECRET-SOURCE</pre>'''


@pytest.fixture
def session():
    made = []
    def create(platform='codeforces'):
        value = PlatformSession(platform, 'session=SECRET-COOKIE')
        made.append(value)
        return value
    yield create
    for value in made: value.close()


def script(monkeypatch, session, replies):
    calls = []
    def request(method, url, *, data=None):
        calls.append((method, url, data))
        response = replies.pop(0)
        if isinstance(response, Exception): raise response
        return response
    monkeypatch.setattr(session, '_request', request)
    return calls


def test_cf_dynamic_compilers_hidden_fields_and_single_post(session, monkeypatch):
    s = session()
    calls = script(monkeypatch, s, [
        (200, {}, cf_form()), (200, {}, json.dumps({'status':'OK', 'result':[]})),
        (302, {'Location':'/contest/4/submission/123'}, ''),
    ])
    boundary = []
    s.before_send = lambda: boundary.append(True)
    assert s.submit('codeforces:4-A', 'demo', 'int main(){}', '89') == {'id':'123', 'url':'https://codeforces.com/contest/4/submission/123', 'compiler_name':'GNU C++20'}
    post = calls[-1]
    assert post[0] == 'POST' and len([c for c in calls if c[0] == 'POST']) == 1
    assert post[2]['source'] == 'int main(){}'
    assert post[2]['programTypeId'] == '89' and post[2]['ftaa'] == 'SECRET-FTAA'
    assert post[2]['csrf_token'] == 'SECRET-CSRF' and post[2]['bfaa'] == 'SECRET-BFAA'
    assert post[2]['submittedProblemIndex'] == 'A' and boundary == [True]


def test_atcoder_task_specific_dynamic_language_and_first_submission(session, monkeypatch):
    s = session('atcoder')
    calls = script(monkeypatch, s, [(200,{},ac_form()), (200,{},ac_nav()+'<h2>My Submissions</h2><p>No Submission</p>'), (302,{'Location':'/contests/abs/submissions/123'},'')])
    assert s.submit('atcoder:practice_1', 'demo', 'code', '6001')['id'] == '123'
    assert '/contests/abs/submit' in calls[0][1]
    assert calls[-1][2]['data.LanguageId'] == '6001'
    assert calls[-1][2]['data.TaskScreenName'] == 'practice_1'
    assert calls[-1][2]['sourceCode'] == 'code'


@pytest.mark.parametrize('platform,form,pid,expected', [('codeforces',cf_form(),'4-A',['54','89']),('atcoder',ac_form(),'atcoder:practice_1',['6001','6002'])])
def test_options_are_official_not_hardcoded(session,monkeypatch,platform,form,pid,expected):
    s=session(platform);script(monkeypatch,s,[(200,{},form)])
    assert [r['id'] for r in s.options(pid,'demo')] == expected


@pytest.mark.parametrize('html,reason', [(cf_form('other'),'account_mismatch'),('<form action="/enter"><input type="password"></form>','login_required'),('<div id="challenge-running">challenge</div>','challenge'),(cf_form().replace('SECRET-CSRF',''),'form_changed')])
def test_options_fail_closed_on_identity_and_challenges(session,monkeypatch,html,reason):
    s=session();calls=script(monkeypatch,s,[(200,{},html)])
    with pytest.raises(SubmissionError) as error:s.options('4-A','demo')
    assert error.value.reason == reason and not any(c[0]=='POST' for c in calls)
    assert 'SECRET' not in str(error.value)


def test_compiler_change_never_posts(session,monkeypatch):
    s=session();calls=script(monkeypatch,s,[(200,{},cf_form())])
    with pytest.raises(SubmissionError) as e:s.submit('4-A','demo','code','999')
    assert e.value.reason=='language_unsupported' and len(calls)==1


def test_no_latest_record_guess_and_safe_raw(session,monkeypatch):
    s=session();replies=[(200,{},cf_form()),(200,{},json.dumps({'status':'OK','result':[cf_record(122)]})),(302,{'Location':'/my'},''),(200,{},json.dumps({'status':'OK','result':[cf_record(123),cf_record(122)]})),(200,{},json.dumps({'status':'OK','result':[cf_record(123),cf_record(122)]}))]
    script(monkeypatch,s,replies)
    assert s.submit('4-A','demo','code','89')['id']=='123'
    script(monkeypatch,s,[(200,{},json.dumps({'status':'OK','result':[cf_record()]}))])
    result=s.judge('4-A','demo','123')
    assert result['verdict']=='WA' and result['account']=='demo' and result['problem_id']=='4-A'
    assert 'MUST-NOT-LEAK' not in json.dumps(result) and 'csrf_token' not in result['raw']


def test_ambiguous_new_records_are_uncertain_not_latest(session,monkeypatch):
    s=session();rows=[cf_record(123),cf_record(124)]
    script(monkeypatch,s,[(200,{},cf_form()),(200,{},json.dumps({'status':'OK','result':[]})),(302,{'Location':'/my'},''),(200,{},json.dumps({'status':'OK','result':rows})),(200,{},json.dumps({'status':'OK','result':rows})),(200,{},json.dumps({'status':'OK','result':rows}))])
    with pytest.raises(SubmissionError) as e:s.submit('4-A','demo','code','89')
    assert e.value.uncertain and e.value.reason=='uncertain'


@pytest.mark.parametrize('changes',[{'problem':{'contestId':4,'index':'B'}},{'author':{'members':[{'handle':'other'}]}},{'author':{'members':[{'handle':'demo'},{'handle':'team'}]}}])
def test_cf_judge_checks_actual_author_and_problem(session,monkeypatch,changes):
    s=session();script(monkeypatch,s,[(200,{},json.dumps({'status':'OK','result':[cf_record(**changes)]}))])
    with pytest.raises(SubmissionError) as e:s.judge('4-A','demo',123)
    assert e.value.reason=='record_mismatch'


def test_atcoder_official_judge_does_not_wait_for_mirror(session,monkeypatch):
    s=session('atcoder');calls=script(monkeypatch,s,[(200,{},ac_record())])
    r=s.judge('atcoder:practice_1','demo',123)
    assert r['verdict']=='WA' and r['account']=='demo' and r['problem_id']=='practice_1'
    assert calls[0][1]=='https://atcoder.jp/contests/abs/submissions/123'
    assert 'SECRET' not in json.dumps(r)


@pytest.mark.parametrize('platform,pid,html', [
    ('codeforces','4-A', cf_form()+'<pre id="program-source-text">int main(){return 0;}</pre>'),
    ('atcoder','atcoder:practice_1', ac_nav()+ac_record()),
])
def test_source_reads_verified_official_page_only(session,monkeypatch,platform,pid,html):
    s = session(platform)
    monkeypatch.setattr(s,'judge',lambda *_:{'id':'123','account':'demo','problem_id':pid.split(':')[-1],
        'submitted_at':int(time.time()),'url':s.base+'/official-source'})
    calls = script(monkeypatch,s,[(200,{},html)])
    assert s.source(pid,'demo','123')['code']
    assert len(calls) == 1 and calls[0][0] == 'GET'


def test_source_never_follows_other_record_redirect(session,monkeypatch):
    s = session()
    monkeypatch.setattr(s,'judge',lambda *_:{'url':s.base+'/contest/4/submission/123'})
    calls = script(monkeypatch,s,[(302,{'Location':'/contest/4/submission/456'},'')])
    with pytest.raises(SubmissionError) as error:
        s.source('4-A','demo','123')
    assert error.value.reason == 'record_unavailable'
    assert len(calls) == 1


@pytest.mark.parametrize('copies,correct',[(1,True),(2,True),(1,False)])
def test_unknown_receipt_requires_exact_source_and_single_match(session,monkeypatch,copies,correct):
    import hashlib
    s = session()
    stamp = int(time.time())
    monkeypatch.setattr(s,'_records',lambda *_:[cf_record(123+n) for n in range(copies)])
    monkeypatch.setattr(s,'judge',lambda pid,handle,sid:{'id':str(sid),'account':'demo','problem_id':'4-A','submitted_at':stamp})
    monkeypatch.setattr(s,'source',lambda pid,handle,sid:{'id':str(sid),'code':'expected' if correct else 'wrong'})
    digest = hashlib.sha256(b'expected').hexdigest()
    if copies == 1 and correct:
        assert s.find_receipt('4-A','demo',stamp,digest)['id'] == '123'
    else:
        with pytest.raises(SubmissionError):
            s.find_receipt('4-A','demo',stamp,digest)


@pytest.mark.parametrize('handle,task',[('other','practice_1'),('demo','other_task')])
def test_atcoder_judge_rejects_wrong_source_record(session,monkeypatch,handle,task):
    s=session('atcoder');script(monkeypatch,s,[(200,{},ac_record(handle,task))])
    with pytest.raises(SubmissionError) as e:s.judge('atcoder:practice_1','demo',123)
    assert e.value.reason=='record_mismatch'


@pytest.mark.parametrize('location',['https://evil.invalid/steal','http://codeforces.com/','https://codeforces.com@evil.invalid/','https://codeforces.com:444/','https://atcoder.jp/'])
def test_no_cookie_or_code_redirect_to_other_origins(session,monkeypatch,location):
    s=session();calls=script(monkeypatch,s,[(302,{'Location':location},'')])
    with pytest.raises(SubmissionError):s.options('4-A','demo')
    assert len(calls)==1


def test_send_boundary_can_cancel_without_source_post(session,monkeypatch):
    s=session();calls=script(monkeypatch,s,[(200,{},cf_form()),(200,{},json.dumps({'status':'OK','result':[]}))])
    def cancel(): raise SubmissionError('account_mismatch')
    s.before_send=cancel
    with pytest.raises(SubmissionError):s.submit('4-A','demo','code','89')
    assert not any(c[0]=='POST' for c in calls)


def test_post_timeout_is_not_retried_or_exposed(session,monkeypatch):
    s=session();calls=[]
    def request(method,url,**kwargs):
        calls.append(method)
        raise requests.Timeout('SECRET-COOKIE SOURCE TOKEN')
    monkeypatch.setattr(s.session,'request',request)
    with pytest.raises(SubmissionError) as e:s._request('POST','/problemset/submit',data={'source':'SECRET-SOURCE'})
    assert calls==['POST'] and e.value.uncertain and 'SECRET' not in str(e.value)


def test_post_redirect_preserving_method_is_never_followed(session,monkeypatch):
    s=session();calls=script(monkeypatch,s,[(200,{},cf_form()),(200,{},json.dumps({'status':'OK','result':[]})),(307,{'Location':'/problemset/submit'},'')])
    with pytest.raises(SubmissionError) as e:s.submit('4-A','demo','code','89')
    assert e.value.uncertain and len([c for c in calls if c[0]=='POST'])==1


@pytest.mark.parametrize('task,contest',[('math_and_algorithm_a','math-and-algorithm'),
                                         ('tessoku_book_a','tessoku-book'),
                                         ('practice_1','abs')])
def test_atcoder_collection_task_uses_official_contest_path(session,monkeypatch,task,contest):
    """Submit form, baseline list and POST receipt share one allowlisted path."""
    s = session('atcoder');pid = 'atcoder:' + task
    calls = script(monkeypatch,s,[(200,{},ac_form(contest,task)),
                                  (200,{},ac_nav()+'<p>No Submission</p>'),
                                  (302,{'Location':f'/contests/{contest}/submissions/123'},'')])
    assert s.submit(pid,'demo','code','6001')['id'] == '123'
    assert calls[0][1] == f'https://atcoder.jp/contests/{contest}/submit?taskScreenName={task}'
    assert calls[1][1] == f'/contests/{contest}/submissions/me'
    assert calls[-1][2]['data.TaskScreenName'] == task
    assert len([c for c in calls if c[0] == 'POST']) == 1


def test_atcoder_judge_reads_official_collection_submission(session,monkeypatch):
    s = session('atcoder')
    calls = script(monkeypatch,s,[(200,{},ac_record(task='math_and_algorithm_a',contest='math-and-algorithm'))])
    record = s.judge('atcoder:math_and_algorithm_a','demo',123)
    assert calls[0][1] == 'https://atcoder.jp/contests/math-and-algorithm/submissions/123'
    assert record['contest_id'] == 'math-and-algorithm'
    assert record['problem_id'] == 'math_and_algorithm_a' and record['verdict'] == 'WA'


def test_atcoder_record_from_other_contest_is_a_mismatch(session,monkeypatch):
    s = session('atcoder')
    script(monkeypatch,s,[(200,{},ac_record(task='math_and_algorithm_a',contest='math_and_algorithm'))])
    with pytest.raises(SubmissionError) as e:s.judge('atcoder:math_and_algorithm_a','demo',123)
    assert e.value.reason == 'record_mismatch'


def test_atcoder_form_must_post_to_the_official_contest(session,monkeypatch):
    """A form served under another contest path never receives source code."""
    s = session('atcoder')
    calls = script(monkeypatch,s,[(200,{},ac_form('math_and_algorithm','math_and_algorithm_a'))])
    with pytest.raises(SubmissionError) as e:s.options('atcoder:math_and_algorithm_a','demo')
    assert e.value.reason == 'form_changed' and not any(c[0] == 'POST' for c in calls)


@pytest.mark.parametrize('key',['','abc001','ABC001_1','../abs','math_and_algorithm_a/../x'])
def test_atcoder_contest_path_helper_fails_closed(session,key):
    s = session('atcoder')
    with pytest.raises(SubmissionError) as e:s._contest(key)
    assert e.value.reason == 'invalid_input' and 'SECRET' not in str(e.value)


@pytest.mark.parametrize('platform,pid',[('luogu','luogu:P1001'),('nowcoder','nowcoder:1000')])
def test_unverified_platform_protocol_explicitly_unsupported(session,monkeypatch,platform,pid):
    s=session(platform);calls=script(monkeypatch,s,[])
    with pytest.raises(SubmissionError) as e:s.options(pid,'12345')
    assert e.value.reason=='unsupported' and not calls


def test_explicit_old_id_is_not_reclaimed_even_when_recent(session,monkeypatch):
    s=session();script(monkeypatch,s,[(200,{},cf_form()),(200,{},json.dumps({'status':'OK','result':[cf_record(123)]})),(302,{'Location':'/contest/4/submission/123'},'')])
    with pytest.raises(SubmissionError) as e:s.submit('4-A','demo','code','89')
    assert e.value.uncertain


@pytest.mark.parametrize('code',['', 'a'*65537, '\ud800'])
def test_invalid_source_is_rejected_before_any_request(session,monkeypatch,code):
    s=session();calls=script(monkeypatch,s,[])
    with pytest.raises(SubmissionError) as e:s.submit('4-A','demo',code,'89')
    assert e.value.reason=='invalid_input' and not calls


def test_atcoder_identity_and_judge_do_not_fold_case(session,monkeypatch):
    s=session('atcoder');script(monkeypatch,s,[(200,{},ac_form().replace('/users/demo','/users/Demo'))])
    with pytest.raises(SubmissionError) as e:s.options('atcoder:practice_1','demo')
    assert e.value.reason=='account_mismatch'
    script(monkeypatch,s,[(200,{},ac_record('Demo'))])
    with pytest.raises(SubmissionError) as e:s.judge('atcoder:practice_1','demo',123)
    assert e.value.reason=='record_mismatch'


@pytest.mark.parametrize('result',[[{}],[None],[{'id':'invalid'}]])
def test_bad_baseline_never_sends_source(session,monkeypatch,result):
    s=session();calls=script(monkeypatch,s,[(200,{},cf_form()),(200,{},json.dumps({'status':'OK','result':result}))])
    with pytest.raises(SubmissionError) as e:s.submit('4-A','demo','code','89')
    assert e.value.reason=='record_unavailable' and not any(c[0]=='POST' for c in calls)

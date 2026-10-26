import pytest
from acmweb.problem_sources import (ATCODER_FIXED_CONTESTS, atcoder_contest, capabilities,
                                    normalize, parse_statement, source_url)
from acmweb.codex_bridge import AIError

@pytest.mark.parametrize('raw,pid',[
 ('https://atcoder.jp/contests/abc001/tasks/abc001_1','atcoder:abc001_1'),
 ('https://www.luogu.com.cn/problem/P1001','luogu:P1001'),
 ('https://ac.nowcoder.com/acm/problem/14301','nowcoder:14301'),
 ('https://ac.nowcoder.com/acm/contest/5657/A','nowcoder:contest-5657-A'),('4-a','codeforces:4-A')])
def test_normalize(raw,pid):
 assert normalize(raw)==pid
 assert normalize(source_url(pid))==pid

@pytest.mark.parametrize('raw',['https://evil.com/problem/P1001','https://atcoder.jp:443/contests/abc/tasks/abc_a','https://atcoder.jp@evil.com/contests/abc/tasks/abc_a','nowcoder:../123'])
def test_reject_urls(raw):
 with pytest.raises(AIError): normalize(raw)

def test_atcoder_samples_math_images():
 html='<span class="h2">A - Test</span><div id="task-statement"><span class="lang-en"><section><h3>Problem</h3><p>\\(N\\)</p><img src="/images/test.png" onerror="evil()"><script>bad()</script></section><section><h3>Sample Input 1</h3><pre>1\n2</pre></section><section><h3>Sample Output 1</h3><pre>3</pre></section></span></div>'
 r=parse_statement(html,'atcoder:abc001_1')
 assert r['samples']==[{'input':'1\n2','output':'3'}]
 assert r['images'][0]['url']=='https://atcoder.jp/images/test.png'
 assert '\\(N\\)' in r['body_html'] and 'onerror' not in r['body_html'] and 'bad()' not in r['body_html']

@pytest.mark.parametrize('title_key', ['title', 'name'])
def test_luogu_lentille_markdown(title_key):
 import json
 html='<script id="lentille-context" type="application/json">'+json.dumps({'data':{'problem':{title_key:'A+B','contenu':{'description':'计算 $a+b$ ![图](https://cdn.luogu.com.cn/x.png)','formatI':'两个整数','formatO':'和'},'samples':[['1 2','3']]}}})+'</script>'
 r=parse_statement(html,'luogu:P1001')
 assert r['title']=='A+B' and '$a+b$' in r['sections'][0] and not r['body_html']
 assert len(r['samples'])==1 and r['has_images']

def test_nowcoder_samples_and_permission():
 html='<title>A+B</title><div class="subject-describe"><p>计算</p><div class="question-oi-bd"><div class="question-oi-mod"><h2>输入</h2><div class="question-oi-cont"><pre>1 2</pre></div></div><div class="question-oi-mod"><h2>输出</h2><div class="question-oi-cont"><pre>3</pre></div></div></div></div>'
 r=parse_statement(html,'nowcoder:14301');assert r['samples']==[{'input':'1 2','output':'3'}]
 with pytest.raises(AIError):parse_statement('<title>没有权限</title>','nowcoder:1000')

def test_capability_does_not_claim_cookie_proves_login():
 c=capabilities('luogu:P1001',[{'platform':'luogu','cookie':'a=b'}])
 assert c['cookie_configured'] and not c['login_verified'] and not c['auto_submit'] and c['judge_sync']


# Official collection contests use hyphens where the task id uses underscores,
# and ABS lives under `abs`. One helper must decide every AtCoder contest path.
@pytest.mark.parametrize('pid,url',[
 ('atcoder:math_and_algorithm_a','https://atcoder.jp/contests/math-and-algorithm/tasks/math_and_algorithm_a'),
 # Second id of each collection only exercises the shared-prefix rule.
 ('atcoder:math_and_algorithm_zz','https://atcoder.jp/contests/math-and-algorithm/tasks/math_and_algorithm_zz'),
 ('atcoder:tessoku_book_a','https://atcoder.jp/contests/tessoku-book/tasks/tessoku_book_a'),
 ('atcoder:tessoku_book_zz','https://atcoder.jp/contests/tessoku-book/tasks/tessoku_book_zz'),
 ('atcoder:practice_1','https://atcoder.jp/contests/abs/tasks/practice_1'),
 # Ordinary contests keep the documented task-id prefix rule.
 ('atcoder:abc001_1','https://atcoder.jp/contests/abc001/tasks/abc001_1'),
 ('atcoder:practice2_a','https://atcoder.jp/contests/practice2/tasks/practice2_a'),
 ('atcoder:typical90_at','https://atcoder.jp/contests/typical90/tasks/typical90_at'),
 ('atcoder:dp_a','https://atcoder.jp/contests/dp/tasks/dp_a')])
def test_atcoder_contest_path_follows_official_collection(pid,url):
 assert source_url(pid)==url
 assert normalize(url)==pid
 assert normalize(source_url(pid))==pid
 contest=url.split('/contests/')[1].split('/tasks/')[0];task=pid.split(':',1)[1]
 submit=capabilities(pid,[])['official_submit_url']
 assert submit==f'https://atcoder.jp/contests/{contest}/submit?taskScreenName={task}'


def test_atcoder_contest_helper_is_allowlisted_not_request_derived():
 assert atcoder_contest('math_and_algorithm_a')=='math-and-algorithm'
 assert atcoder_contest('tessoku_book_a')=='tessoku-book'
 assert atcoder_contest('practice_1')=='abs'
 assert atcoder_contest('abc001_1')=='abc001'
 assert ATCODER_FIXED_CONTESTS=={'practice_1':'abs','math_and_algorithm':'math-and-algorithm',
                                 'tessoku_book':'tessoku-book'}
 for key in ('math_and_algorithm_a','tessoku_book_a','practice_1','abc001_1'):
  contest=atcoder_contest(key)
  assert '/' not in contest and ':' not in contest and contest==contest.lower()


@pytest.mark.parametrize('key',['','abc001','ABC001_1','abc001_1/../x','../abs','abc 001_1',
                                'abc001_1\n','math_and_algorithm_a/../../abs',None,123])
def test_atcoder_contest_helper_rejects_unsafe_task_ids(key):
 with pytest.raises(AIError):atcoder_contest(key)


@pytest.mark.parametrize('raw',[
 # The collection's contest path is fixed; a link naming another contest is not
 # an official task page.
 'https://atcoder.jp/contests/math_and_algorithm/tasks/math_and_algorithm_a',
 'https://atcoder.jp/contests/abc001/tasks/math_and_algorithm_a',
 'https://atcoder.jp/contests/tessoku/tasks/tessoku_book_a',
 'https://atcoder.jp/contests/abc001/tasks/tessoku_book_a',
 'https://atcoder.jp/contests/abc001/tasks/practice_1',
 'https://atcoder.jp/contests/math-and-algorithm/tasks/math_and_algorithm_a/extra',
 # No extra host, path prefix or scheme variants.
 'https://atcoder.jp.evil.invalid/contests/abs/tasks/practice_1',
 'https://evil.invalid/contests/math-and-algorithm/tasks/math_and_algorithm_a',
 'http://atcoder.jp/contests/math-and-algorithm/tasks/math_and_algorithm_a',
 'https://atcoder.jp/contests/math-and-algorithm/tasks/math_and_algorithm_a?x=1'])
def test_atcoder_official_url_is_required(raw):
 with pytest.raises(AIError):normalize(raw)


def test_atcoder_statement_uses_official_collection_url():
 html=('<span class="h2">A - Print 5+N</span><div id="task-statement"><span class="lang-en"><section>'
       '<h3>Problem</h3><p>Print 5+N.</p><img src="/images/test.png"></section>'
       '<section><h3>Sample Input 1</h3><pre>1</pre></section>'
       '<section><h3>Sample Output 1</h3><pre>6</pre></section></span></div>')
 r=parse_statement(html,'atcoder:math_and_algorithm_a')
 assert r['url']=='https://atcoder.jp/contests/math-and-algorithm/tasks/math_and_algorithm_a'
 assert r['images'][0]['url']=='https://atcoder.jp/images/test.png'
 assert r['samples']==[{'input':'1','output':'6'}]


def test_atcoder_old_markup_heading_outside_section():
 r=parse_statement('<span class="h2">A</span><div id="task-statement"><div class="part"><h3>問題文</h3><section><var>N^2</var></section></div><div class="part"><h3>入力例 1</h3><section><pre>1</pre></section></div><div class="part"><h3>出力例 1</h3><section><pre>2</pre></section></div></div>','atcoder:abc001_1')
 assert r['samples']==[{'input':'1','output':'2'}]
 assert '\\(N^2\\)' in r['body_html']

def test_non_cf_candidates_only_own_official_unattempted(tmp_path):
 from acmweb.database import Database
 from acmweb.catalog import candidates
 import json
 db=Database(tmp_path/'test.sqlite3')
 with db.connect() as conn:
  for pid,source in [('atcoder:abc001_1','official'),('atcoder:abc001_2','user_import'),('luogu:P1001','official')]:
   conn.execute('INSERT INTO wb_problems VALUES(?,?,?)',(pid,json.dumps({'id':pid,'source':source,'title':pid,'url':source_url(pid)}),1))
 rows=candidates(db,platform='atcoder')
 assert len(rows)==1 and rows[0]['id']=='atcoder:abc001_1' and rows[0]['difficulty'] is None
 with db.connect() as conn:
  conn.execute('INSERT INTO practice_feedback VALUES(?,?,?,?,?)',('atcoder','abc001_1','done','',1))
 assert candidates(db,platform='atcoder')==[]

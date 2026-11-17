import json
from acmweb.database import Database
from acmweb.submission_statistics import (statistics, parse_atcoder, parse_nowcoder,
    related_nowcoder_contests, refresh_statistics, save_statistics)
from ojcrawler.models import CrawlError
import pytest


def atcoder_fixture():
    return {'Fixed':True,'TaskInfo':[{'TaskScreenName':'abc100_a'}], 'StandingsData':[
        {'UserScreenName':'one','TaskResults':{'abc100_a':{'Count':3,'Score':10000,'Status':1}}},
        {'UserScreenName':'two','TaskResults':{'abc100_a':{'Count':2,'Score':0,'Status':2}}}]}


def test_units_are_never_mixed_and_zero_denominator_unknown():
    assert statistics(10,accepted=4,source='x',scope='all_time',count_kind='all_submissions')['acceptance_rate']==.4
    x=statistics(10,attempted_people=4,accepted_people=2,source='x',scope='contest',count_kind='all_contest_submissions')
    assert x['acceptance_rate']==.5 and x['rate_unit']=='participant' and 'accepted_count' not in x
    assert statistics(0,accepted=0,source='x',scope='all_time',count_kind='all_submissions')['acceptance_rate'] is None
    assert statistics(True,source='x',scope='all_time',count_kind='all_submissions') is None


def test_atcoder_actual_count_and_participant_success_are_separate():
    known={'abc100_a':{'id':'atcoder:abc100_a','contest_id':'abc100','atcoder_points':100}}
    data=atcoder_fixture();result=parse_atcoder(data,'abc100',known)['atcoder:abc100_a']
    assert result['submitted_count']==5 and result['acceptance_rate']==.5
    assert result['attempted_participants']==2 and result['accepted_participants']==1
    assert 'one' not in json.dumps(result) and 'SubmissionID' not in json.dumps(result)
    assert parse_atcoder(data,'abc101',known)=={}
    data['StandingsData'][0]['TaskResults']['abc100_a']['Score']=5000
    assert parse_atcoder(data,'abc100',known)['atcoder:abc100_a']['accepted_participants']==0


@pytest.mark.parametrize('change',[{'Fixed':'false'},{'truncated':True}])
def test_atcoder_incomplete_data_is_not_published(change):
    data=atcoder_fixture();data.update(change)
    with pytest.raises(CrawlError):parse_atcoder(data,'abc100',{})


def nc_fixture():
    return {'code':0,'data':{'basicInfo':{'contestId':90070,'problemCount':1,'pageCount':1},
        'data':[{'problemId':278845,'submitCount':100,'submitPersonCount':40,'acceptedCount':20}]}}


def test_nowcoder_matches_problem_and_contest_and_uses_person_denominator():
    known={'nowcoder:278845':{}}
    result=parse_nowcoder(nc_fixture(),90070,known)['nowcoder:278845']
    assert result['submitted_count']==100 and result['acceptance_rate']==.5
    assert result['rate_unit']=='participant' and result['contest_id']==90070
    assert parse_nowcoder(nc_fixture(),90070,{'nowcoder:1':{}})=={}
    with pytest.raises(CrawlError):parse_nowcoder(nc_fixture(),90071,known)
    data=nc_fixture();data['data']['basicInfo']['problemCount']=2
    with pytest.raises(CrawlError):parse_nowcoder(data,90070,known)


def test_related_contests_do_not_include_unrelated_sidebar_links():
    html='<h3>相关比赛</h3><ul><li><a href="/acm/contest/90070/">比赛</a></li></ul><ul><a href="/acm/contest/1">广告</a></ul>'
    assert related_nowcoder_contests(html)==[90070]
    assert related_nowcoder_contests('<a href="/acm/contest/1">广告</a>')==[]


def test_storage_keeps_existing_tags_and_isolates_workspaces(tmp_path):
    first=Database(tmp_path/'one.sqlite3');second=Database(tmp_path/'two.sqlite3')
    for db in (first,second):
        with db.connect() as conn:conn.execute('insert into problem_catalog values(?,?,?)',('luogu','luogu:P1001',json.dumps({'tags':['1']})))
    save_statistics(first,'luogu',{'luogu:P1001':{'submitted_count':10}})
    with first.connect() as conn: assert json.loads(conn.execute('select data from problem_catalog').fetchone()[0])['tags']==['1']
    with second.connect() as conn: assert 'submission_statistics' not in json.loads(conn.execute('select data from problem_catalog').fetchone()[0])


def test_failure_retains_existing_snapshot_and_does_not_leak_upstream_text(tmp_path):
    db=Database(tmp_path/'data.sqlite3')
    old={'id':'atcoder:abc100_a','problem_id':'abc100_a','contest_id':'abc100','tags':[],
         'submission_statistics':{'submitted_count':10,'observed_at':1}}
    with db.connect() as conn:conn.execute('insert into problem_catalog values(?,?,?)',('atcoder',old['id'],json.dumps(old)))
    class Failed:
        def __init__(self,**kwargs):pass
        def json(self,*args,**kwargs):raise CrawlError('private cookie text')
        def close(self):pass
    result=refresh_statistics(db,'atcoder',[{'platform':'atcoder','cookie':'synthetic'}],client_factory=Failed)
    assert result['status']=='unavailable' and 'private' not in str(result)
    with db.connect() as conn: assert json.loads(conn.execute('select data from problem_catalog').fetchone()[0])==old


def test_nowcoder_zero_attempts_keep_count_without_fabricating_rate():
    data=nc_fixture();data['data']['data'][0].update(submitCount=0,submitPersonCount=0,acceptedCount=0)
    stat=parse_nowcoder(data,90070,{'nowcoder:278845':{}})['nowcoder:278845']
    assert stat['submitted_count']==0 and stat['acceptance_rate'] is None
    assert stat['limitation']


def test_one_contest_updates_siblings_without_duplicate_page_fetches(tmp_path, monkeypatch):
    from acmweb import submission_statistics as module
    db=Database(tmp_path/'nowcoder.sqlite3')
    rows=[{'id':f'nowcoder:{i}','problem_id':str(i),'platform':'nowcoder','tags':[]} for i in (278845,278846)]
    with db.connect() as conn:
        for row in rows:conn.execute('insert into problem_catalog values(?,?,?)',('nowcoder',row['id'],json.dumps(row)))
    clock=[100]
    monkeypatch.setattr(module.time,'time',lambda:clock[0])
    calls=[]
    class Public:
        def __init__(self,**kwargs):pass
        def get(self,*args):
            calls.append('page');return '<h3>相关比赛</h3><ul><a href="/acm/contest/90070/">比赛</a></ul>'
        def json(self,*args,**kwargs):
            calls.append('stats');clock[0]=110
            data=nc_fixture();data['data']['basicInfo']['problemCount']=2
            data['data']['data'].append({**data['data']['data'][0],'problemId':278846})
            return data
        def close(self):pass
    result=refresh_statistics(db,'nowcoder',[{'platform':'nowcoder','cookie':'synthetic'}],request_budget=8,client_factory=Public)
    assert result['updated']==2 and calls==['page','stats']


def test_foreign_statistics_cannot_be_relabeled_as_official_cf():
    from acmweb.submission_statistics import from_catalog
    assert from_catalog({'platform':'codeforces','contest_statistics':{'complete':True,'source':'https://example.org','submissions':20}}) is None
    assert from_catalog({'platform':'luogu','brief_statistics':None}) is None
    assert statistics(10,source='x',scope='contest',count_kind='all_submissions',observed_at=True) is None


def test_missing_platform_login_does_not_send_requests(tmp_path):
    db=Database(tmp_path/'none.sqlite3')
    def forbidden(**kwargs):raise AssertionError('must not create an authenticated client')
    result=refresh_statistics(db,'nowcoder',client_factory=forbidden)
    assert result['status']=='login_required' and result['requests']==0


def test_workbench_returns_saved_statistics_without_fetching_statement(tmp_path):
    from acmweb.workbench import Workbench
    db=Database(tmp_path/'workbench.sqlite3')
    stat=statistics(40,accepted=10,source='https://www.luogu.com.cn/problem/list',scope='all_time',count_kind='all_submissions')
    with db.connect() as conn:conn.execute('insert into problem_catalog values(?,?,?)',('luogu','luogu:P1001',json.dumps({'submission_statistics':stat})))
    class Accounts:
        def document(self):return {'accounts':[]}
    service=Workbench(db,None,Accounts())
    assert service.state('luogu:P1001')['submission_statistics']==stat
    assert service.state('luogu:P1002')['submission_statistics'] is None


@pytest.mark.parametrize('platform', ['codeforces', 'atcoder', 'luogu', 'nowcoder'])
def test_catalog_rediscovery_preserves_saved_submission_statistics(tmp_path, platform):
    from acmweb.catalog import _store_catalog_rows
    db=Database(tmp_path/(platform+'.sqlite3'))
    pid=platform+':test'
    stat=statistics(10,accepted=4,source='https://official.example',scope='all_time',count_kind='all_submissions')
    _store_catalog_rows(db,platform,[{'id':pid,'platform':platform,'title':'old','tags':[], 'submission_statistics':stat}])
    _store_catalog_rows(db,platform,[{'id':pid,'platform':platform,'title':'new','tags':['dp']}])
    with db.connect() as conn:
        row=json.loads(conn.execute('select data from problem_catalog where id=?',(pid,)).fetchone()[0])
    assert row['submission_statistics']==stat and row['title']=='new' and row['tags']==['dp']

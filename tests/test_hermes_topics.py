"""Synthetic metadata/AI tests: no real OJ requests or code execution."""
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from acmhermes.models import Draw
from acmhermes.topics import parse_topics, matches_topics, method_evidence, context
from acmweb.app import create_app
from acmweb.settings import Settings
from test_hermes_api import setup_pipeline, wait, PID, EXPLANATION, REPORT, ASSESSMENT


def test_alias_trim_dedup_preserves_legacy_request_text():
    text=' dp, DP，动态规划 & luogu-3 '
    request=Draw(score_range=(800,1200),topic=text)
    assert request.topic==text
    assert len(parse_topics(text))==1
    assert parse_topics('博弈论')[0]['id']=='luogu-13'
    assert parse_topics('tree-dp')[0]['id']=='tree-dp'
    assert Draw(score_range=(800,1200),topic='math').model_dump()=={
        'score_range':(800,1200),'topic':'math','exclude_ids':[]}


@pytest.mark.parametrize('query',[' ','dp,','|dp','dp||math','dp&&math','dp，,math',
                                   'dp&(math|games)','（dp）','x'*81,','.join(['dp']*9)])
def test_invalid_expression_rejected(query):
    with pytest.raises(ValueError):
        Draw(score_range=(800,1200),topic=query)


def check(query,tags,**problem):
    return matches_topics({'id':'codeforces:1-A','platform':'codeforces','tags':tags,**problem},parse_topics(query),{})


def test_union_intersection_precedence_and_exact_broad_tags():
    assert check('dp,博弈论',['dp','games'])
    assert not check('dp,博弈论',['dp'])
    assert check('dp|博弈论',['games'])
    assert check('dp|games&math',['dp'])
    assert not check('dp|games&math',['games'])
    assert check('dp|games&math',['games','math'])
    assert not check('tree-dp',['dp'])
    assert check('data structures',['data structures'])
    assert not check('segment-tree',['data structures'])
    assert check('dp|dp&games',['dp'])


def test_method_approval_is_per_topic_not_all_categories():
    topics=parse_topics('tree-dp&博弈论')
    service=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(topic_feasibility=SimpleNamespace(
        approved_map=lambda tid: {'p':{'method':tid}} if tid=='tree-dp' else {}))))
    methods=method_evidence(service,topics)
    assert not matches_topics({'id':'p','platform':'codeforces','tags':[]},topics,methods)
    assert matches_topics({'id':'p','platform':'codeforces','tags':['games']},topics,methods)


@pytest.fixture
def pipeline(tmp_path,monkeypatch):
    app=create_app(Settings(data_dir=tmp_path,config_path=tmp_path/'missing.toml',enable_codex=False,enable_hermes=True))
    s,fake=setup_pipeline(app,monkeypatch)
    with s.db.connect() as db:
        for pid,tags in ((PID,['dp','games']),('codeforces:4-B',['dp']),('codeforces:4-C',['games'])):
            db.execute('INSERT OR REPLACE INTO problem_catalog VALUES(?,?,?)',('codeforces',pid,json.dumps({
                'id':pid,'platform':'codeforces','difficulty':1000,'tags':tags,'title':'Synthetic','contest_id':4})))
    with TestClient(app) as client:
        yield client,s,fake


def test_preview_and_diagnostics_share_expression_and_never_call_model(pipeline):
    client,s,fake=pipeline
    union=client.get('/api/hermes/candidates',params={'minimum':800,'maximum':1200,'topic':'dp|博弈论'}).json()
    both=client.get('/api/hermes/candidates',params={'minimum':800,'maximum':1200,'topic':' dp，博弈论 '}).json()
    assert {p['id'] for p in union['items']}=={PID,'codeforces:4-B','codeforces:4-C'}
    assert [p['id'] for p in both['items']]==[PID]
    assert both['diagnostics']['candidate_count']==both['count']==1
    assert both['topic_query']['operator']=='AND'
    assert union['topic_query']['operator']=='OR'
    assert fake.await_count==0
    assert client.get('/api/hermes/candidates',params={'minimum':800,'maximum':1200,'topic':'dp|'}).status_code==422
    assert client.get('/api/hermes/capabilities').json()['topic_query']['default_operator']=='AND'


def test_generation_context_idempotency_and_exclusions(pipeline,monkeypatch):
    client,s,fake=pipeline
    async def complete(task,provider,model,data,schema):
        assert data['topic']['operator']=='AND'
        assert {t['id'] for t in data['topic']['topics']}=={'luogu-3','luogu-13'}
        assert len(data['topic']['any_of_all'])==1
        if task=='hermes_selection':value={'problem_id':PID,'reason':'Synthetic','suitable':True,'difficulty_assessment':ASSESSMENT}
        else:value={**REPORT,'problem_explanation':EXPLANATION}
        return SimpleNamespace(data=value,provenance={'channel':'api','model':'synthetic','task':task})
    fake.side_effect=complete
    request={'score_range':[800,1200],'topic':'dp&博弈论'}
    headers={'Idempotency-Key':'b'*32}
    first=client.post('/api/hermes/draw',json=request,headers=headers)
    assert first.status_code==200,first.text
    result=wait(client,first.json()['id'])
    assert result['status']=='completed',result
    assert fake.await_count==2
    assert client.post('/api/hermes/draw',json=request,headers=headers).json()['id']==first.json()['id']
    assert fake.await_count==2
    assert not s.pool(Draw(**request))  # Delivered question excluded for subsequent rolls.
    assert not s.pool(Draw(score_range=(800,1200),topic='dp|games',exclude_ids=['codeforces:4-B','codeforces:4-C']))


def test_old_single_topic_request_fingerprint_is_unchanged(pipeline):
    client,s,fake=pipeline
    request={'score_range':[800,1200],'topic':'dp'}
    response=client.post('/api/hermes/draw',json=request,headers={'Idempotency-Key':'c'*32})
    row=s.control.row(response.json()['id'])
    with s.db.connect() as db:
        stored=db.execute('SELECT fingerprint FROM web_job_requests WHERE job_id=?',(row['id'],)).fetchone()[0]
    legacy_payload={'request':request,'provider':'api','model':'synthetic'}
    assert stored==hashlib.sha256(json.dumps(legacy_payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

"""Focused synthetic checks, not real model/OJ acceptance or public user data."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from acmhermes.models import DifficultyAssessment, Draw
from acmhermes.rating_review import metadata_matches, decision
from acmweb.app import create_app
from acmweb.settings import Settings
from test_hermes_api import (client, PID, REPORT, EXPLANATION, STATEMENT,
    add_candidate, set_catalog, generic_complete, start, wait, setup_pipeline)


def test_original_c408ebf6_interval_cannot_make_1300_center_an_1800_task():
    # Exact evidence interval from the reported record, no private payloads.
    assert not metadata_matches({'minimum':900,'maximum':1700},1800,1800)
    assert metadata_matches({'minimum':1450,'maximum':2250},1800,1800)
    assert decision({'rating':1799.9,'confidence':'medium'},1800,1800)=='below_minimum'
    assert decision({'rating':1840.5,'confidence':'medium'},1800,1800)=='accepted'
    assert decision({'rating':None,'confidence':'unknown'},1800,1800)=='undetermined'


@pytest.mark.parametrize('value',[True,-1,4001,float('nan'),float('inf')])
def test_rating_schema_rejects_unusable_values(value):
    with pytest.raises(ValueError):
        DifficultyAssessment(rating=value,confidence='medium',evidence=['Synthetic'],limitations=['AI estimate'])


def prepare_two(s,monkeypatch):
    other='codeforces:7-A'
    add_candidate(s,other)
    for pid in (PID,other):set_catalog(s,pid,difficulty=1800)
    monkeypatch.setattr(s,'choose',lambda pool,*args:(sorted(pool,key=lambda p:p['id'])[0],'synthetic'))
    return other


def test_low_read_time_rating_rerolls_before_report_and_keeps_same_platform(client,monkeypatch):
    c,s,fake=client;other=prepare_two(s,monkeypatch)
    async def complete(task,provider,model,data,schema):
        result=await generic_complete(task,provider,model,data,schema)
        if task=='hermes_selection':
            assert data['statement']['id']==data['candidates'][0]['id']
            assert 'score_evidence' not in data['candidates'][0]  # independent, unanchored estimate
            if result.data['problem_id']==PID:result.data['difficulty_assessment']['rating']=1200.0
        return result
    fake.side_effect=complete
    response=c.post('/api/hermes/draw',json={'score_range':[1800,1800]},headers={'Idempotency-Key':'f'*32})
    job=wait(c,response.json()['id'])
    assert job['status']=='completed',job
    result=job['result']
    assert result['problem']['id']==other
    assert result['difficulty_reviews'][0]['decision']=='below_minimum'
    assert result['ai_difficulty_assessment']['rating']==1800
    assert [call.args[0] for call in fake.await_args_list]==['hermes_selection','hermes_selection','hermes_solution_report']
    assert s.control.memory.drawn_ids()=={other}
    assert c.get('/api/hermes/requests/'+'f'*32).json()['id']==job['id']
    assert fake.await_count==3  # Recovery is not another roll.


def test_full_analysis_can_correct_optimistic_score_and_preserves_rejected_report(client,monkeypatch):
    c,s,fake=client;other=prepare_two(s,monkeypatch)
    async def complete(task,provider,model,data,schema):
        result=await generic_complete(task,provider,model,data,schema)
        if task=='hermes_solution_report' and data['problem_id']==PID:
            result.data['difficulty_assessment']['rating']=1200.0
        return result
    fake.side_effect=complete
    response=c.post('/api/hermes/draw',json={'score_range':[1800,1800]},headers={'Idempotency-Key':'e'*32})
    job=wait(c,response.json()['id'])
    assert job['status']=='completed',job
    assert job['result']['problem']['id']==other
    assert job['result']['difficulty_reviews'][0]['report_decision']=='below_minimum'
    rejected=c.get('/api/problem-reports',params={'problem_id':PID}).json()['items']
    assert rejected[0]['difficulty_assessment']['rating']==1200
    assert job['result']['report_attempts']==2


def test_unknown_scores_never_create_report_and_retry_budget_is_bounded(client,monkeypatch):
    c,s,fake=client
    for i in range(10,16):add_candidate(s,f'codeforces:{i}-A')
    async def complete(task,provider,model,data,schema):
        assert task=='hermes_selection'
        result=await generic_complete(task,provider,model,data,schema)
        result.data['difficulty_assessment'].update(rating=None,confidence='unknown')
        return result
    fake.side_effect=complete
    job=wait(c,start(c))
    assert job['status']=='failed' and '复核次数' in job['error']
    assert fake.await_count==4 and s.control.memory.drawn_ids()==set()


def test_shared_report_reads_no_ai_no_spoiler_in_list_and_statement_mismatch_rejected(client):
    c,s,fake=client
    job=wait(c,start(c));assert job['status']=='completed',job
    rid=job['result']['shared_report_id'];assert rid
    listing=c.get('/api/problem-reports',params={'problem_id':PID}).json()
    assert listing['items'][0]['id']==rid
    assert 'approach' not in json.dumps(listing) and 'evidence' not in json.dumps(listing)
    data=c.get('/api/problem-reports/'+rid,params={'problem_id':PID}).json()
    assert data['report']['approach']==REPORT['approach']
    assert not {'endpoint','provenance','owner','user_id','account','request'} & data.keys()
    assert fake.await_count==2
    assert c.get('/api/problem-reports/'+rid,params={'problem_id':'codeforces:7-A'}).status_code==409
    with s.db.connect() as db:
        db.execute('UPDATE wb_problems SET data=? WHERE id=?',(json.dumps({**STATEMENT,'body_html':'<p>Changed official task</p>'}),PID))
    assert c.get('/api/problem-reports',params={'problem_id':PID}).json()['items']==[]
    assert c.get('/api/problem-reports/'+rid,params={'problem_id':PID}).status_code==409
    assert fake.await_count==2


def test_other_workspace_can_read_and_reuse_public_report_but_not_private_job(client,tmp_path,monkeypatch):
    c,s,fake=client
    first=wait(c,start(c));assert first['status']=='completed'
    rid=first['result']['shared_report_id']
    app=create_app(Settings(data_dir=tmp_path/'other',config_path=tmp_path/'missing.toml',enable_codex=False,
        enable_hermes=True,translation_database=s.app.state.problem_reports.path.with_name('translations.sqlite3')))
    second,ai=setup_pipeline(app,monkeypatch)
    with TestClient(app) as other:
        assert other.get('/api/hermes/draws/'+first['id']).status_code==404
        assert other.get('/api/problem-reports/'+rid,params={'problem_id':PID}).status_code==200
        job=wait(other,start(other,'2'))
        assert job['status']=='completed',job
        assert job['result']['shared_report_id']==rid and job['result']['report_reused'] is True
        assert job['result']['report_attempts']==0 and ai.await_count==1  # Fresh assessment only.
        assert ai.await_args.args[0]=='hermes_selection'


def test_public_save_is_deduplicated_and_cannot_accept_drafts_or_mismatched_problem(client):
    c,s,fake=client
    result=asyncio.run(s.run({'request':{'score_range':[800,1200]},'provider':'api','model':'synthetic'}))
    store=s.app.state.problem_reports;report={**REPORT,'problem_explanation':EXPLANATION}
    fingerprint=s.app.state.selection_reports._cached(PID)[1]
    query={'operator':'AND','topics':[],'any_of_all':[],'notice':'test'}
    rid=store.save(PID,fingerprint,report,provider='api',model='synthetic',topic_query=query)
    assert store.save(PID,fingerprint,report,provider='api',model='synthetic',topic_query=query)==rid
    with pytest.raises(ValueError):store.save(PID,fingerprint,{**report,'draft':'private'},provider='api',model='synthetic',topic_query=query)
    with pytest.raises(ValueError):store.save('codeforces:7-A',fingerprint,report,provider='api',model='synthetic',topic_query=query)


def test_public_storage_io_failure_preserves_paid_private_result(client,monkeypatch):
    import sqlite3
    c,s,fake=client
    def fail(*a,**kw):raise sqlite3.OperationalError('synthetic disk failure')
    monkeypatch.setattr(s.app.state.problem_reports,'save',fail)
    job=wait(c,start(c))
    assert job['status']=='completed' and job['result']['shared_report_status']=='storage_unavailable'
    assert job['result']['report']==REPORT and fake.await_count==2

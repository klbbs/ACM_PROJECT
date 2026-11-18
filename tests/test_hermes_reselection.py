"""Original failure-path regressions; all OJ/model responses here are synthetic."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import time

import pytest

from acmhermes.base_filter import sample, score
from acmhermes.models import Draw
from acmhermes.review_memory import ReviewMemory
from acmweb.codex_bridge import AIError
from acmweb.database import Database
from acmweb.selection_reports import _statement_material
from test_hermes_api import client, PID, STATEMENT, add_candidate, set_catalog, generic_complete, wait


def post(c, key):
    r=c.post('/api/hermes/draw',json={'score_range':[1800,1800]},headers={'Idempotency-Key':key*32})
    assert r.status_code==200,r.text
    return wait(c,r.json()['id'])


def candidates(s, monkeypatch, count=9):
    for n in range(4,4+count):
        pid=f'codeforces:{n}-A'
        if pid!=PID:add_candidate(s,pid)
        set_catalog(s,pid,difficulty=1800)
    monkeypatch.setattr(s,'choose',lambda pool,*args:(sorted(pool,key=lambda p:int(p['contest_id']))[0],'system_random_without_replacement'))


def test_more_than_four_low_scores_continue_without_replacement_and_survive_next_roll(client,monkeypatch):
    c,s,fake=client;candidates(s,monkeypatch)
    selected=[]
    async def complete(task,provider,model,data,schema):
        value=await generic_complete(task,provider,model,data,schema)
        if task=='hermes_selection':
            selected.append(value.data['problem_id'])
            if len(selected)<=5:value.data['difficulty_assessment']['rating']=1200.0
        return value
    fake.side_effect=complete
    first=post(c,'a')
    assert first['status']=='completed',first
    assert selected==[f'codeforces:{n}-A' for n in range(4,10)]
    assert len(selected)==len(set(selected)) and first['result']['problem']['id']=='codeforces:9-A'
    assert first['selection_progress']['ai_review_calls']==6
    assert fake.await_count==7  # Six short reviews, one full report.
    before=fake.await_count
    assert c.get('/api/hermes/requests/'+'a'*32).json()['id']==first['id']
    assert post(c,'a')['id']==first['id'] and fake.await_count==before
    # Reconstructing the memory object doesn't erase persisted rejections.
    s.reviews=ReviewMemory(s.db)
    assert not set(selected[:5]) & {p['id'] for p in s.pool(Draw(score_range=(1800,1800)))}
    second=post(c,'b')
    assert second['status']=='completed' and second['result']['problem']['id']=='codeforces:10-A'
    assert selected[-1]=='codeforces:10-A' and len(selected)==len(set(selected))


def test_full_report_can_reroll_more_than_twice_and_low_report_is_remembered(client,monkeypatch):
    c,s,fake=client;candidates(s,monkeypatch)
    reports=[]
    async def complete(task,provider,model,data,schema):
        value=await generic_complete(task,provider,model,data,schema)
        if task=='hermes_solution_report':
            reports.append(data['problem_id'])
            if len(reports)<=2:value.data['difficulty_assessment']['rating']=1300.0
        return value
    fake.side_effect=complete
    job=post(c,'c')
    assert job['status']=='completed',job
    assert reports==['codeforces:4-A','codeforces:5-A','codeforces:6-A']
    assert job['result']['report_attempts']==3
    assert s.control.memory.drawn_ids()=={'codeforces:6-A'}
    assert not set(reports[:2]) & {p['id'] for p in s.pool(Draw(score_range=(1800,1800)))}
    assert job['selection_progress']['reviews'][0]['report_rating']==1300


def test_failed_budget_keeps_safe_progress_and_rejected_memory(client,monkeypatch):
    import acmhermes.service as module
    c,s,fake=client;candidates(s,monkeypatch)
    monkeypatch.setattr(module,'MAX_DIFFICULTY_REVIEWS',2)
    async def complete(task,provider,model,data,schema):
        value=await generic_complete(task,provider,model,data,schema)
        value.data['difficulty_assessment']['rating']=1200.0
        return value
    fake.side_effect=complete
    job=post(c,'d')
    assert job['status']=='failed' and job['result'] is None
    assert job['error_code']=='hermes_review_budget_exhausted'
    progress=job['selection_progress']
    assert progress['ai_review_calls']==2 and len(progress['reviews'])==2
    assert progress['report_attempts']==0 and s.control.memory.drawn_ids()==set()
    assert not {'evidence','statement','code','cookie','prompt'} & set(progress['reviews'][0])
    assert s.reviews.claimed()==set()


def test_review_invalidated_by_statement_model_range_or_topic_change(client,monkeypatch):
    c,s,fake=client;candidates(s,monkeypatch,1)
    request=Draw(score_range=(1800,1800))
    context=s.review_context(request)
    _,fingerprint=s.app.state.selection_reports._cached(PID)
    choice={'problem_id':PID,'reason':'synthetic','suitable':False,'difficulty_assessment':
        {'rating':1200.0,'confidence':'medium','evidence':['size'],'limitations':['estimate']}}
    s.reviews.save(PID,fingerprint,context,choice)
    assert s.pool(request)==[]
    assert s.review_context(Draw(score_range=(1700,1800)))!=context
    assert s.review_context(Draw(score_range=(1800,1800),topic='math'))!=context
    monkeypatch.setattr(s.ai,'configuration_fingerprint',lambda:'new-config')
    assert s.pool(request)
    monkeypatch.setattr(s.ai,'configuration_fingerprint',lambda:'ephemeral-fingerprint')
    with s.db.connect() as db:
        changed={**STATEMENT,'body_html':'<p>Changed constraint.</p>'}
        db.execute('UPDATE wb_problems SET data=? WHERE id=?',(json.dumps(changed),PID))
    assert s.pool(request)


def test_candidate_claims_are_atomic_expire_and_isolate_users(tmp_path):
    db=Database(tmp_path/'a.sqlite3')
    from acmhermes.memory import DrawMemory
    DrawMemory(db);one=ReviewMemory(db);two=ReviewMemory(db)
    def claim(pair):return pair[0].claim(PID,pair[1],1200)
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(claim,[(one,'one'),(two,'two')]))==[False,True]
    with db.connect() as c: owner=c.execute('SELECT job_id FROM hermes_candidate_claims').fetchone()[0]
    assert one.claimed(owner)==set() and two.claimed('other')=={PID}
    other_db=Database(tmp_path/'b.sqlite3');DrawMemory(other_db);other=ReviewMemory(other_db)
    assert other.claim(PID,'other-user',1200)
    with db.connect() as c:c.execute('UPDATE hermes_candidate_claims SET expires_at=0')
    assert two.claim(PID,'new',1200)
    two.release('new');assert two.claimed()==set()


@pytest.mark.parametrize('source,url,pid',[
    ('luogu_mirror','https://www.luogu.com.cn/problem/CF4A',PID),
    ('luogu_mirror','https://www.luogu.com.cn/problem/CF5A',PID),
    ('luogu_mirror','https://evil.example/problem/CF4A',PID),
    ('luogu_mirror','https://www.luogu.com.cn/problem/CF100001A','codeforces:100001-A'),
    ('untrusted','https://www.luogu.com.cn/problem/CF4A',PID),
])
def test_exact_public_mirror_is_readable_and_forged_mirrors_are_rejected(source,url,pid):
    statement={**STATEMENT,'id':pid,'source':source,'mirror_url':url}
    if source=='luogu_mirror' and url.endswith('/CF4A') and url.startswith('https://www.luogu.com.cn'):
        material,_=_statement_material(statement,pid)
        assert material['source']=='luogu_mirror' and material['mirror_url']==url
    else:
        with pytest.raises(AIError):_statement_material(statement,pid)


def test_original_cf_mirror_path_generates_report_with_clear_source(client):
    c,s,fake=client
    with s.db.connect() as db:
        statement={**STATEMENT,'source':'luogu_mirror','mirror_url':'https://www.luogu.com.cn/problem/CF4A'}
        db.execute('UPDATE wb_problems SET data=? WHERE id=?',(json.dumps(statement),PID))
    jid=c.post('/api/hermes/draw',json={'score_range':[800,1200]},headers={'Idempotency-Key':'e'*32}).json()['id']
    job=wait(c,jid)
    assert job['status']=='completed',job
    assert job['result']['statement_source']=='luogu_mirror'
    assert any('镜像' in x for x in job['result']['report']['limitations'])
    assert fake.await_args_list[0].args[3]['statement']['mirror_url'].endswith('/CF4A')


def stat_problem():
    return {'id':'atcoder:abc100_a','platform':'atcoder','problem_id':'abc100_a','contest_id':'abc100',
        'atcoder_points':500,'atcoder_points_source':'https://kenkoooo.com/atcoder/resources/merged-problems.json',
        'submission_statistics':{'submitted_count':220,'accepted_participants':198,'attempted_participants':200,
            'scope':'contest','rate_unit':'participant','count_kind':'all_contest_submissions','acceptance_rate':.99,
            'observed_at':int(time.time())-1,'contest_id':'abc100',
            'source':'https://atcoder.jp/contests/abc100/standings/json'}}


def test_program_filters_overwhelmingly_easy_samples_and_retains_unknown_honestly():
    p=stat_problem();result=score(p,2000,2000)
    assert not result['eligible'] and result['base_filter']['decision']=='sample_too_easy'
    del p['submission_statistics']
    result=score(p,2000,2000)
    assert result['eligible'] and result['base_filter']['statistics'] is None
    assert result['base_filter']['statistics_status']=='unknown_or_insufficient'


@pytest.mark.parametrize('change',[
    {'rate_unit':'submission'}, {'scope':'all_time'}, {'contest_id':'abc101'},
    {'count_kind':'all_submissions'}, {'acceptance_rate':.5}, {'attempted_participants':True},
    {'source':'https://evil.example'}, {'observed_at':int(time.time())+3600},
])
def test_prefilter_rejects_wrong_source_units_or_denominators(change):
    p=stat_problem();p['submission_statistics'].update(change)
    assert sample(p) is None

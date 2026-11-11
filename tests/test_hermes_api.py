"""Local optional handoff integration. OJ and AI responses here are synthetic."""
import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from acmweb.app import create_app
from acmweb.codex_bridge import AIError
from acmweb.settings import Settings
from acmhermes.models import Draw

PID='codeforces:4-A'
ASSESSMENT={'rating':1000.0,'confidence':'medium','evidence':['合成规模与最简单解法证据'],'limitations':['AI 静态估计，非官方分数']}
def assessment_for(data):
    low,high=data.get('requested_score_range',[800,1200])
    return {**ASSESSMENT,'rating':(low+high)/2}
EXPLANATION={'title':'求整数后继','description':'给定整数 n，求它的后继 n+1。',
             'input_format':'一个整数 n。','output_format':'输出 n+1。','notes':['保留原题的变量与条件。']}
REPORT={'problem_id':PID,'suitable':True,'topic_fit':'合成数学题','summary':'求后继',
        'constraints':['给定整数'],'approach':['加一'],'correctness':['后继的定义'],
        'complexity':'时间 O(1)，空间 O(1)','sample_walkthrough':['1 对应 2'],
        'pitfalls':['检查范围'],'alternative_approaches':[],'limitations':['AI 合成测试，未运行代码'],'difficulty_assessment':ASSESSMENT}
STATEMENT={'id':PID,'url':'https://codeforces.com/problemset/problem/4/A','title':'Synthetic test',
           'source':'official','render_version':4,'body_html':'<p>Find the successor of $$$n$$$.</p>',
           'sections':['Find n+1.'],'samples':[{'input':'1\n','output':'2\n'}]}


def setup_pipeline(app,monkeypatch):
    s=app.state.hermes
    monkeypatch.setattr(s,'public_references',AsyncMock(return_value=[]))
    monkeypatch.setattr(s,'ensure_catalog',AsyncMock(return_value={'platform':'codeforces','status':'cached','count':1}))
    monkeypatch.setattr(s,'refresh_evidence',AsyncMock(return_value={'status':'synthetic'}))
    monkeypatch.setattr(s,'sync_history',AsyncMock(return_value={'complete':True,'accounts':[{'platform':'codeforces','handle':'Owner','complete':True}]}))
    monkeypatch.setattr('acmhermes.service.refresh_recommendation_sessions',AsyncMock())
    monkeypatch.setattr(s.ai,'selection',lambda:{'ready':True,'channel':'api','model':'synthetic'})
    monkeypatch.setattr(s.ai,'configuration_fingerprint',lambda:'ephemeral-fingerprint')
    monkeypatch.setattr(s.accounts.credential_checks,'statuses',lambda:{'items':[{'platform':'codeforces','handle':'Owner','status':'authenticated','login_verified':True}]})
    with s.db.connect() as c:
        c.execute('INSERT INTO problem_catalog VALUES(?,?,?)',('codeforces',PID,json.dumps({
            'id':PID,'platform':'codeforces','difficulty':1000,'tags':['math'],'title':'Synthetic','contest_id':4})))
        c.execute('INSERT INTO wb_problems VALUES(?,?,?)',(PID,json.dumps(STATEMENT),int(time.time())))
    async def complete(task,provider,model,data,schema):
        if task=='hermes_selection':
            constraint=schema.model_json_schema()['properties']['problem_id']
            assert PID in constraint.get('enum',[constraint.get('const')])
            with pytest.raises(ValueError):schema.model_validate({'problem_id':'codeforces:999999-Z','reason':'候选外'})
            value={'problem_id':PID,'reason':'合成选择','suitable':True,'difficulty_assessment':assessment_for(data)}
        elif task=='hermes_solution_report':
            assert data['statement']['id']==PID and 'draft' not in data
            value={**REPORT,'problem_explanation':EXPLANATION,'difficulty_assessment':assessment_for(data)}
        elif task=='translation': value={'blocks':[{'id':b['id'],'text':b['text']} for b in data['blocks']]}
        else: raise AssertionError(task)
        return SimpleNamespace(data=value,provenance={'channel':'api','model':'synthetic','task':task})
    fake=AsyncMock(side_effect=complete)
    monkeypatch.setattr(s.ai,'complete',fake)
    return s,fake


@pytest.fixture
def client(tmp_path,monkeypatch):
    app=create_app(Settings(data_dir=tmp_path,config_path=tmp_path/'missing.toml',enable_codex=False,enable_hermes=True))
    service,fake=setup_pipeline(app,monkeypatch)
    with TestClient(app) as client:
        yield client,service,fake


def wait(client,jid,headers=None):
    for _ in range(100):
        r=client.get('/api/hermes/draws/'+jid,headers=headers or {})
        assert r.status_code==200,r.text
        if r.json()['status'] not in {'queued','running'}:return r.json()
        time.sleep(.01)
    pytest.fail('Synthetic job did not complete')


def test_optional_module_disabled_by_default(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path,enable_codex=False))) as c:
        assert c.get('/api/hermes/capabilities').status_code==404
        assert c.post('/api/hermes/draw',json={'score_range':[800,1200]}).status_code in {404,405}


@pytest.mark.parametrize('value',[{}, {'score_range':[1200,800]}, {'score_range':[True,1200]},
    {'score_range':[800,1200,1400]}, {'score_range':['800',1200]}, {'score_range':[800,1200],'topic':' '},
    {'score_range':[800,1200],'provider':'codex'}])
def test_request_contract_strict_two_parameters(value):
    with pytest.raises(ValueError):Draw.model_validate(value)


def test_full_text_pipeline_and_idempotency(client):
    c,s,fake=client
    headers={'Idempotency-Key':'a'*32}
    r=c.post('/api/hermes/draw',json={'score_range':[800,1200],'topic':'math'},headers=headers)
    assert r.status_code==200,r.text
    job=wait(c,r.json()['id'])
    assert job['status']=='completed',job
    assert job['result']['report']==REPORT
    assert 'statement' not in job['result']
    explanation=job['result']['problem_explanation']
    assert explanation['description']==EXPLANATION['description']
    assert explanation['input_format']==EXPLANATION['input_format']
    assert explanation['samples']==STATEMENT['samples']
    assert explanation['original_url']==STATEMENT['url']
    assert explanation['problem_id']==PID and explanation['language']=='zh-CN'
    assert job['result']['format']=='text'
    assert not {'screenshots','artifact_id','expires_at','translation_provenance'} & job['result'].keys()
    count=fake.await_count
    assert c.get('/api/hermes/draws/'+job['id']).status_code==200
    assert fake.await_count==count
    assert c.get('/api/hermes/requests/'+'a'*32).json()['id']==job['id']
    assert c.post('/api/hermes/draw',json={'score_range':[800,1200],'topic':'math'},headers=headers).json()['id']==job['id']
    assert fake.await_count==count
    assert c.post('/api/hermes/draw',json={'score_range':[900,1300]},headers=headers).status_code==409
    assert c.get('/api/hermes/draws/'+job['id']+'/screenshots/0').status_code==410
    assert c.get('/api/hermes/draws/'+job['id']).json()['result']['report']==REPORT


def test_text_generation_needs_no_browser_or_translation(client,monkeypatch):
    import builtins
    c,s,fake=client
    original=builtins.__import__
    def no_renderer(name,*args,**kwargs):
        if name.startswith(('playwright','markdown_it','acmhermes.render')):
            raise AssertionError('Text generation must not load optional renderer dependencies')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',no_renderer)
    r=c.post('/api/hermes/draw',json={'score_range':[800,1200]},headers={'Idempotency-Key':'b'*32})
    job=wait(c,r.json()['id'])
    assert job['status']=='completed',job
    assert [call.args[0] for call in fake.await_args_list]==['hermes_selection','hermes_solution_report']
    assert s.image_renderer is None
    assert not (s.app.state.settings.data_dir/'hermes-artifacts').exists()
    capabilities=c.get('/api/hermes/capabilities').json()
    assert capabilities['output_format']=='text' and 'screenshot_ready' not in capabilities


def test_empty_candidates_fail_before_paid_ai(client,monkeypatch):
    c,s,fake=client
    monkeypatch.setattr(s,'pool',lambda *a,**kw:[])
    r=c.post('/api/hermes/draw',json={'score_range':[800,1200]},headers={'Idempotency-Key':'b'*32})
    job=wait(c,r.json()['id'])
    assert job['status']=='failed' and '没有符合' in job['error']
    fake.assert_not_awaited()


@pytest.mark.parametrize('invalid',['missing','empty_description','model_rewrites_samples'])
def test_incomplete_problem_explanation_is_not_delivered(client,invalid):
    c,s,fake=client
    original=fake.side_effect
    async def incomplete(task,*args):
        result=await original(task,*args)
        if task=='hermes_solution_report':
            if invalid=='missing':
                result.data={k:v for k,v in result.data.items() if k!='problem_explanation'}
            else:
                result.data={**result.data,'problem_explanation':{**EXPLANATION,
                    **({'description':''} if invalid=='empty_description' else {'samples':[{'input':'fake','output':'fake'}]})}}
        return result
    fake.side_effect=incomplete
    r=c.post('/api/hermes/draw',json={'score_range':[800,1200]},headers={'Idempotency-Key':'f'*32})
    job=wait(c,r.json()['id'])
    assert job['status']=='failed' and job['result'] is None
    assert fake.await_count==2  # No automatic retry or separate translation.


def test_unreadable_full_statement_never_generates_explanation(client,monkeypatch):
    c,s,fake=client
    monkeypatch.setattr(s.app.state.selection_reports,'_read',AsyncMock(side_effect=AIError('官方完整题面读取失败')))
    r=c.post('/api/hermes/draw',json={'score_range':[800,1200]},headers={'Idempotency-Key':'f'*32})
    job=wait(c,r.json()['id'])
    assert job['status']=='failed' and job['result'] is None
    fake.assert_not_awaited()


def test_no_model_or_idempotency_key_never_queues(client,monkeypatch):
    c,s,fake=client
    assert c.post('/api/hermes/draw',json={'score_range':[800,1200]}).status_code==422
    monkeypatch.setattr(s.ai,'selection',lambda:{'ready':False})
    assert c.post('/api/hermes/draw',json={'score_range':[800,1200]},headers={'Idempotency-Key':'b'*32}).status_code==400
    fake.assert_not_awaited()


@pytest.mark.parametrize('failure',['outside','unsuitable','config_change','statement_change'])
def test_failed_validation_never_delivers_mismatched_text(client,monkeypatch,failure):
    c,s,fake=client
    original=fake.side_effect
    async def fail(task,*args):
        result=await original(task,*args)
        if task=='hermes_selection' and failure=='outside':result.data={'problem_id':'codeforces:99-Z','reason':'bad'}
        if task=='hermes_solution_report':
            if failure=='unsuitable':result.data={**result.data,'suitable':False}
            if failure=='config_change':monkeypatch.setattr(s.ai,'configuration_fingerprint',lambda:'changed')
            if failure=='statement_change':
                with s.db.connect() as db:db.execute('UPDATE wb_problems SET data=?',(json.dumps({**STATEMENT,'title':'changed'}),))
        return result
    fake.side_effect=fail
    r=c.post('/api/hermes/draw',json={'score_range':[800,1200]},headers={'Idempotency-Key':'c'*32})
    job=wait(c,r.json()['id'])
    assert job['status']=='failed' and not job['result']
    assert not (s.app.state.settings.data_dir/'hermes-artifacts').exists()


def test_legacy_screenshot_job_keeps_report_without_regeneration(client):
    c,s,fake=client
    jid='e'*32
    old={'report':REPORT,'problem':{'id':PID},'screenshots':['0.png'],'artifact_id':'f'*32,'expires_at':0}
    with s.db.connect() as db:
        db.execute('INSERT INTO web_jobs VALUES(?,?,?,?,?,?,?)',
                   (jid,'hermes_draw','completed',0,0,json.dumps(old),None))
    result=c.get('/api/hermes/draws/'+jid).json()['result']
    assert result['report']==REPORT
    assert result['format']=='legacy_report' and result['problem_explanation'] is None
    assert 'screenshots' not in result and 'artifact_id' not in result
    assert '不会自动重新调用 AI' in result['notice']
    assert c.get('/api/hermes/draws/'+jid+'/screenshots/0').status_code==410
    fake.assert_not_awaited()


def test_multiuser_csrf_and_result_isolation(tmp_path,monkeypatch):
    settings=Settings(data_dir=tmp_path,multi_user=True,enable_codex=False,enable_hermes=True,
        access_token='synthetic-setup-token-with-enough-length',allowed_hosts=('testserver',))
    app=create_app(settings)
    with TestClient(app,base_url='https://testserver') as c:
        h={'Origin':'https://testserver','X-ACM-Request':'1'}
        def auth(path,body):
            r=c.post(path,json=body,headers=h);assert r.status_code==200,r.text
            cookie=r.cookies['acm_user_session'];state=c.get('/api/auth/status').json()
            return {**h,'Cookie':'acm_user_session='+cookie,'X-CSRF-Token':state['csrf_token']},state['user']['id']
        admin,uid=auth('/api/auth/setup',{'username':'owner','password':'Synthetic-password-123','setup_token':settings.access_token})
        invite=c.post('/api/admin/invitations',json={},headers=admin).json()['invitation']
        service,fake=setup_pipeline(app.state.workspaces.apps[uid][0],monkeypatch) if uid in app.state.workspaces.apps else (None,None)
        # Resolve child only via the authenticated public route.
        assert c.get('/api/hermes/capabilities',headers=admin).status_code==200
        if service is None:service,fake=setup_pipeline(app.state.workspaces.apps[uid][0],monkeypatch)
        assert c.post('/api/hermes/draw',json={'score_range':[800,1200]},headers={**admin,'X-CSRF-Token':'bad','Idempotency-Key':'d'*32}).status_code==403
        r=c.post('/api/hermes/draw',json={'score_range':[800,1200]},headers={**admin,'Idempotency-Key':'d'*32})
        job=wait(c,r.json()['id'],admin)
        assert job['status']=='completed',job
        dispatch=service.control.deliveries.claim(job['id'])
        receipt={k:dispatch[k] for k in ('delivery_id','result_sha256')};receipt['received']=True
        ack_path='/api/hermes/draws/'+job['id']+'/delivery/ack'
        stream_path='/api/hermes/draws/'+job['id']+'/delivery/stream'
        assert c.post(ack_path,json=receipt,headers={**admin,'X-CSRF-Token':'bad'}).status_code==403
        assert c.post(stream_path,json={},headers={**admin,'Origin':'https://different.example'}).status_code==403
        assert c.post(ack_path,json=receipt,headers=admin).status_code==200
        # Text and image share delivery memory, so the next image must use a
        # different eligible question rather than delivering the first again.
        next_pid='codeforces:7-A'
        add_candidate(service,next_pid)
        fake.side_effect=generic_complete
        fake_renderer(service)
        r=c.post('/api/hermes/draw/image',json={'score_range':[800,1200]},headers={**admin,'Idempotency-Key':'8'*32})
        image_job=wait(c,r.json()['id'],admin)
        assert image_job['status']=='completed',image_job
        image_url=image_job['result']['screenshots'][0]['url']
        assert c.get(image_url,headers=admin).status_code==200
        assert c.post('/api/hermes/stop',json={},headers={**admin,'X-CSRF-Token':'bad'}).status_code==403
        member,member_uid=auth('/api/auth/register',{'username':'member','password':'Synthetic-password-123','invitation':invite})
        assert c.get('/api/hermes/draws/'+job['id']+'/delivery',headers=member).status_code==404
        assert c.post(ack_path,json=receipt,headers=member).status_code==404
        assert c.post(stream_path,json={},headers=member).status_code==404
        assert c.get('/api/hermes/draws/'+job['id'],headers=member).status_code==404
        assert c.get('/api/hermes/draws/'+job['id']+'/screenshots/0',headers=member).status_code==404
        assert c.get(image_url,headers=member).status_code==404
        assert c.get('/api/hermes/requests/'+'8'*32,headers=member).status_code==404
        assert c.post('/api/hermes/draws/'+image_job['id']+'/cancel',json={},headers=member).status_code==404
        assert c.post('/api/hermes/requests/'+'8'*32+'/cancel',json={},headers=member).status_code==404
        assert c.get('/api/hermes/draws',headers=member).json()['total']==0
        # Public reports cross user boundaries; private jobs still do not.
        setup_pipeline(app.state.workspaces.apps[member_uid][0],monkeypatch)
        report_id=job['result']['shared_report_id']
        path='/api/problem-reports/'+report_id
        shared=c.get(path,params={'problem_id':PID},headers=member)
        assert shared.status_code==200,shared.text
        assert shared.json()['report']['problem_id']==PID
        assert c.get(path,params={'problem_id':PID},headers={'Cookie':''}).status_code==401
        member_memory=c.get('/api/hermes/problem-memory',headers=member).json()
        owner_memory=c.get('/api/hermes/problem-memory',headers=admin).json()
        assert member_memory['items']==[] and member_memory['scope']!=owner_memory['scope']
        assert {i['problem_id'] for i in owner_memory['items']}=={PID,next_pid}
        assert c.post('/api/hermes/stop',json={},headers=member).json()['requested_count']==0
        # Both draw formats share the paid-work quota. Emergency stop remains
        # available when that quota is exhausted (normal auth/CSRF still apply).
        from fastapi import HTTPException
        original_limit=app.state.identities.limit
        def limit(key,*args):
            if key.startswith('cost:'):raise HTTPException(429,'synthetic cost limit')
            return original_limit(key,*args)
        monkeypatch.setattr(app.state.identities,'limit',limit)
        for path in ('/api/hermes/draw','/api/hermes/draw/image'):
            assert c.post(path,json={'score_range':[800,1200]},headers={**admin,'Idempotency-Key':'7'*32}).status_code==429
        assert c.post('/api/hermes/stop',json={},headers=admin).status_code==200
        assert c.get('/api/hermes/capabilities',headers={'Cookie':''}).status_code==401


def start(c,key='1',path='/api/hermes/draw'):
    r=c.post(path,json={'score_range':[800,1200]},headers={'Idempotency-Key':key*32})
    assert r.status_code==200,r.text
    return r.json()['id']


def add_candidate(service,pid):
    with service.db.connect() as db:
        db.execute('INSERT INTO problem_catalog VALUES(?,?,?)',('codeforces',pid,json.dumps({
            'id':pid,'platform':'codeforces','difficulty':1000,'tags':['math'],'title':'Synthetic','contest_id':int(pid.split(':')[1].split('-')[0])})))
        db.execute('INSERT INTO wb_problems VALUES(?,?,?)',(pid,json.dumps({**STATEMENT,'id':pid,
            'url':'https://codeforces.com/problemset/problem/'+pid.split(':')[1].replace('-','/')}),int(time.time())))


async def generic_complete(task,provider,model,data,schema):
    if task=='hermes_selection':
        assert len(data['candidates'])==1  # The model cannot choose a preferred ID.
        value={'problem_id':data['candidates'][0]['id'],'reason':'系统随机题目的训练理由','suitable':True,'difficulty_assessment':assessment_for(data)}
    else:
        value={**REPORT,'problem_id':data['statement']['id'],'problem_explanation':EXPLANATION,'difficulty_assessment':assessment_for(data)}
    return SimpleNamespace(data=schema.model_validate(value).model_dump(),provenance={'channel':'api','model':'synthetic'})


def stopped(c,jid):
    for _ in range(100):
        job=c.get('/api/hermes/draws/'+jid).json()
        if job['control']['stop_state']=='stopped':return job
        time.sleep(.01)
    pytest.fail('Local synthetic task cleanup did not stop')


@pytest.mark.parametrize('stage',['selecting','reading_statement','generating_report'])
def test_cancel_at_each_paid_pipeline_boundary(client,monkeypatch,stage):
    import threading
    c,s,fake=client
    entered=threading.Event()
    original=fake.side_effect
    async def blocked(task,*args):
        if (stage=='selecting' and task=='hermes_selection') or (stage=='generating_report' and task=='hermes_solution_report'):
            entered.set()
            await asyncio.Event().wait()
        return await original(task,*args)
    fake.side_effect=blocked
    if stage=='reading_statement':
        async def read(*args):
            entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(s.app.state.selection_reports,'_read',read)
    jid=start(c)
    assert entered.wait(2)
    assert c.get('/api/hermes/draws/'+jid).json()['control']['stage']==stage
    response=c.post('/api/hermes/draws/'+jid+'/cancel',json={})
    assert response.status_code==200
    job=stopped(c,jid)
    assert job['status']=='interrupted' and job['result'] is None
    assert job['control']['cancel_requested']
    assert job['control']['upstream_cancellation']=='best_effort'
    count=fake.await_count
    assert c.post('/api/hermes/draws/'+jid+'/cancel',json={}).status_code==200
    assert fake.await_count==count
    # A late success/progress writer cannot resurrect a cancelled result.
    s.jobs.update(jid,'completed',{'report':'late secret'})
    assert c.get('/api/hermes/draws/'+jid).json()['result'] is None


def test_queued_cancel_before_coroutine_runs(tmp_path,monkeypatch):
    async def run():
        app=create_app(Settings(data_dir=tmp_path,enable_codex=False,enable_hermes=True))
        s,fake=setup_pipeline(app,monkeypatch)
        job=s.jobs.start('hermes_draw',{'request':{'score_range':[800,1200]},'provider':'api','model':'synthetic'})
        s.control.track(job['id'])
        task=s.jobs.tasks[job['id']][1]
        s.control.cancel(job['id'])
        await asyncio.gather(task,return_exceptions=True)
        await asyncio.sleep(0)
        row=s.job(job['id'])
        assert row['status']=='interrupted' and row['control']['stop_state']=='stopped'
        fake.assert_not_awaited()
        await s.jobs.close()
        s.close()
    asyncio.run(run())


def test_repeated_stop_does_not_cancel_cleanup_or_accept_late_ai(client):
    import threading
    c,s,fake=client
    entered,cleaning=threading.Event(),threading.Event()
    release=asyncio.Event()
    original=fake.side_effect
    async def delayed(task,*args):
        if task=='hermes_selection':
            entered.set()
            try:await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleaning.set()
                await release.wait()  # Simulated upstream ignores cancellation until cleanup.
        return await original(task,*args)
    fake.side_effect=delayed
    jid=start(c)
    assert entered.wait(2)
    task=s.jobs.tasks[jid][1]
    c.post('/api/hermes/requests/'+'1'*32+'/cancel',json={})
    assert cleaning.wait(2)
    assert c.get('/api/hermes/draws/'+jid).json()['control']['stop_state']=='stopping'
    assert c.post('/api/hermes/draws/'+jid+'/cancel',json={}).status_code==200
    task.get_loop().call_soon_threadsafe(release.set)
    job=stopped(c,jid)
    assert job['result'] is None and job['status']=='interrupted'
    assert fake.await_count==1


def test_stop_all_only_affects_own_hermes_jobs_and_not_future_draws(client):
    import threading
    c,s,fake=client
    entered=threading.Event()
    original=fake.side_effect
    async def blocked(*args):
        entered.set()
        await asyncio.Event().wait()
    fake.side_effect=blocked
    with s.db.connect() as db:
        db.execute('INSERT INTO web_jobs VALUES(?,?,?,?,?,?,?)',('9'*32,'crawl','running',0,0,None,None))
    jid=start(c)
    assert entered.wait(2)
    result=c.post('/api/hermes/stop',json={}).json()
    assert result['scope']=='current_user_hermes_jobs' and result['requested_count']==1
    stopped(c,jid)
    with s.db.connect() as db:
        assert db.execute('SELECT status FROM web_jobs WHERE id=?',('9'*32,)).fetchone()[0]=='running'
    fake.side_effect=original
    second=wait(c,start(c,'2'))
    assert second['status']=='completed'
    assert c.post('/api/hermes/draws/'+second['id']+'/cancel',json={}).json()['control']['cancel_requested'] is False
    assert c.get('/api/hermes/draws/'+second['id']).json()['result']['report']==REPORT
    assert c.post('/api/hermes/requests/'+'0'*32+'/cancel',json={}).status_code==404
    assert c.post('/api/hermes/stop',json={'user_id':'other'}).status_code==422


def test_readonly_discovery_progress_list_and_restart_recovery(client,monkeypatch):
    from acmhermes.control import TaskControl
    c,s,fake=client
    refresh=AsyncMock()
    monkeypatch.setattr('acmhermes.service.refresh_recommendation_sessions',refresh)
    assert c.get('/api/hermes/readiness').json()['ai_ready'] is True
    preview=c.get('/api/hermes/candidates',params={'minimum':800,'maximum':1200}).json()
    assert preview['count']==1 and preview['cache_only']
    refresh.assert_not_awaited()
    fake.assert_not_awaited()
    assert c.get('/api/hermes/candidates?minimum=1200&maximum=800').status_code==422
    assert c.get('/api/hermes/draws?limit=101').status_code==422
    job=wait(c,start(c))
    listing=c.get('/api/hermes/draws?status=completed').json()
    assert listing['total']==1 and listing['items'][0]['id']==job['id']
    assert 'result' not in listing['items'][0] and 'error' not in listing['items'][0]
    assert listing['items'][0]['control']['stage']=='completed'
    with s.db.connect() as db:
        db.execute("UPDATE web_jobs SET status='interrupted',result=NULL WHERE id=?",(job['id'],))
        db.execute('UPDATE hermes_task_control SET cancel_requested=1,requested_at=1,stopped_at=NULL WHERE job_id=?',(job['id'],))
    restarted=TaskControl(s.jobs)
    assert restarted.row(job['id'])['control']['stop_state']=='stopped'


def fake_renderer(service,*,failure=False):
    async def render(statement,directory):
        assert statement['samples']==STATEMENT['samples']
        assert REPORT['approach'][0] not in statement['body_html']
        assert EXPLANATION['description'] in statement['body_html']
        directory.mkdir(mode=0o700)
        (directory/'0.png').write_bytes(b'\x89PNG\r\n\x1a\nsynthetic-image')
        return ['0.png'],[]
    service.image_renderer=SimpleNamespace(preflight=AsyncMock(side_effect=AIError('截图组件不可用') if failure else None),render=AsyncMock(side_effect=render))
    return service.image_renderer


def test_image_interface_uses_same_question_and_report_with_private_expiring_png(client):
    c,s,fake=client
    renderer=fake_renderer(s)
    jid=start(c,path='/api/hermes/draw/image')
    job=wait(c,jid)
    assert job['status']=='completed',job
    result=job['result']
    assert result['format']=='image' and result['report']==REPORT
    assert result['problem_explanation']['description']==EXPLANATION['description']
    assert [call.args[0] for call in fake.await_args_list]==['hermes_selection','hermes_solution_report']
    renderer.preflight.assert_awaited_once()
    url=result['screenshots'][0]['url']
    assert c.get(url).content.startswith(b'\x89PNG\r\n\x1a\n')
    assert c.get(url[:-1]+'8').status_code==404
    assert start(c,path='/api/hermes/draw/image')==jid
    assert fake.await_count==2
    assert c.post('/api/hermes/draw',json={'score_range':[800,1200]},headers={'Idempotency-Key':'1'*32}).status_code==409
    s.expire(result['artifact_id'])
    assert c.get(url).status_code==410
    assert c.get('/api/hermes/draws/'+jid).json()['result']['report']==REPORT


def test_image_dependency_failure_does_not_spend_ai(client):
    c,s,fake=client
    fake_renderer(s,failure=True)
    job=wait(c,start(c,path='/api/hermes/draw/image'))
    assert job['status']=='failed' and '截图组件不可用' in job['error']
    fake.assert_not_awaited()


def test_cancelling_renderer_removes_partial_artifact(client):
    import threading
    c,s,fake=client
    entered=threading.Event()
    renderer=fake_renderer(s)
    async def partial(statement,directory):
        directory.mkdir(mode=0o700)
        (directory/'0.png').write_bytes(b'partial')
        entered.set()
        await asyncio.Event().wait()
    renderer.render.side_effect=partial
    jid=start(c,path='/api/hermes/draw/image')
    assert entered.wait(2)
    c.post('/api/hermes/draws/'+jid+'/cancel',json={})
    job=stopped(c,jid)
    assert job['result'] is None
    assert list(s.directory.iterdir())==[]
    assert not s.timers
    assert c.get('/api/hermes/draws/'+jid+'/screenshots/0').status_code==410


def test_history_sync_failure_never_calls_paid_ai(client,monkeypatch):
    c,s,fake=client
    monkeypatch.setattr(s,'sync_history',AsyncMock(side_effect=AIError('历史提交同步未完整，暂不抽题')))
    job=wait(c,start(c))
    assert job['status']=='failed' and '历史提交同步未完整' in job['error']
    fake.assert_not_awaited()


def test_synced_old_solved_question_is_removed_before_ai_selection(client,monkeypatch):
    from ojcrawler.models import Collection,Submission
    from ojcrawler.storage import Store
    c,s,fake=client
    async def sync():
        store=Store(s.db.path)
        try:store.save('synthetic',int(time.time()),Collection('codeforces','Owner',
            submissions=[Submission('old','4-a',1,'AC')],complete=True),capture_snapshot=False)
        finally:store.close()
        return {'complete':True,'accounts':[{'platform':'codeforces','handle':'Owner','complete':True}]}
    monkeypatch.setattr(s,'sync_history',sync)
    job=wait(c,start(c))
    assert job['status']=='failed' and '没有符合' in job['error']
    fake.assert_not_awaited()


def test_account_changes_during_ai_do_not_deliver_unsynced_identity(client,monkeypatch):
    c,s,fake=client
    original=fake.side_effect
    async def change(task,*args):
        result=await original(task,*args)
        monkeypatch.setattr(s.accounts,'revision',lambda:'new-account')
        return result
    fake.side_effect=change
    job=wait(c,start(c))
    assert job['status']=='failed' and '平台配置已变化' in job['error'] and not job['result']


def test_history_stage_can_be_cancelled_before_any_ai(client,monkeypatch):
    import threading
    c,s,fake=client
    entered=threading.Event()
    async def sync():
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(s,'sync_history',sync)
    jid=start(c)
    assert entered.wait(2)
    assert c.get('/api/hermes/draws/'+jid).json()['control']['stage']=='syncing_submissions'
    c.post('/api/hermes/draws/'+jid+'/cancel',json={})
    assert stopped(c,jid)['result'] is None
    fake.assert_not_awaited()


def test_new_draw_after_external_submission_cannot_repeat_the_solved_problem(client,monkeypatch):
    from ojcrawler.models import Collection,Submission
    from ojcrawler.storage import Store
    c,s,fake=client
    first=wait(c,start(c))
    assert first['status']=='completed' and first['result']['problem']['id']==PID
    other='codeforces:7-A'
    with s.db.connect() as db:
        db.execute('INSERT INTO problem_catalog VALUES(?,?,?)',('codeforces',other,json.dumps({
            'id':other,'platform':'codeforces','difficulty':1000,'tags':['math'],'title':'Next synthetic','contest_id':7})))
        db.execute('INSERT INTO wb_problems VALUES(?,?,?)',(other,json.dumps({**STATEMENT,'id':other,'url':'https://codeforces.com/problemset/problem/7/A'}),int(time.time())))
    async def sync():
        store=Store(s.db.path)
        try:store.save('after-external-ac',int(time.time()),Collection('codeforces','Owner',
            submissions=[Submission('old','4-A',1,'AC')],complete=True),capture_snapshot=False)
        finally:store.close()
        return {'complete':True,'accounts':[{'platform':'codeforces','handle':'Owner','complete':True}]}
    async def choose_new(task,provider,model,data,schema):
        if task=='hermes_selection':
            assert {p['id'] for p in data['candidates']}=={other}
            value={'problem_id':other,'reason':'只选未做题','suitable':True,'difficulty_assessment':assessment_for(data)}
        else:
            assert data['statement']['id']==other
            value={**REPORT,'problem_id':other,'problem_explanation':EXPLANATION}
        return SimpleNamespace(data=schema.model_validate(value).model_dump(),provenance={'channel':'api','model':'synthetic'})
    monkeypatch.setattr(s,'sync_history',sync)
    fake.side_effect=choose_new
    second=wait(c,start(c,'2'))
    assert second['status']=='completed',second
    assert second['result']['problem']['id']==other
    assert second['result']['history_sync']['complete']


def test_newly_verified_platform_cannot_enter_pool_without_history_sync(client,monkeypatch):
    c,s,fake=client
    rows=[{'platform':'codeforces','handle':'Owner','status':'authenticated','login_verified':True}]
    monkeypatch.setattr(s.accounts.credential_checks,'statuses',lambda:{'items':list(rows)})
    async def sync():
        rows.append({'platform':'atcoder','handle':'Other','status':'authenticated','login_verified':True})
        return {'complete':True,'accounts':[{'platform':'codeforces','handle':'Owner','complete':True}]}
    monkeypatch.setattr(s,'sync_history',sync)
    job=wait(c,start(c))
    assert job['status']=='failed' and '新验证账号尚未同步提交' in job['error']
    fake.assert_not_awaited()


def test_consecutive_rolls_without_submissions_never_repeat_even_across_image_and_text(client,monkeypatch):
    c,s,fake=client
    add_candidate(s,'codeforces:7-A')
    fake.side_effect=generic_complete
    sampled=[]
    def pick(pool):
        sampled.append({p['id'] for p in pool})
        return min(pool,key=lambda p:p['id'])
    monkeypatch.setattr('acmhermes.service.secrets.choice',pick)
    first=wait(c,start(c,'1'))
    assert first['status']=='completed',first
    # No official submission was created; delivery memory alone removes it.
    fake_renderer(s)
    second=wait(c,start(c,'2',path='/api/hermes/draw/image'))
    assert second['status']=='completed',second
    a,b=first['result']['problem']['id'],second['result']['problem']['id']
    assert a!=b and sampled==[{PID,'codeforces:7-A'},{b}]
    assert second['result']['selection_strategy']=='system_random_without_replacement'
    assert s.control.memory.drawn_ids()=={a,b}
    third=wait(c,start(c,'3'))
    assert third['status']=='failed' and '未推荐题' in third['error']
    assert fake.await_count==4  # No paid re-roll when the pool is exhausted.
    # Recovering the same idempotency key is deliberately the same old job.
    assert start(c,'1')==first['id']


def test_client_exclusions_are_checked_before_paid_ai_and_exposed_via_memory(client):
    c,s,fake=client
    r=c.post('/api/hermes/draw',json={'score_range':[800,1200],
        'exclude_ids':['https://codeforces.com/contest/4/problem/a']},headers={'Idempotency-Key':'1'*32})
    assert r.status_code==200,r.text
    job=wait(c,r.json()['id'])
    assert job['status']=='failed' and '客户端记录' in job['error']
    fake.assert_not_awaited()
    assert s.control.memory.drawn_ids()==set()  # Client assertion is not AC evidence.
    assert c.get('/api/hermes/problem-memory').json()['items']==[]
    assert c.post('/api/hermes/draw',json={'score_range':[800,1200],'exclude_ids':['not-a-problem']},
        headers={'Idempotency-Key':'2'*32}).status_code==422


def test_draw_memory_survives_process_reconstruction_and_get_cannot_spend_ai(client):
    from acmhermes.memory import DrawMemory
    c,s,fake=client
    first=wait(c,start(c))
    assert first['status']=='completed'
    count=fake.await_count
    # Simulate the new process's memory migration over the same private DB.
    s.control.memory=DrawMemory(s.db)
    assert s.pool(Draw(score_range=(800,1200)))==[]
    result=c.get('/api/hermes/problem-memory?limit=1').json()
    assert result['items']==[{'problem_id':PID,'drawn':True,'attempted':False,'solved':False,'skipped':False}]
    assert fake.await_count==count
    assert c.get('/api/hermes/problem-memory?revision='+'0'*64).status_code==409


def test_failed_or_cancelled_task_does_not_consume_delivered_question(client,monkeypatch):
    c,s,fake=client
    original=fake.side_effect
    async def fail(task,*args):
        if task=='hermes_solution_report':raise AIError('合成报告失败')
        return await original(task,*args)
    fake.side_effect=fail
    assert wait(c,start(c))['status']=='failed'
    assert s.control.memory.drawn_ids()==set()
    fake.side_effect=original
    second=wait(c,start(c,'2'))
    assert second['status']=='completed',second


def test_catalog_failure_stops_before_model_and_reports_sync_stage(client,monkeypatch):
    c,s,fake=client
    monkeypatch.setattr(s,'ensure_catalog',AsyncMock(side_effect=AIError('Codeforces 官方题库同步未完成')))
    job=wait(c,start(c))
    assert job['status']=='failed' and '故障平台已跳过' in job['error']
    fake.assert_not_awaited()


def test_model_cannot_override_system_random_id_with_another_eligible_problem(client,monkeypatch):
    c,s,fake=client
    add_candidate(s,'codeforces:7-A')
    monkeypatch.setattr('acmhermes.service.secrets.choice',lambda pool:next(p for p in pool if p['id']==PID))
    async def malicious(task,*args):
        assert task=='hermes_selection'
        return SimpleNamespace(data={'problem_id':'codeforces:7-A','reason':'我偏好另一题'})
    fake.side_effect=malicious
    job=wait(c,start(c))
    assert job['status']=='failed' and '随机抽取不一致' in job['error']
    assert fake.await_count==1 and s.control.memory.drawn_ids()==set()


def aggregate(population=1000, accepted=300):
    return {'scope':'contest','source':'codeforces.contest.standings',
            'observed_at':int(time.time()),'participants':population,'accepted_participants':accepted}


def set_catalog(s, pid, **fields):
    with s.db.connect() as db:
        old=db.execute('SELECT data FROM problem_catalog WHERE id=?',(pid,)).fetchone()
        value=json.loads(old[0]) if old else {'id':pid,'platform':'codeforces','title':'Synthetic',
            'contest_id':int(pid.split(':')[1].split('-')[0]),'tags':['math']}
        value.update(fields)
        db.execute('INSERT OR REPLACE INTO problem_catalog VALUES(?,?,?)',('codeforces',pid,json.dumps(value)))


def test_roll_rejects_high_pass_rate_even_with_matching_official_rating(client):
    c,s,fake=client
    set_catalog(s,PID,difficulty=1800,contest_statistics=aggregate(1000,850))
    assert s.pool(Draw(score_range=(1800,1800)))==[]
    r=c.post('/api/hermes/draw',json={'score_range':[1800,1800]},headers={'Idempotency-Key':'a'*32})
    result=wait(c,r.json()['id'])
    assert result['status']=='failed'
    fake.assert_not_awaited()


def test_roll_uses_combined_rating_instead_of_prefiltering_by_official_label(client):
    _,s,_=client
    set_catalog(s,PID,difficulty=1600,contest_statistics=aggregate())
    pool=s.pool(Draw(score_range=(1800,1800)))
    assert [p['id'] for p in pool]==[PID]
    assert pool[0]['score_evidence']['estimated_rating']==pytest.approx(1746.24)
    assert pool[0]['score_evidence']['official_rating']==1600


def test_roll_unrated_gym_requires_evidence_and_respects_date_and_memory(client):
    _,s,_=client
    gym='codeforces:100001-A'
    set_catalog(s,gym,difficulty=None,is_gym=True,contest_statistics=aggregate(),
        published_at=1600000000,published_at_source='codeforces.contest.startTimeSeconds')
    request=Draw(score_range=(1900,1900))
    pool=s.pool(request)
    assert [p['id'] for p in pool]==[gym]
    assert pool[0]['score_evidence']['gym_bonus']>0
    assert s.pool(Draw(score_range=(1900,1900),exclude_ids=[gym]))==[]
    s.db.set('training',{'published_after':'2026-01-01'})
    assert s.pool(request)==[]
    s.db.set('training',{})
    set_catalog(s,gym,contest_statistics=None)
    assert s.pool(request)==[]


def test_weighted_os_random_uses_gym_bonus_without_changing_rating(monkeypatch):
    from acmhermes.difficulty import score_candidate
    from acmhermes.service import Hermes
    first={'platform':'codeforces','id':'codeforces:10-A','difficulty':1800,
           'contest_statistics':aggregate()}
    second={**first,'id':'codeforces:100001-A','is_gym':True}
    pool=[{**p,'score_evidence':score_candidate(p,1900,1900)} for p in (first,second)]
    bounds=[]
    def draw(total):
        bounds.append(total)
        return total-1
    monkeypatch.setattr('acmhermes.service.secrets.randbelow',draw)
    chosen,strategy=Hermes.choose(pool)
    assert chosen['id']==second['id'] and strategy=='system_weighted_random_without_replacement'
    weights=[round(1000*(1+p['score_evidence']['priority_score'])) for p in pool]
    assert weights[1]>weights[0] and bounds==[sum(weights)]
    assert pool[0]['score_evidence']['estimated_rating']==pool[1]['score_evidence']['estimated_rating']


def test_preview_stays_readonly_and_exposes_explainable_policy(client):
    c,s,_=client
    result=c.get('/api/hermes/capabilities').json()
    assert result['difficulty_policy']['empirical_weight']>.5
    assert c.get('/api/hermes/candidates?minimum=800&maximum=1200').status_code==200
    s.refresh_evidence.assert_not_awaited()


def test_finite_statistics_sample_keeps_unknown_official_candidates_but_not_proven_easy(client):
    c,s,fake=client
    set_catalog(s,PID,difficulty=1800,contest_statistics=aggregate(1000,850))
    add_candidate(s,'codeforces:7-A')
    set_catalog(s,'codeforces:7-A',difficulty=1800)
    fake.side_effect=generic_complete
    s.refresh_evidence.return_value={'status':'available','contests_available':1,
        'checked_contest_ids':[4,7],'available_contest_ids':[4]}
    response=c.post('/api/hermes/draw',json={'score_range':[1800,1800]},headers={'Idempotency-Key':'b'*32})
    job=wait(c,response.json()['id'])
    assert job['status']=='completed',job
    assert job['result']['problem']['id']=='codeforces:7-A'
    assert job['result']['problem']['score_evidence']['kind']=='official'


def test_discovery_reserves_space_for_other_official_ratings(client):
    _,s,_=client
    for i in range(10,90):set_catalog(s,f'codeforces:{i}-A',difficulty=1800)
    for i in range(90,100):set_catalog(s,f'codeforces:{i}-A',difficulty=1600)
    pool=s.pool(Draw(score_range=(1800,1800)),evidence_only=True)
    assert sum(p['difficulty']==1800 for p in pool)==40
    assert any(p['difficulty']==1600 for p in pool)
    assert len(pool)<=60


def test_checked_contest_filter_happens_before_random_pool_limit(client,monkeypatch):
    c,s,_=client
    set_catalog(s,PID,difficulty=1800,contest_statistics=aggregate(1000,350))
    for i in range(10,90):set_catalog(s,f'codeforces:{i}-A',difficulty=1800)
    s.refresh_evidence.return_value={'contests_available':1,'available_contest_ids':[4]}
    pool=s.pool(Draw(score_range=(1800,1800)),qualified_contests={4})
    assert [p['id'] for p in pool]==[PID]
    # The production pool now permits unsampled official ratings. Keep this
    # synthetic statement in the random preview instead of flakily losing it.
    original_pool=s.pool
    monkeypatch.setattr(s,'pool',lambda request,preferred_ids=None,**kwargs:original_pool(request,[PID],**kwargs))
    monkeypatch.setattr(s,'choose',lambda pool,*args:(next(p for p in pool if p['id']==PID),'synthetic'))
    response=c.post('/api/hermes/draw',json={'score_range':[1800,1800]},headers={'Idempotency-Key':'c'*32})
    job=wait(c,response.json()['id'])
    assert job['status']=='completed',job
    assert job['result']['problem']['id']==PID


@pytest.mark.parametrize('slow_phase',['catalog','statistics'])
def test_draw_renews_real_login_evidence_at_candidate_boundaries(client,monkeypatch,slow_phase):
    from acmweb import catalog
    from acmweb.credential_check import CredentialChecks
    c,s,fake=client
    clock=[int(time.time())]
    monkeypatch.setattr(catalog.time,'time',lambda:clock[0])
    monkeypatch.setattr(s.accounts,'document',lambda:{'accounts':[
        {'platform':'codeforces','handle':'Owner','cookie':'session=SYNTHETIC'}]})
    checker=CredentialChecks(s.accounts)
    monkeypatch.setattr(s.accounts,'credential_checks',checker)
    _,digest=checker.credential('codeforces','Owner')
    checker.cache[('codeforces','Owner')]=(digest,{'platform':'codeforces','handle':'Owner',
        'status':'authenticated','login_verified':True,'checked_at':clock[0]-10})
    calls=[]
    class Response:
        status_code=200;headers={};encoding='utf-8'
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def iter_content(self,chunk_size):
            yield (b'<div class="lang-chooser"><a href="/profile/Owner">Owner</a>'
                   b'<a href="/logout">logout</a></div><form action="/settings/general"></form>')
    class Session:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,*args,**kwargs):
            calls.append(clock[0]);return Response()
    monkeypatch.setattr('acmweb.credential_check.requests.Session',Session)
    monkeypatch.setattr('acmhermes.service.refresh_recommendation_sessions',catalog.refresh_recommendation_sessions)
    async def refresh_catalog():
        if slow_phase=='catalog':clock[0]+=291
        return {'status':'synthetic'}
    async def statistics(request):
        assert s.pool(request,evidence_only=True)
        if slow_phase=='statistics':clock[0]+=291
        return {'status':'synthetic'}
    monkeypatch.setattr(s,'ensure_catalog',refresh_catalog)
    monkeypatch.setattr(s,'refresh_evidence',statistics)
    fake_renderer(s)
    job=wait(c,start(c,path='/api/hermes/draw/image'))
    assert job['status']=='completed',job
    assert len(calls)==1 and checker.statuses()['items'][0]['checked_at']==clock[0]
    assert fake.await_count==2


def test_draw_unverified_login_stops_before_model_without_looping_checks(client,monkeypatch,tmp_path):
    from acmweb.runtime_logs import RuntimeLogs
    c,s,fake=client
    monkeypatch.setattr(s.accounts.credential_checks,'statuses',lambda:{'items':[]})
    store=RuntimeLogs(tmp_path/'events')
    monkeypatch.setattr(s.app.state,'event_log',store)
    job=wait(c,start(c))
    assert job['status']=='failed' and '没有已验证登录的平台' in job['error']
    fake.assert_not_awaited()
    s.sync_history.assert_not_awaited()
    events=store.listing()['items']
    checked=[e for e in events if e['event']=='hermes.sessions_checked']
    assert len(checked)==1
    assert checked[0]['details']['session_stage']=='before_history'
    assert checked[0]['details']['verified_count']==0


def test_platform_renewed_before_evidence_requires_its_history_to_be_synced(client,monkeypatch):
    c,s,fake=client
    rows=[{'platform':'codeforces','handle':'Owner','status':'authenticated','login_verified':True}]
    monkeypatch.setattr(s.accounts.credential_checks,'statuses',lambda:{'items':list(rows)})
    calls=[]
    async def renew(*args,**kwargs):
        calls.append(1)
        if len(calls)==3:
            rows.append({'platform':'atcoder','handle':'Other','status':'authenticated','login_verified':True})
        return {}
    monkeypatch.setattr('acmhermes.service.refresh_recommendation_sessions',renew)
    job=wait(c,start(c))
    assert job['status']=='failed' and '新验证账号尚未同步提交' in job['error']
    s.refresh_evidence.assert_not_awaited()
    fake.assert_not_awaited()


def test_cancel_during_last_login_refresh_never_starts_paid_model(client,monkeypatch):
    import threading
    c,s,fake=client
    calls=[];entered=threading.Event()
    async def renew(*args,**kwargs):
        calls.append(1)
        if len(calls)==4:
            entered.set()
            await asyncio.Event().wait()
        return {}
    monkeypatch.setattr('acmhermes.service.refresh_recommendation_sessions',renew)
    jid=start(c)
    assert entered.wait(2)
    assert c.post('/api/hermes/draws/'+jid+'/cancel',json={}).status_code==200
    job=stopped(c,jid)
    assert job['status']=='interrupted'
    fake.assert_not_awaited()


def test_delivery_stream_repeats_saved_result_until_explicit_receipt(client,monkeypatch):
    c,s,fake=client
    job=wait(c,start(c))
    jid=job['id'];calls=fake.await_count
    assert c.get(f'/api/hermes/draws/{jid}/delivery').json()['state']=='pending'
    monkeypatch.setattr('acmhermes.delivery.ACK_TIMEOUT',.03)
    monkeypatch.setattr('acmhermes.delivery.POLL_INTERVAL',.001)
    r=c.post(f'/api/hermes/draws/{jid}/delivery/stream',json={})
    assert r.status_code==200 and r.headers['cache-control']=='no-store'
    events=[json.loads(line) for line in r.text.splitlines()]
    results=[e for e in events if e['type']=='result']
    assert len(results)==3 and events[-1]['type']=='pending'
    assert len({e['result_json'] for e in results})==1
    assert len({e['delivery_id'] for e in results})==1
    assert json.loads(results[0]['result_json'])['report']==job['result']['report']
    payload={k:results[0][k] for k in ('delivery_id','result_sha256')};payload['received']=True
    receipt=c.post(f'/api/hermes/draws/{jid}/delivery/ack',json=payload).json()
    assert receipt['state']=='acknowledged'
    assert c.post(f'/api/hermes/draws/{jid}/delivery/ack',json=payload).json()==receipt
    response=c.post(f'/api/hermes/draws/{jid}/delivery/stream',json={})
    assert [json.loads(line)['type'] for line in response.text.splitlines()]==['acknowledged']
    assert fake.await_count==calls
    assert s.control.memory.drawn_ids()=={PID}


def test_ack_during_open_stream_stops_retransmission(client,monkeypatch):
    import threading
    c,s,_=client;job=wait(c,start(c));jid=job['id']
    monkeypatch.setattr('acmhermes.delivery.ACK_TIMEOUT',.5)
    monkeypatch.setattr('acmhermes.delivery.POLL_INTERVAL',.001)
    seen=threading.Event();messages=[];responses=[]
    original=s.control.deliveries.claim
    def claimed(value):
        message=original(value)
        if message['type']=='result':messages.append(message);seen.set()
        return message
    monkeypatch.setattr(s.control.deliveries,'claim',claimed)
    def confirm():
        assert seen.wait(3)
        payload={k:messages[0][k] for k in ('delivery_id','result_sha256')};payload['received']=True
        responses.append(c.post(f'/api/hermes/draws/{jid}/delivery/ack',json=payload).status_code)
    thread=threading.Thread(target=confirm);thread.start()
    response=c.post(f'/api/hermes/draws/{jid}/delivery/stream',json={})
    thread.join(3)
    assert responses==[200]
    assert json.loads(response.text.splitlines()[-1])['type']=='acknowledged'
    assert s.control.deliveries.claim(jid)['type']=='acknowledged'


def test_delivery_creation_rolls_back_with_failed_exclusion_memory(client,monkeypatch):
    c,s,fake=client
    monkeypatch.setattr(s.control.memory,'record',lambda *args:(_ for _ in ()).throw(AIError('synthetic conflict')))
    job=wait(c,start(c))
    assert job['status']=='failed'
    with s.db.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM hermes_deliveries').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM hermes_drawn_problems').fetchone()[0]==0
    assert c.post(f"/api/hermes/draws/{job['id']}/delivery/stream",json={}).status_code==409


def add_second_platform(s,monkeypatch):
    from acmweb.difficulty_estimates import ATCODER_POINTS
    pid='atcoder:abc400_a'
    rows=[{'platform':p,'handle':h,'status':'authenticated','login_verified':True}
          for p,h in [('codeforces','Owner'),('atcoder','Other')]]
    monkeypatch.setattr(s.accounts.credential_checks,'statuses',lambda:{'items':list(rows)})
    s.sync_history.return_value={'complete':True,'accounts':[
        {'platform':p,'handle':h,'complete':True} for p,h in [('codeforces','Owner'),('atcoder','Other')]]}
    with s.db.connect() as db:
        db.execute('INSERT INTO problem_catalog VALUES(?,?,?)',('atcoder',pid,json.dumps({
            'id':pid,'problem_id':'abc400_a','platform':'atcoder','tags':['math'],'title':'Other synthetic',
            'atcoder_points':200,'atcoder_points_source':ATCODER_POINTS})))
        db.execute('INSERT INTO wb_problems VALUES(?,?,?)',(pid,json.dumps({**STATEMENT,'id':pid,
            'url':'https://atcoder.jp/contests/abc400/tasks/abc400_a'}),int(time.time())))
    return pid


@pytest.mark.parametrize('stage',['history','catalog','statement','unsuitable'])
def test_platform_failure_falls_back_without_losing_healthy_platform(client,monkeypatch,stage):
    c,s,fake=client;other=add_second_platform(s,monkeypatch)
    fake.side_effect=generic_complete
    monkeypatch.setattr(s,'choose',lambda pool,*args:(next((p for p in pool if p['platform']=='codeforces'),pool[0]),'synthetic'))
    if stage=='history':
        s.sync_history.return_value={'complete':False,'accounts':[
            {'platform':'codeforces','handle':'Owner','complete':False},
            {'platform':'atcoder','handle':'Other','complete':True}]}
    elif stage=='catalog':
        s.ensure_catalog.side_effect=AIError('synthetic OJ unavailable')
    elif stage=='statement':
        original=s.app.state.selection_reports._read
        async def read(pid):
            if pid==PID:raise AIError('synthetic OJ unavailable')
            return await original(pid)
        monkeypatch.setattr(s.app.state.selection_reports,'_read',read)
    else:
        async def reject_one(task,provider,model,payload,schema):
            result=await generic_complete(task,provider,model,payload,schema)
            if task=='hermes_solution_report' and payload['problem_id']==PID:result.data['suitable']=False
            return result
        fake.side_effect=reject_one
    job=wait(c,start(c))
    assert job['status']=='completed',job
    assert job['result']['problem']['id']==other
    if stage=='unsuitable':
        assert job['result']['difficulty_reviews'][0]['report_decision']=='unsuitable'
    else:
        assert job['result']['platform_fallbacks'][0]['platform']=='codeforces'
    assert fake.await_count==(4 if stage=='unsuitable' else 2)
    assert s.control.memory.drawn_ids()=={other}
    with s.db.connect() as db:assert db.execute('SELECT COUNT(*) FROM hermes_deliveries').fetchone()[0]==1


def test_shared_model_error_does_not_retry_paid_work_on_different_oj(client,monkeypatch):
    c,s,fake=client;add_second_platform(s,monkeypatch)
    async def fail(task,*args):
        if task=='hermes_solution_report':raise AIError('synthetic shared AI outage')
        return await generic_complete(task,*args)
    fake.side_effect=fail
    job=wait(c,start(c))
    assert job['status']=='failed' and fake.await_count==2
    assert not s.control.memory.drawn_ids()


def test_unsuitable_reports_stop_without_committing_result(client,monkeypatch):
    c,s,fake=client;add_second_platform(s,monkeypatch)
    async def reject(task,*args):
        result=await generic_complete(task,*args)
        if task=='hermes_solution_report':result.data['suitable']=False
        return result
    fake.side_effect=reject
    job=wait(c,start(c))
    assert job['status']=='failed' and fake.await_count==4
    assert not s.control.memory.drawn_ids()
    with s.db.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM hermes_deliveries').fetchone()[0]==0


def test_platform_sampling_is_not_proportional_to_candidate_count(monkeypatch):
    from acmhermes.service import Hermes
    pool=[{'id':str(i),'platform':'atcoder','score_evidence':{}} for i in range(59)]
    pool.append({'id':'cf','platform':'codeforces','score_evidence':{'priority_score':2}})
    bounds=[]
    def random(total):bounds.append(total);return total-1
    monkeypatch.setattr('acmhermes.service.secrets.randbelow',random)
    chosen,strategy=Hermes.choose(pool)
    assert chosen['platform']=='codeforces' and bounds==[2000]
    assert strategy=='system_platform_balanced_random_without_replacement'
    weights=Hermes.platform_weights(pool,['atcoder']*8)
    assert weights['atcoder']<weights['codeforces']
    assert Hermes.platform_weights(pool,[])=={'atcoder':1000,'codeforces':1000}


def test_target_neighbourhood_is_used_for_preview_report_and_result(client):
    c,s,fake=client
    original=fake.side_effect
    async def inspect(task,provider,model,data,schema):
        assert data['score_range']==[850,1050]
        assert data['requested_score_range']==[950,950]
        return await original(task,provider,model,data,schema)
    fake.side_effect=inspect
    preview=c.get('/api/hermes/candidates?minimum=950&maximum=950').json()
    assert preview['effective_score_range']==[850,1050] and preview['count']==1
    assert preview['diagnostics']['candidate_count']==1
    response=c.post('/api/hermes/draw',json={'score_range':[950,950]},headers={'Idempotency-Key':'9'*32})
    assert response.status_code==200
    job=wait(c,response.json()['id'])
    assert job['status']=='completed',job
    assert job['result']['effective_score_range']==[850,1050]
    assert job['result']['problem']['score_evidence']['estimated_rating']==1000


def test_coverage_counts_all_candidates_not_only_the_60_preview(client):
    c,s,_=client
    for n in range(5,79):set_catalog(s,f'codeforces:{n}-A',difficulty=1000,tags=['math'])
    data=c.get('/api/hermes/candidates?minimum=1000&maximum=1000&topic=math').json()
    diagnostic=data['diagnostics']
    assert data['count']==60 and diagnostic['candidate_count']==75
    cf=next(r for r in diagnostic['platforms'] if r['platform']=='codeforces')
    assert cf['cached_count']==cf['eligible_count']==75
    assert sum(cf['filter_counts'].values())==0


def test_diagnostics_match_original_date_topic_and_exclusion_path(client):
    from acmhermes.models import Draw
    _,s,_=client
    recent={'published_at':1760000000,'published_at_source':'codeforces.contest.startTimeSeconds','tags':['games']}
    s.db.set('training',{'published_after':'2022-10-14'})
    set_catalog(s,PID,difficulty=1800,tags=['games'],published_at=1600000000,published_at_source='codeforces.contest.startTimeSeconds')
    set_catalog(s,'codeforces:5-A',difficulty=1700,**recent)
    set_catalog(s,'codeforces:6-A',difficulty=1900,**recent)
    set_catalog(s,'codeforces:7-A',difficulty=2200,**recent)
    request=Draw(score_range=(1800,1800),topic='luogu-13',exclude_ids=['codeforces:6-A'])
    found=s.pool(request);d=s.diagnostics(request);cf=d['platforms'][0]
    assert [r['id'] for r in found]==['codeforces:5-A']
    assert d['candidate_count']==1
    assert cf['filter_counts']['before_date']==1 and cf['filter_counts']['difficulty']==1
    assert cf['filter_counts']['client_or_drawn']==1
    assert cf['cached_count']==cf['eligible_count']+sum(cf['filter_counts'].values())

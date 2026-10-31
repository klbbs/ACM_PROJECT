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
EXPLANATION={'title':'求整数后继','description':'给定整数 n，求它的后继 n+1。',
             'input_format':'一个整数 n。','output_format':'输出 n+1。','notes':['保留原题的变量与条件。']}
REPORT={'problem_id':PID,'suitable':True,'topic_fit':'合成数学题','summary':'求后继',
        'constraints':['给定整数'],'approach':['加一'],'correctness':['后继的定义'],
        'complexity':'时间 O(1)，空间 O(1)','sample_walkthrough':['1 对应 2'],
        'pitfalls':['检查范围'],'alternative_approaches':[],'limitations':['AI 合成测试，未运行代码']}
STATEMENT={'id':PID,'url':'https://codeforces.com/problemset/problem/4/A','title':'Synthetic test',
           'source':'official','render_version':4,'body_html':'<p>Find the successor of $$$n$$$.</p>',
           'sections':['Find n+1.'],'samples':[{'input':'1\n','output':'2\n'}]}


def setup_pipeline(app,monkeypatch):
    s=app.state.hermes
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
            value={'problem_id':PID,'reason':'合成选择'}
        elif task=='hermes_solution_report':
            assert data['statement']['id']==PID and 'draft' not in data
            value={**REPORT,'problem_explanation':EXPLANATION}
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
    monkeypatch.setattr(s,'pool',lambda *a:[])
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
    assert [call.args[0] for call in fake.await_args_list]==['hermes_selection']


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
        fake_renderer(service)
        r=c.post('/api/hermes/draw/image',json={'score_range':[800,1200]},headers={**admin,'Idempotency-Key':'8'*32})
        image_job=wait(c,r.json()['id'],admin)
        assert image_job['status']=='completed',image_job
        image_url=image_job['result']['screenshots'][0]['url']
        assert c.get(image_url,headers=admin).status_code==200
        assert c.post('/api/hermes/stop',json={},headers={**admin,'X-CSRF-Token':'bad'}).status_code==403
        member,_=auth('/api/auth/register',{'username':'member','password':'Synthetic-password-123','invitation':invite})
        assert c.get('/api/hermes/draws/'+job['id'],headers=member).status_code==404
        assert c.get('/api/hermes/draws/'+job['id']+'/screenshots/0',headers=member).status_code==404
        assert c.get(image_url,headers=member).status_code==404
        assert c.get('/api/hermes/requests/'+'8'*32,headers=member).status_code==404
        assert c.post('/api/hermes/draws/'+image_job['id']+'/cancel',json={},headers=member).status_code==404
        assert c.post('/api/hermes/requests/'+'8'*32+'/cancel',json={},headers=member).status_code==404
        assert c.get('/api/hermes/draws',headers=member).json()['total']==0
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
    assert [call.args[0] for call in fake.await_args_list]==['hermes_selection']


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
            value={'problem_id':other,'reason':'只选未做题'}
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

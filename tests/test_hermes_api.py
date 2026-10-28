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
from acmhermes.render import document

PID='codeforces:4-A'
REPORT={'problem_id':PID,'suitable':True,'topic_fit':'合成数学题','summary':'求后继',
        'constraints':['给定整数'],'approach':['加一'],'correctness':['后继的定义'],
        'complexity':'时间 O(1)，空间 O(1)','sample_walkthrough':['1 对应 2'],
        'pitfalls':['检查范围'],'alternative_approaches':[],'limitations':['AI 合成测试，未运行代码']}
STATEMENT={'id':PID,'url':'https://codeforces.com/problemset/problem/4/A','title':'Synthetic test',
           'source':'official','render_version':4,'body_html':'<p>Find the successor of $$$n$$$.</p>',
           'sections':['Find n+1.'],'samples':[{'input':'1\n','output':'2\n'}]}


def setup_pipeline(app,monkeypatch):
    s=app.state.hermes
    monkeypatch.setattr('acmhermes.service.refresh_recommendation_sessions',AsyncMock())
    monkeypatch.setattr(s.renderer,'preflight',AsyncMock())
    monkeypatch.setattr(s.ai,'selection',lambda:{'ready':True,'channel':'api','model':'synthetic'})
    monkeypatch.setattr(s.ai,'configuration_fingerprint',lambda:'ephemeral-fingerprint')
    monkeypatch.setattr(s.accounts.credential_checks,'statuses',lambda:{'items':[{'platform':'codeforces','status':'authenticated','login_verified':True}]})
    with s.db.connect() as c:
        c.execute('INSERT INTO problem_catalog VALUES(?,?,?)',('codeforces',PID,json.dumps({
            'id':PID,'platform':'codeforces','difficulty':1000,'tags':['math'],'title':'Synthetic','contest_id':4})))
        c.execute('INSERT INTO wb_problems VALUES(?,?,?)',(PID,json.dumps(STATEMENT),int(time.time())))
    async def complete(task,provider,model,data,schema):
        if task=='hermes_selection': value={'problem_id':PID,'reason':'合成选择'}
        elif task=='hermes_solution_report':
            assert data['statement']['id']==PID and 'draft' not in data
            value=REPORT
        elif task=='translation': value={'blocks':[{'id':b['id'],'text':b['text']} for b in data['blocks']]}
        else: raise AssertionError(task)
        return SimpleNamespace(data=value,provenance={'channel':'api','model':'synthetic','task':task})
    fake=AsyncMock(side_effect=complete)
    monkeypatch.setattr(s.ai,'complete',fake)
    async def render(statement,directory):
        directory.mkdir(mode=0o700)
        (directory/'0.png').write_bytes(b'\x89PNG\r\n\x1a\nSYNTHETIC')
        return ['0.png'],[]
    monkeypatch.setattr(s.renderer,'render',AsyncMock(side_effect=render))
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


def test_full_pipeline_and_idempotency_download_expiry(client):
    c,s,fake=client
    headers={'Idempotency-Key':'a'*32}
    r=c.post('/api/hermes/draw',json={'score_range':[800,1200],'topic':'math'},headers=headers)
    assert r.status_code==200,r.text
    job=wait(c,r.json()['id'])
    assert job['status']=='completed',job
    assert job['result']['report']==REPORT
    assert 'statement' not in job['result']
    assert c.get(job['result']['screenshots'][0]['url']).content.startswith(b'\x89PNG')
    count=fake.await_count
    assert c.get('/api/hermes/draws/'+job['id']).status_code==200
    assert fake.await_count==count
    assert c.get('/api/hermes/requests/'+'a'*32).json()['id']==job['id']
    assert c.post('/api/hermes/draw',json={'score_range':[800,1200],'topic':'math'},headers=headers).json()['id']==job['id']
    assert fake.await_count==count
    assert c.post('/api/hermes/draw',json={'score_range':[900,1300]},headers=headers).status_code==409
    s.expire(job['result']['artifact_id'])
    assert c.get(job['result']['screenshots'][0]['url']).status_code==410
    assert c.get('/api/hermes/draws/'+job['id']).json()['result']['report']==REPORT


def test_missing_browser_fails_before_paid_ai(client,monkeypatch):
    c,s,fake=client
    async def unavailable():raise AIError('Hermes 截图组件未安装；尚未调用 AI')
    monkeypatch.setattr(s.renderer,'preflight',unavailable)
    r=c.post('/api/hermes/draw',json={'score_range':[800,1200]},headers={'Idempotency-Key':'b'*32})
    job=wait(c,r.json()['id'])
    assert job['status']=='failed' and '尚未调用 AI' in job['error']
    fake.assert_not_awaited()


def test_no_model_or_idempotency_key_never_queues(client,monkeypatch):
    c,s,fake=client
    assert c.post('/api/hermes/draw',json={'score_range':[800,1200]}).status_code==422
    monkeypatch.setattr(s.ai,'selection',lambda:{'ready':False})
    assert c.post('/api/hermes/draw',json={'score_range':[800,1200]},headers={'Idempotency-Key':'b'*32}).status_code==400
    fake.assert_not_awaited()


@pytest.mark.parametrize('failure',['outside','unsuitable','config_change','statement_change'])
def test_failed_validation_never_delivers_mismatched_artifacts(client,monkeypatch,failure):
    c,s,fake=client
    original=fake.side_effect
    async def fail(task,*args):
        result=await original(task,*args)
        if task=='hermes_selection' and failure=='outside':result.data={'problem_id':'codeforces:99-Z','reason':'bad'}
        if task=='hermes_solution_report':
            if failure=='unsuitable':result.data={**REPORT,'suitable':False}
            if failure=='config_change':monkeypatch.setattr(s.ai,'configuration_fingerprint',lambda:'changed')
            if failure=='statement_change':
                with s.db.connect() as db:db.execute('UPDATE wb_problems SET data=?',(json.dumps({**STATEMENT,'title':'changed'}),))
        return result
    fake.side_effect=fail
    r=c.post('/api/hermes/draw',json={'score_range':[800,1200]},headers={'Idempotency-Key':'c'*32})
    job=wait(c,r.json()['id'])
    assert job['status']=='failed' and not job['result']
    s.renderer.render.assert_not_awaited()


def test_screenshot_sanitization_offline_and_keeps_links():
    markup,images=document({'title':'<bad>','body_html':'<script>secret()</script><p onclick="evil()">文本 $x$</p><img src="https://example.org/p.png"><iframe src="http://127.0.0.1/"/>','samples':[]})
    assert '<script' not in markup and 'onclick' not in markup and '<iframe' not in markup and '<img' not in markup
    assert 'https://example.org/p.png' in markup and images==['https://example.org/p.png']
    assert '&lt;bad&gt;' in markup


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
        member,_=auth('/api/auth/register',{'username':'member','password':'Synthetic-password-123','invitation':invite})
        assert c.get('/api/hermes/draws/'+job['id'],headers=member).status_code==404
        assert c.get(job['result']['screenshots'][0]['url'],headers=member).status_code==404
        assert c.get('/api/hermes/capabilities',headers={'Cookie':''}).status_code==401

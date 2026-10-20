import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from acmweb.app import create_app
from acmweb.settings import Settings
from acmweb.analytics import overview
from acmweb.catalog import candidates
from acmweb.ai import CoachReport, ProviderConfig
from acmweb.codex_bridge import AIError
from acmweb.database import Database
from ojcrawler.models import Collection, Submission
from ojcrawler.storage import Store


@pytest.fixture
def client(tmp_path):
    settings=Settings(data_dir=tmp_path,config_path=tmp_path/'account.toml',enable_codex=False)
    store=Store(settings.database)
    now=int(time.time())
    store.save('r',now,Collection('codeforces','demo',submissions=[
        Submission('1','2000-A',now-100,'WA',problem={'title':'A','tags':['math'],'difficulty':1000}),
        Submission('2','2000-A',now-50,'AC',problem={'title':'A','tags':['math'],'difficulty':1000}),
        Submission('3','2000-B',now-40*86400,'AC',problem={'title':'B','tags':['dp'],'difficulty':1200}),
    ],complete=True));store.close()
    with TestClient(create_app(settings)) as c:
        yield c


def test_overview_real_window_deduplication_and_query_coercion(client):
    response=client.get('/api/overview?days=30')
    assert response.status_code==200
    data=response.json()
    assert data['stats']['submissions']==2 and data['stats']['solved']==1
    assert data['stats']['acceptance']==50
    assert data['recent'][0]['id']=='2' and data['recent'][0]['account']=='demo'
    assert client.get('/api/overview?days=0').json()['stats']['submissions']==3
    assert client.get('/api/overview?days=-1').status_code==422
    assert data['topics'][0]['problems']==1


def test_submission_filters_and_pagination(client):
    data=client.get('/api/submissions?days=0&verdict=AC&limit=1').json()
    assert data['total']==2 and len(data['items'])==1
    assert client.get('/api/submissions?search=2000-B').json()['total']==1
    assert client.get('/api/submissions?limit=101').status_code==422


def test_security_token_origin_and_production_guards(tmp_path):
    with pytest.raises(RuntimeError):
        create_app(Settings(data_dir=tmp_path,production=True,access_token='short'))
    secret='a-strong-access-token-for-tests'
    with TestClient(create_app(Settings(data_dir=tmp_path,production=True,access_token=secret))) as c:
        assert c.get('/api/overview').status_code==401
        assert c.post('/api/auth/login',json={'token':'wrong'}).status_code==401
        response=c.post('/api/auth/login',json={'token':secret})
        assert response.status_code==200
        assert 'HttpOnly' in response.headers['set-cookie'] and 'Secure' in response.headers['set-cookie']
        assert c.get('/api/overview',headers={'Authorization':'Bearer '+secret}).status_code==200
        assert c.post('/api/analysis',headers={'Authorization':'Bearer '+secret,'Origin':'https://evil.example'},json={}).status_code==403
        assert not c.app.state.codex.enabled


def test_settings_do_not_return_secrets_and_cookie_config(client):
    secret='sk-test-not-a-real-key'
    response=client.put('/api/ai/settings',json={'api_key':secret,'base_url':'https://api.openai.com/v1','protocol':'responses','model':''})
    assert response.status_code==200 and response.json()['has_key']
    assert secret not in response.text
    assert secret not in client.get('/api/ai/settings').text
    path=client.app.state.ai.secret_file
    assert path.stat().st_mode&0o777==0o600
    assert client.put('/api/ai/settings',json={'base_url':'http://127.0.0.1','protocol':'responses'}).status_code==400
    oversized='SENSITIVE-'+('x'*2000)
    invalid=client.put('/api/ai/settings',json={'api_key':oversized})
    assert invalid.status_code==422 and 'SENSITIVE-' not in invalid.text


def test_raw_credentials_are_never_in_ai_prompt(client,monkeypatch):
    captured={}
    async def generate(model,prompt,schema):
        captured['prompt']=prompt
        return json.dumps(valid_report())
    monkeypatch.setattr(client.app.state.ai,'_api_generate',generate)
    from acmweb.analytics import overview
    context=overview(client.app.state.database)
    context['profile']={'cookie':'TOP-SECRET'}
    context['recent'][0]['problem']['raw']={'cookie':'TOP-SECRET'}
    result=asyncio.run(client.app.state.ai.generate('api','test-model',context))
    assert 'TOP-SECRET' not in captured['prompt']
    assert result['summary']=='summary'


def valid_report():
    return {'title':'Report','summary':'summary','confidence':'small sample','strengths':['math'],
        'weaknesses':[{'topic':'dp','evidence':'few attempts','action':'practice'}],
        'plan':[{'day':'Monday','focus':'dp','task':'one problem'}],'selected_problems':[]}


def test_ai_unapproved_problem_rejected(client,monkeypatch):
    async def generate(*args):
        value=valid_report();value['selected_problems']=[{'id':'invented','reason':'x'}]
        return json.dumps(value)
    monkeypatch.setattr(client.app.state.ai,'_api_generate',generate)
    with pytest.raises(AIError,match='候选列表'):
        asyncio.run(client.app.state.ai.generate('api','test',overview(client.app.state.database),[{'id':'valid'}]))


def test_catalog_excludes_all_attempts_and_feedback(client):
    db=client.app.state.database
    rows=[]
    for pid in ['2000-A','2000-B','2000-C','2000-D']:
        p={'id':'codeforces:'+pid,'platform':'codeforces','problem_id':pid,'contest_id':2000,'difficulty':1000,'tags':['math'],'title':pid,'url':'https://codeforces.com/'}
        rows.append(('codeforces',p['id'],json.dumps(p)))
    with db.connect() as conn:conn.executemany('INSERT INTO problem_catalog VALUES(?,?,?)',rows)
    client.post('/api/feedback',json={'platform':'codeforces','problem_id':'2000-C','status':'skip'})
    assert [p['problem_id'] for p in candidates(db)]==['2000-D']


def test_missing_data_and_disabled_codex_report_honestly(client):
    data=client.get('/api/ai/account').json()
    assert not data['available'] and not data['connected']
    assert client.post('/api/ai/login',json={}).status_code==400
    assert client.post('/api/analysis',json={'provider':'api','model':'x','days':31}).status_code==422
    assert client.post('/api/recommend',json={'provider':'api','model':'x','minimum':1600,'maximum':800}).status_code==400


def test_task_and_report_lifecycle(client,monkeypatch):
    async def generate(*args,**kwargs):return valid_report()
    monkeypatch.setattr(client.app.state.ai,'generate',generate)
    response=client.post('/api/analysis',json={'provider':'api','model':'test-model','days':30})
    assert response.status_code==200
    job_id=response.json()['id']
    for _ in range(100):
        job=next(j for j in client.get('/api/jobs').json()['items'] if j['id']==job_id)
        if job['status'] not in {'queued','running'}:break
        time.sleep(.01)
    assert job['status']=='completed'
    report=client.get('/api/reports/'+job['result']['report_id']).json()
    assert report['data']['summary']=='summary'
    assert report['data']['data_window']['submissions']==2


def test_user_driven_daily_task_api(client):
    state=client.put('/api/daily/settings',json={'daily_goal':2,'duration_days':14})
    assert state.status_code==200
    assert state.json()['active'] and state.json()['settings']['daily_goal']==2
    assert state.json()['rerolls_remaining']==2


def test_training_preferences_and_invalid_range(client):
    current=client.get('/api/training').json()
    current.update(minimum=1600,maximum=1800,min_contest=1879,preferred_tags=['dp'],avoid_tags=['implementation'])
    assert client.put('/api/training',json=current).status_code==200
    assert client.get('/api/training').json()['maximum']==1800
    current['minimum']=2000
    assert client.put('/api/training',json=current).status_code==400


def test_chat_upstream_truncation_not_saved(client,monkeypatch):
    client.app.state.ai.save(ProviderConfig(api_key='test-key',protocol='chat'))
    class Fake:
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def post(self,*a,**k):return httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{}'}}]})
    monkeypatch.setattr('acmweb.ai.httpx.AsyncClient',lambda **kwargs:Fake())
    with pytest.raises(AIError,match='截断'):
        asyncio.run(client.app.state.ai._api_generate('test','prompt',{}))


def test_legacy_partial_training_settings_include_form_defaults(client):
    client.app.state.database.set('training',{'minimum':800,'maximum':1800})
    data=client.get('/api/training').json()
    assert data['minimum']==800 and data['maximum']==1800
    assert data['preferred_tags']==[] and data['avoid_tags']==[]
    assert data['goal_rating']==2000 and data['prefer_recent'] is True

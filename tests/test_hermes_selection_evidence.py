"""Synthetic provider results, no platform or model is contacted."""
import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from acmhermes import selection_evidence as module
from acmweb.database import Database


def accounts(platform='codeforces'):
    return SimpleNamespace(credential_checks=SimpleNamespace(statuses=lambda:{'items':[
        {'platform':platform,'handle':'Owner','status':'authenticated','login_verified':True}]}),
        document=lambda:{'accounts':[
            {'platform':'codeforces','handle':'Different','api_key':'other-private','api_secret':'other-secret'},
            {'platform':'codeforces','handle':'OWNER','api_key':'own-private','api_secret':'own-secret'}]})


def test_bounded_enrichment_uses_only_verified_account_key_and_merges_public_rows(tmp_path,monkeypatch):
    database=Database(tmp_path/'db.sqlite3')
    calls=[]
    row={'id':'codeforces:4-A','platform':'codeforces','title':'Public','contest_id':4}
    class Provider:
        def __init__(self,db,**kwargs):
            assert db is database
            assert kwargs['api_key']=='own-private' and kwargs['api_secret']=='own-secret'
        def standings(self,cid):
            calls.append(cid)
            return {'status':'available','problems':[{**row,'id':f'codeforces:{cid}-A','contest_id':cid}]}
        def gym_catalog(self,limit):
            assert limit==2
            return {'status':'unavailable','reason':'gym_api_required','rows':[],'contests_checked':0}
    monkeypatch.setattr(module,'ContestEvidenceProvider',Provider)
    summary=asyncio.run(module.refresh_evidence(database,accounts(),[
        {**row,'contest_id':i} for i in range(1,15)]))
    assert len(calls)==3 and len(set(calls))==3
    assert summary['contests_checked']==3
    with database.connect() as db:
        rows=[json.loads(r[0]) for r in db.execute('SELECT data FROM problem_catalog')]
    assert len(rows)==3
    assert all('private' not in json.dumps(r) and 'secret' not in json.dumps(r) for r in rows)
    assert 'private' not in json.dumps(summary)


def test_other_verified_platform_does_not_fetch_cf(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'ContestEvidenceProvider',lambda *a,**kw:pytest.fail('unexpected network'))
    result=asyncio.run(module.refresh_evidence(Database(tmp_path/'db'),accounts('atcoder'),[]))
    assert result['status']=='not_required'


def test_cancellation_does_not_publish_late_candidates(tmp_path,monkeypatch):
    database=Database(tmp_path/'db')
    started,release,finished=threading.Event(),threading.Event(),threading.Event()
    class Provider:
        def __init__(self,*a,stop=None,**kw):self.stop=stop
        def standings(self,cid):
            started.set()
            assert release.wait(3)
            assert self.stop.is_set()
            finished.set()
            raise asyncio.CancelledError
        def gym_catalog(self,**kw):pytest.fail('cancelled worker cannot fetch more')
    monkeypatch.setattr(module,'ContestEvidenceProvider',Provider)
    async def run():
        task=asyncio.create_task(module.refresh_evidence(database,accounts(),[
            {'platform':'codeforces','contest_id':4}]))
        assert await asyncio.to_thread(started.wait,3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        release.set()
        assert await asyncio.to_thread(finished.wait,3)
    asyncio.run(run())
    with database.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM problem_catalog').fetchone()[0]==0


def test_account_switch_prevents_publishing_old_candidates(tmp_path,monkeypatch):
    database=Database(tmp_path/'db')
    config=accounts()
    revision=['before']
    config.revision=lambda:revision[0]
    class Provider:
        def __init__(self,*args,**kwargs):pass
        def standings(self,cid):return {'status':'unavailable'}
        def gym_catalog(self,**kwargs):
            revision[0]='after'
            return {'status':'available','rows':[{'id':'codeforces:100001-A','platform':'codeforces'}]}
    monkeypatch.setattr(module,'ContestEvidenceProvider',Provider)
    from acmweb.codex_bridge import AIError
    with pytest.raises(AIError,match='账号.*变化'):
        asyncio.run(module.refresh_evidence(database,config,[]))
    with database.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM problem_catalog').fetchone()[0]==0


def test_sampling_reserves_outside_rating_and_reports_only_successful_contests(tmp_path,monkeypatch):
    calls=[]
    class Provider:
        def __init__(self,*a,**kw):pass
        def standings(self,cid):
            calls.append(cid)
            return {'status':'available','problems':[]} if cid==50 else {'status':'unavailable'}
        def gym_catalog(self,**kw):return {'status':'unavailable','rows':[]}
    monkeypatch.setattr(module,'ContestEvidenceProvider',Provider)
    rows=[{'platform':'codeforces','contest_id':i,'difficulty':1800} for i in range(1,40)]
    rows.append({'platform':'codeforces','contest_id':50,'difficulty':1600})
    summary=asyncio.run(module.refresh_evidence(Database(tmp_path/'db'),accounts(),rows,score_range=(1800,1800)))
    assert len(calls)==3 and 50 in calls
    assert summary['available_contest_ids']==[50]
    assert summary['contests_available']==1

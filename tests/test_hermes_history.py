"""Synthetic local history coverage; never contacts platforms or a paid model."""
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from acmhermes import history
from acmweb.codex_bridge import AIError
from acmweb.database import Database
from ojcrawler.models import Collection, Submission
from ojcrawler.storage import Store, run_lock


@pytest.fixture
def state(tmp_path,monkeypatch):
    path=tmp_path/'private.toml'
    path.write_text('''[crawler]
overlap_seconds=60
[[accounts]]
platform="codeforces"
handle="Owner"
[[accounts]]
platform="atcoder"
handle="Other"
''')
    accounts=SimpleNamespace(effective_path=path,revision=lambda:'current',credential_checks=SimpleNamespace(
        statuses=lambda:{'items':[{'platform':'codeforces','handle':'Owner','login_verified':True,'status':'authenticated'}]}))
    database=Database(tmp_path/'private.sqlite3')
    seen=[]
    outcome=SimpleNamespace(complete=True,error=None)
    def collect(since):
        seen.append(since)
        return Collection('codeforces','Owner',submissions=[Submission('1','4-A',100,'AC')],
                          complete=outcome.complete,error=outcome.error)
    monkeypatch.setattr(history,'ADAPTERS',{'codeforces':lambda *args,**kw:SimpleNamespace(collect=collect)})
    class Client:
        def __init__(self,*args,**kw):pass
        def close(self):pass
    monkeypatch.setattr(history,'HistoryClient',Client)
    monkeypatch.setattr(history.time,'time',lambda:1000)
    return database,accounts,seen,outcome


def checkpoint(db):
    with db.connect() as c:
        return c.execute('SELECT covered_until FROM hermes_history_sync').fetchall()


def test_full_baseline_ignores_today_watermark_and_increment_uses_independent_cursor(state):
    db,accounts,seen,_=state
    with db.connect() as c:c.execute('INSERT INTO accounts VALUES(?,?,?)',('codeforces','Owner',9999999))
    result=history.synchronize(db,accounts,threading.Event())
    assert seen==[0] and result['complete']
    assert len(result['accounts'])==1  # Unverified second account is not fetched.
    assert result['accounts'][0]['coverage'].startswith('CF')
    assert checkpoint(db)[0][0]==1000
    with db.connect() as c:c.execute('UPDATE accounts SET watermark=99999999')
    history.synchronize(db,accounts,threading.Event())
    assert seen==[0,940]
    with db.connect() as c:assert c.execute('SELECT verdict FROM submissions').fetchone()[0]=='AC'


def test_partial_history_retains_known_records_but_never_establishes_baseline(state):
    db,accounts,seen,outcome=state
    outcome.complete=False;outcome.error='SECRET-COOKIE upstream text'
    with pytest.raises(AIError) as raised:history.synchronize(db,accounts,threading.Event())
    assert 'SECRET' not in str(raised.value) and '同步未完整' in str(raised.value)
    assert not checkpoint(db)
    with db.connect() as c:assert c.execute('SELECT count(*) FROM submissions').fetchone()[0]==1
    outcome.complete=True
    history.synchronize(db,accounts,threading.Event())
    assert seen==[0,0]


def test_increment_failure_does_not_advance_independent_checkpoint(state,monkeypatch):
    db,accounts,seen,outcome=state
    history.synchronize(db,accounts,threading.Event())
    monkeypatch.setattr(history.time,'time',lambda:2000)
    outcome.complete=False
    with pytest.raises(AIError):history.synchronize(db,accounts,threading.Event())
    assert checkpoint(db)[0][0]==1000
    outcome.complete=True
    history.synchronize(db,accounts,threading.Event())
    assert seen==[0,940,940] and checkpoint(db)[0][0]==2000


def test_cf_case_change_reuses_same_baseline(state):
    db,accounts,seen,_=state
    history.synchronize(db,accounts,threading.Event())
    accounts.effective_path.write_text(accounts.effective_path.read_text().replace('Owner','owner'))
    accounts.credential_checks.statuses=lambda:{'items':[{'platform':'codeforces','handle':'owner','login_verified':True,'status':'authenticated'}]}
    history.synchronize(db,accounts,threading.Event())
    assert seen==[0,940]


def test_credentials_change_during_read_never_persists_response(state,monkeypatch):
    db,accounts,_,_=state
    def collect(since):
        accounts.revision=lambda:'replaced'
        return Collection('codeforces','Owner',submissions=[Submission('9','9-A',100,'AC')],complete=True)
    monkeypatch.setattr(history,'ADAPTERS',{'codeforces':lambda *args,**kw:SimpleNamespace(collect=collect)})
    with pytest.raises(AIError,match='配置已变化'):history.synchronize(db,accounts,threading.Event())
    with db.connect() as c:assert c.execute('SELECT count(*) FROM submissions').fetchone()[0]==0
    assert not checkpoint(db)


def test_lock_conflict_does_not_run_parallel_sync(state):
    db,accounts,seen,_=state
    with run_lock(db.path):
        with pytest.raises(AIError,match='另一个提交同步'):history.synchronize(db,accounts,threading.Event())
    assert not seen


def test_unknown_or_unverified_account_does_not_fetch(state):
    db,accounts,seen,_=state
    accounts.credential_checks.statuses=lambda:{'items':[{'platform':'codeforces','handle':'Owner','status':'saved','login_verified':False}]}
    with pytest.raises(AIError,match='没有已验证'):history.synchronize(db,accounts,threading.Event())
    assert not seen


def test_history_workspace_checkpoints_and_submissions_are_isolated(state,tmp_path):
    db,accounts,seen,_=state
    history.synchronize(db,accounts,threading.Event())
    other=Database(tmp_path/'other.sqlite3')
    history.synchronize(other,accounts,threading.Event())
    assert seen==[0,0]


def test_cancelled_thread_returns_without_saving_or_starting_more_work(state,monkeypatch):
    db,accounts,_,_=state
    entered,release,finished=threading.Event(),threading.Event(),threading.Event()
    def collect(since):
        entered.set()
        release.wait(3)
        return Collection('codeforces','Owner',submissions=[Submission('9','9-A',100,'AC')],complete=True)
    monkeypatch.setattr(history,'ADAPTERS',{'codeforces':lambda *args,**kw:SimpleNamespace(collect=collect)})
    original=history.synchronize
    def tracked(*args):
        try:return original(*args)
        finally:finished.set()
    monkeypatch.setattr(history,'synchronize',tracked)
    async def run():
        task=asyncio.create_task(history.sync_history(db,accounts))
        for _ in range(100):
            if entered.is_set():break
            await asyncio.sleep(.01)
        assert entered.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        release.set()
        for _ in range(100):
            if finished.is_set():break
            await asyncio.sleep(.01)
        assert finished.is_set()
    asyncio.run(run())
    with db.connect() as c:assert c.execute('SELECT count(*) FROM submissions').fetchone()[0]==0
    assert not checkpoint(db)


def test_http_client_stops_between_requests_without_using_a_returned_late_body(monkeypatch):
    stop=threading.Event()
    calls=[]
    def get(self,*args,**kwargs):
        calls.append(args)
        stop.set()
        return 'late private response'
    monkeypatch.setattr(history.Client,'get',get)
    client=history.HistoryClient(stop)
    try:
        with pytest.raises(history.Stopped):client.get('https://codeforces.com/api/user.status')
        with pytest.raises(history.Stopped):client.get('https://codeforces.com/api/user.status')
        assert len(calls)==1
    finally:client.close()


def test_cancellation_during_host_pacing_prevents_actual_send(monkeypatch):
    stop=threading.Event()
    sends=[]
    class Session:
        headers={}
        def get(self,*args,**kwargs):
            sends.append(args)
            pytest.fail('A stopped history request must not be sent')
        def close(self):pass
    monkeypatch.setattr('ojcrawler.http.requests.Session',Session)
    def paced(self,*args,**kwargs):
        stop.set()  # Simulates cancellation while the base transport is pacing.
        return self.session.get(*args,**kwargs)
    monkeypatch.setattr(history.Client,'get',paced)
    client=history.HistoryClient(stop)
    try:
        with pytest.raises(history.Stopped):client.get('https://codeforces.com/api/user.status')
        assert not sends
    finally:client.close()

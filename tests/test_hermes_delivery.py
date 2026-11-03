"""Durable receipt state using a temporary private DB, no OJ or AI calls."""
import json
import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from acmhermes.delivery import Deliveries, Receipt
from acmweb.database import Database

JID='1'*32
RESULT={'format':'text','problem':{'id':'codeforces:4-A'},'report':{'summary':'合成报告'}}


@pytest.fixture
def harness(tmp_path,monkeypatch):
    db=Database(tmp_path/'private.sqlite3');store=Deliveries(db);clock=[2000000000.0]
    monkeypatch.setattr('acmhermes.delivery.time.time',lambda:clock[0])
    with db.connect() as c:
        c.execute('INSERT INTO web_jobs VALUES(?,?,?,?,?,?,?)',(JID,'hermes_draw','completed',1,1,json.dumps(RESULT),None))
    return db,store,clock


def receipt(message,**extra):
    return Receipt(delivery_id=message['delivery_id'],result_sha256=message['result_sha256'],received=True,**extra)


def test_unacknowledged_result_retries_identical_bytes_until_receipt(harness):
    db,store,clock=harness
    assert store.status(JID)['state']=='not_started'  # GET does not create a delivery.
    store.start(JID)
    assert store.status(JID)['state']=='pending'
    first=store.claim(JID)
    assert first['type']=='result' and first['attempts']==1
    assert hashlib.sha256(first['result_json'].encode()).hexdigest()==first['result_sha256']
    assert store.claim(JID)['type']=='waiting'
    clock[0]+=5
    second=store.claim(JID)
    assert second['attempts']==2
    assert (first['delivery_id'],first['result_sha256'],first['result_json'])==(
        second['delivery_id'],second['result_sha256'],second['result_json'])
    confirmed=store.acknowledge(JID,receipt(first))
    assert confirmed['state']=='acknowledged'
    assert store.acknowledge(JID,receipt(first))==confirmed  # Lost ACK reply is safe.
    assert store.claim(JID)['type']=='acknowledged'
    assert 'result_json' not in store.status(JID)


def test_restart_keeps_same_message_and_pending_receipt(harness):
    db,store,clock=harness;store.start(JID);first=store.claim(JID)
    restarted=Deliveries(db)
    assert restarted.status(JID)['state']=='awaiting_ack'
    clock[0]+=6
    assert restarted.claim(JID)['delivery_id']==first['delivery_id']
    restarted.acknowledge(JID,receipt(first))
    assert Deliveries(db).status(JID)['state']=='acknowledged'


def test_wrong_job_delivery_and_checksum_never_acknowledge(harness):
    _,store,_=harness;store.start(JID)
    with pytest.raises(HTTPException) as e:store.claim('2'*32)
    assert e.value.status_code==404
    metadata=store.status(JID)
    with pytest.raises(HTTPException) as e:store.acknowledge(JID,receipt(metadata))
    assert e.value.status_code==409  # Not sent yet.
    first=store.claim(JID)
    for field,value,status in [('delivery_id','0'*32,404),('result_sha256','0'*64,409)]:
        wrong={**first,field:value}
        with pytest.raises(HTTPException) as e:store.acknowledge(JID,receipt(wrong))
        assert e.value.status_code==status
    assert store.status(JID)['state']=='awaiting_ack'


def test_only_completed_hermes_jobs_can_be_delivered(harness):
    db,store,_=harness
    for state in ['running','failed','interrupted']:
        with db.connect() as c:c.execute('UPDATE web_jobs SET status=? WHERE id=?',(state,JID))
        with pytest.raises(HTTPException) as e:store.start(JID)
        assert e.value.status_code==409
    with db.connect() as c:c.execute("UPDATE web_jobs SET status='completed',kind='translation' WHERE id=?",(JID,))
    with pytest.raises(HTTPException) as e:store.start(JID)
    assert e.value.status_code==404


def test_dispatch_lease_is_atomic_across_consumers(harness):
    _,store,_=harness;store.start(JID)
    with ThreadPoolExecutor(max_workers=2) as pool:
        events=list(pool.map(lambda _:store.claim(JID),range(2)))
    assert sorted(e['type'] for e in events)==['result','waiting']
    assert store.status(JID)['attempts']==1


def test_image_payload_uses_same_origin_urls_and_stable_digest(harness):
    db,store,_=harness
    value={**RESULT,'format':'image','artifact_id':'private-path','screenshots':['/private/1.png'],
           'expires_at':2000001800,'artifacts_expired':False}
    with db.connect() as c:c.execute('UPDATE web_jobs SET result=? WHERE id=?',(json.dumps(value),JID))
    store.start(JID);first=store.claim(JID);result=json.loads(first['result_json'])
    assert 'artifact_id' not in result and 'artifacts_expired' not in result
    assert result['screenshots']==[{'index':0,'url':f'/api/hermes/draws/{JID}/screenshots/0'}]
    assert '/private' not in first['result_json']


@pytest.mark.parametrize('extra',[{'received':False},{'received':1},{'received':'true'},{'user_id':'another'}])
def test_receipt_is_explicit_and_strict(extra):
    with pytest.raises(ValueError):Receipt.model_validate({'delivery_id':'1'*32,'result_sha256':'2'*64,'received':True,**extra})


def test_real_local_http_stream_sends_before_ack_and_stops_after_ack(tmp_path):
    """Real loopback HTTP transport; result data is synthetic, no OJ or model."""
    import socket
    import threading
    import time
    from types import SimpleNamespace
    import httpx
    import uvicorn
    from fastapi import FastAPI
    from acmhermes.delivery import register
    db=Database(tmp_path/'local.sqlite3');store=Deliveries(db)
    with db.connect() as c:
        c.execute('INSERT INTO web_jobs VALUES(?,?,?,?,?,?,?)',(JID,'hermes_draw','completed',1,1,json.dumps(RESULT),None))
    app=FastAPI();register(app,SimpleNamespace(control=SimpleNamespace(deliveries=store)))
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='critical',access_log=False))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    try:
        for _ in range(200):
            if server.started:break
            time.sleep(.01)
        assert server.started
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=3,trust_env=False) as client:
            with client.stream('POST',f'/api/hermes/draws/{JID}/delivery/stream',json={}) as response:
                lines=response.iter_lines();first=json.loads(next(lines))
                assert first['type']=='result'  # Received before stream ends.
                payload={k:first[k] for k in ('delivery_id','result_sha256')};payload['received']=True
                reply=client.post(f'/api/hermes/draws/{JID}/delivery/ack',json=payload)
                assert reply.status_code==200
                assert json.loads(next(lines))['type']=='acknowledged'
                assert list(lines)==[]
        assert store.status(JID)['attempts']==1
        from acmhermes.client import HermesClient
        second='2'*32
        with db.connect() as c:
            c.execute('INSERT INTO web_jobs VALUES(?,?,?,?,?,?,?)',(second,'hermes_draw','completed',1,1,json.dumps(RESULT),None))
        received=[]
        def save(job):
            received.append(job['delivery']['delivery_id'])
            return True
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=3,trust_env=False) as client:
            assert HermesClient(client).receive(second,save)['state']=='acknowledged'
        assert len(received)==1 and store.status(second)['attempts']==1
    finally:
        server.should_exit=True;thread.join(5);sock.close()
        assert not thread.is_alive()

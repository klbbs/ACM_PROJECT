import json
import httpx
import pytest
from acmhermes.client import HermesClient


def test_sdk_minimal_parameters_session_headers_and_text_download(tmp_path):
    calls=[]
    jid='a'*32
    def transport(request):
        calls.append(request)
        if request.method=='POST':
            assert json.loads(request.content)=={'score_range':[800,1200]}
            assert request.headers['idempotency-key']=='b'*32
            assert request.headers['x-csrf-token']=='synthetic-csrf'
            return httpx.Response(200,json={'id':jid,'status':'queued'})
        assert '/screenshots/' not in request.url.path
        return httpx.Response(200,json={'id':jid,'status':'completed','result':{
            'problem_explanation':{'title':'测试题','description':'给 n 求 n+1','input_format':'一个整数',
                'output_format':'一个整数','constraints':['n 为整数'],'samples':[{'input':'1\n','output':'2\n'}],
                'notes':[],'original_url':'https://codeforces.com/problemset/problem/4/A'},
            'report':{'summary':'synthetic','approach':['答案思路只在报告展示']}}})
    with httpx.Client(base_url='https://acm.example',headers={'X-CSRF-Token':'synthetic-csrf'},transport=httpx.MockTransport(transport)) as session:
        sdk=HermesClient(session)
        assert sdk.draw((800,1200),request_key='b'*32)['id']==jid
        assert sdk.recover('b'*32)['id']==jid
        job=sdk.wait(jid)
        question=sdk.explain(job)
        assert '给 n 求 n+1' in question and '样例 1 输入' in question
        assert '答案思路只在报告展示' not in question
        folder=sdk.download(job,tmp_path)
        assert (folder/'problem.txt').read_text()==question
        assert '答案思路只在报告展示' in (folder/'solution.txt').read_text()
        assert not list(folder.glob('*.png'))
        assert all(r.url.host=='acm.example' for r in calls)
        assert len([r for r in calls if r.method=='POST'])==1
        with pytest.raises(FileExistsError):sdk.download(job,tmp_path)


def test_sdk_does_not_retry_paid_request_on_transport_failure():
    calls=[]
    def fail(request):
        calls.append(request)
        raise httpx.ReadTimeout('synthetic')
    with httpx.Client(base_url='https://acm.example',transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(httpx.ReadTimeout):HermesClient(client).draw((800,1200),request_key='c'*32)
    assert len(calls)==1


def test_sdk_legacy_download_does_not_follow_screenshot_urls(tmp_path):
    with httpx.Client(base_url='https://acm.example',transport=httpx.MockTransport(
            lambda request: pytest.fail('Text download must not send any request'))) as session:
        sdk=HermesClient(session)
        job={'id':'d'*32,'status':'completed','result':{'report':{'summary':'旧报告'},
             'screenshots':[{'url':'http://127.0.0.1/private'}]}}
        folder=sdk.download(job,tmp_path)
        assert '此历史任务尚无中文题目说明' in (folder/'problem.txt').read_text()
        assert '旧报告' in (folder/'solution.txt').read_text()


def test_sdk_controls_and_image_download_never_follow_arbitrary_urls(tmp_path):
    jid,key='a'*32,'b'*32
    calls=[]
    def transport(request):
        calls.append(request)
        assert request.url.host=='acm.example'
        path=request.url.path
        if path.endswith('/screenshots/0'):
            return httpx.Response(200,content=b'\x89PNG\r\n\x1a\nsynthetic')
        if request.method=='POST':
            if path.endswith('/image'):
                assert json.loads(request.content)=={'score_range':[800,1200],'topic':'math'}
                assert request.headers['idempotency-key']==key
            else:
                assert json.loads(request.content)=={}
            assert request.headers['x-csrf-token']=='synthetic'
        return httpx.Response(200,json={'id':jid,'status':'completed'})
    with httpx.Client(base_url='https://acm.example',headers={'X-CSRF-Token':'synthetic'},transport=httpx.MockTransport(transport)) as session:
        sdk=HermesClient(session)
        sdk.draw_image((800,1200),'math',request_key=key)
        sdk.cancel(jid);sdk.cancel_request(key);sdk.stop_all()
        sdk.list_jobs(status='completed');sdk.readiness();sdk.candidates((800,1200),'math')
        job={'id':jid,'status':'completed','result':{'format':'image','report':{},
            'screenshots':[{'url':'http://127.0.0.1/private'}]}}
        folder=sdk.download(job,tmp_path)
        assert (folder/'1.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
        assert calls[-1].url.path==f'/api/hermes/draws/{jid}/screenshots/0'
        assert any(r.url.path=='/api/hermes/stop' for r in calls)
        assert any(r.url.path=='/api/hermes/requests/'+key+'/cancel' for r in calls)


def test_sdk_waits_for_cleanup_after_interrupt_status(monkeypatch):
    replies=iter([
        {'status':'interrupted','control':{'cancel_requested':True,'stop_state':'stopping'}},
        {'status':'interrupted','control':{'cancel_requested':True,'stop_state':'stopped'}}])
    calls=[]
    def transport(request):
        calls.append(request)
        return httpx.Response(200,json=next(replies))
    monkeypatch.setattr('acmhermes.client.time.sleep',lambda _:None)
    with httpx.Client(base_url='https://acm.example',transport=httpx.MockTransport(transport)) as session:
        assert HermesClient(session).wait_stopped('a'*32)['control']['stop_state']=='stopped'
    assert len(calls)==2


def memory_item(pid,**flags):
    return {'problem_id':pid,'attempted':False,'solved':False,'drawn':False,'skipped':False,**flags}


def test_sdk_syncs_private_paginated_memory_and_sends_exclusions(tmp_path):
    calls=[]
    first=memory_item('codeforces:4-A',solved=True)
    second=memory_item('codeforces:5-A',drawn=True)
    def transport(request):
        calls.append(request)
        if request.method=='POST':
            assert request.url.path=='/api/hermes/draw/image'
            assert json.loads(request.content)=={'score_range':[800,1200],
                'exclude_ids':['codeforces:4-A','codeforces:5-A','codeforces:6-A']}
            return httpx.Response(200,json={'id':'a'*32})
        assert request.url.path=='/api/hermes/problem-memory'
        params=request.url.params
        if params.get('limit')=='1':
            return httpx.Response(200,json={'scope':'user-one','revision':'newer',
                'items':[first],'next_after':'codeforces:4-A'})
        if params.get('after'):
            assert params['after']=='codeforces:4-A' and params['revision']=='r1'
            return httpx.Response(200,json={'scope':'user-one','revision':'r1',
                'items':[second],'next_after':None})
        assert 'revision' not in params
        return httpx.Response(200,json={'scope':'user-one','revision':'r1',
            'items':[first],'next_after':'codeforces:4-A'})
    path=tmp_path/'one'/'memory.json'
    with httpx.Client(base_url='https://acm.example',transport=httpx.MockTransport(transport)) as session:
        sdk=HermesClient(session)
        snapshot=sdk.sync_memory(path)
        assert snapshot['items']==[first,second]
        assert json.loads(path.read_text())==snapshot
        assert path.stat().st_mode&0o777==0o600
        sdk.draw_image((800,1200),request_key='b'*32,
            exclude_ids=['codeforces:6-A','codeforces:4-A'],memory_path=path)
    assert len([request for request in calls if request.method=='POST'])==1


@pytest.mark.parametrize('failure',['revision','scope','transport','malformed'])
def test_sdk_sync_failure_keeps_existing_cache(tmp_path,failure):
    path=tmp_path/'memory.json';path.write_text('original private cache')
    count=0
    def transport(request):
        nonlocal count
        count+=1
        if count==1:
            return httpx.Response(200,json={'scope':'one','revision':'r1',
                'items':[memory_item('codeforces:4-A',attempted=True)],'next_after':'codeforces:4-A'})
        if failure=='transport':raise httpx.ReadTimeout('synthetic read failure')
        if failure=='revision':return httpx.Response(409,json={'detail':'memory changed'})
        if failure=='scope':return httpx.Response(200,json={'scope':'two','revision':'r1','items':[],'next_after':None})
        return httpx.Response(200,json={'scope':'one','revision':'r1',
            'items':[memory_item('not canonical',solved=True)],'next_after':None})
    with httpx.Client(base_url='https://acm.example',transport=httpx.MockTransport(transport)) as session:
        with pytest.raises((ValueError,httpx.HTTPError)):HermesClient(session).sync_memory(path)
    assert count==2 and path.read_text()=='original private cache'
    assert list(tmp_path.iterdir())==[path]


def test_sdk_refuses_other_identity_cache_before_paid_request(tmp_path):
    path=tmp_path/'memory.json'
    path.write_text(json.dumps({'scope':'other-user','revision':'r1',
        'items':[memory_item('codeforces:4-A',solved=True)],'next_after':None}))
    calls=[]
    def transport(request):
        calls.append(request)
        assert request.method=='GET'
        return httpx.Response(200,json={'scope':'current-user','revision':'r1','items':[],'next_after':None})
    with httpx.Client(base_url='https://acm.example',transport=httpx.MockTransport(transport)) as session:
        with pytest.raises(ValueError,match='another user'):
            HermesClient(session).draw((800,1200),request_key='b'*32,memory_path=path)
    assert len(calls)==1


def test_sdk_exclusions_no_automatic_paid_retry():
    calls=[]
    def transport(request):
        calls.append(request)
        assert json.loads(request.content)['exclude_ids']==['codeforces:4-A']
        return httpx.Response(503,json={'detail':'synthetic upstream failure'})
    with httpx.Client(base_url='https://acm.example',transport=httpx.MockTransport(transport)) as session:
        with pytest.raises(httpx.HTTPStatusError):
            HermesClient(session).draw((800,1200),request_key='c'*32,exclude_ids=['codeforces:4-A'])
    assert len(calls)==1


def delivery_event(jid='a'*32,did='b'*32):
    import hashlib
    text=json.dumps({'format':'text','report':{'summary':'合成结果'}},ensure_ascii=False)
    return {'type':'result','protocol':'acm-hermes-receipt-v1','job_id':jid,'delivery_id':did,
            'result_sha256':hashlib.sha256(text.encode()).hexdigest(),'result_json':text}


def test_sdk_persists_before_ack_and_only_retries_same_result():
    message=delivery_event();calls=[];committed=[]
    def transport(request):
        calls.append(request.url.path)
        assert request.url.path.endswith(('/delivery/stream','/delivery/ack'))
        if request.url.path.endswith('/ack'):
            assert committed==[message['delivery_id']]
            assert json.loads(request.content)['received'] is True
            return httpx.Response(200,json={**message,'type':'acknowledged','state':'acknowledged'})
        return httpx.Response(200,content=json.dumps(message)+'\n')
    def save(job):
        committed.append(job['delivery']['delivery_id'])
        assert job['result']['report']['summary']=='合成结果'
        return True
    with httpx.Client(base_url='https://acm.example',transport=httpx.MockTransport(transport)) as session:
        assert HermesClient(session).receive('a'*32,save)['state']=='acknowledged'
    assert len(calls)==2


def test_sdk_ack_response_loss_reconnects_without_repeating_consumer_commit(monkeypatch):
    message=delivery_event();events=[];handled=[];acks=[]
    monkeypatch.setattr('acmhermes.client.time.sleep',lambda _:None)
    def transport(request):
        events.append(request.url.path)
        if request.url.path.endswith('/ack'):
            acks.append(1)
            if len(acks)==1:raise httpx.ReadTimeout('synthetic lost ACK reply')
            return httpx.Response(200,json={**message,'type':'acknowledged','state':'acknowledged'})
        return httpx.Response(200,content=json.dumps(message)+'\n')
    def commit(job):handled.append(job['delivery']['delivery_id']);return True
    with httpx.Client(base_url='https://acm.example',transport=httpx.MockTransport(transport)) as session:
        assert HermesClient(session).receive('a'*32,commit)['state']=='acknowledged'
    assert handled==[message['delivery_id']] and len(acks)==2 and len(events)==4
    assert all('/draw' not in p.rsplit('/delivery',1)[-1] for p in events)


@pytest.mark.parametrize('kind',['checksum','wrong_job','handler_failure','not_saved'])
def test_sdk_never_acks_invalid_or_uncommitted_results(kind,monkeypatch):
    message=delivery_event();requests=[]
    monkeypatch.setattr('acmhermes.client.time.sleep',lambda _:None)
    if kind=='checksum':message['result_sha256']='0'*64
    if kind=='wrong_job':message['job_id']='c'*32
    def transport(request):
        requests.append(request.url.path)
        return httpx.Response(200,content=json.dumps(message)+'\n')
    def handler(job):
        if kind=='handler_failure':raise RuntimeError('synthetic storage failure')
        return None
    error=RuntimeError if kind=='handler_failure' else TimeoutError if kind=='not_saved' else ValueError
    with httpx.Client(base_url='https://acm.example',transport=httpx.MockTransport(transport)) as session:
        with pytest.raises(error):HermesClient(session).receive('a'*32,handler,connections=1)
    assert all(p.endswith('/delivery/stream') for p in requests)


def test_sdk_receives_saved_ack_after_connection_loss_without_new_delivery():
    message={**delivery_event(),'type':'acknowledged','state':'acknowledged'}
    def transport(request):
        assert request.url.path.endswith('/delivery/stream')
        return httpx.Response(200,content=json.dumps(message)+'\n')
    with httpx.Client(base_url='https://acm.example',transport=httpx.MockTransport(transport)) as session:
        result=HermesClient(session).receive('a'*32,lambda _:pytest.fail('already acknowledged'))
    assert result['state']=='acknowledged'


def test_sdk_shared_reports_read_only_and_rating_without_spoilers():
    requests=[]
    def transport(request):
        requests.append(request)
        assert request.method=='GET'
        assert request.url.host=='acm.example'
        assert request.url.params['problem_id']=='codeforces:4-A'
        return httpx.Response(200,json={'items':[]})
    with httpx.Client(base_url='https://acm.example',transport=httpx.MockTransport(transport)) as session:
        sdk=HermesClient(session)
        sdk.shared_reports('codeforces:4-A')
        sdk.shared_report('a'*32,'codeforces:4-A')
        with pytest.raises(ValueError):sdk.shared_report('../private','codeforces:4-A')
    assert len(requests)==2
    job={'result':{'ai_difficulty_assessment':{'rating':1840.5},
        'problem_explanation':{'title':'合成题','description':'题意','input_format':'输入','output_format':'输出',
            'constraints':[],'original_url':'https://codeforces.com/problemset/problem/4/A'},
        'report':{'difficulty_assessment':{'rating':1840.5,'evidence':['剧透推理'],'limitations':[]},
            'approach':['剧透算法']}}}
    question=HermesClient.explain(job)
    assert '1840.5' in question and '非官方评级' in question
    assert '剧透' not in question
    assert '剧透推理' in HermesClient.solution(job)

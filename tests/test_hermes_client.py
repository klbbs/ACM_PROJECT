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

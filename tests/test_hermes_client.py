import json
import httpx
import pytest
from acmhermes.client import HermesClient


def test_sdk_minimal_parameters_session_headers_and_safe_download(tmp_path):
    calls=[]
    jid='a'*32
    def transport(request):
        calls.append(request)
        if request.method=='POST':
            assert json.loads(request.content)=={'score_range':[800,1200]}
            assert request.headers['idempotency-key']=='b'*32
            assert request.headers['x-csrf-token']=='synthetic-csrf'
            return httpx.Response(200,json={'id':jid,'status':'queued'})
        if request.url.path.endswith('/screenshots/0'):
            return httpx.Response(200,content=b'\x89PNG\r\n\x1a\nSYNTHETIC')
        return httpx.Response(200,json={'id':jid,'status':'completed','result':{
            'report':{'summary':'synthetic'},'screenshots':[{'url':'http://127.0.0.1/private','index':0}]}})
    with httpx.Client(base_url='https://acm.example',headers={'X-CSRF-Token':'synthetic-csrf'},transport=httpx.MockTransport(transport)) as session:
        sdk=HermesClient(session)
        assert sdk.draw((800,1200),request_key='b'*32)['id']==jid
        assert sdk.recover('b'*32)['id']==jid
        job=sdk.wait(jid)
        folder=sdk.download(job,tmp_path)
        assert (folder/'1.png').read_bytes().startswith(b'\x89PNG')
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

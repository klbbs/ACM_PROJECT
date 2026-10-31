"""Synthetic upstream responses test the QR boundary, not real WeChat login."""
import asyncio
import json
from email.message import Message
from types import SimpleNamespace

import pytest
import requests
from fastapi import HTTPException
from acmweb.accounts import Accounts
from acmweb.credential_check import CredentialChecks
from acmweb.nowcoder_qr import NowcoderQR, ORIGIN, _body, _create, _poll
from acmweb.settings import Settings


class Response:
    status_code = 200
    def __init__(self, body, mime='application/json'):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = {'Content-Type': mime}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, size): yield self.body


class Upstream(requests.Session):
    image_url = 'https://mp.weixin.qq.com/cgi-bin/showqrcode?ticket=synthetic-ticket'
    result = {'code': 1, 'msg': '二维码未被扫描'}
    page = b'<script>window.isLogin = true; window.globalInfo = {ownerId:123};</script>'
    def __init__(self):
        super().__init__()
        self.cookies.set('test_session', 'SYNTHETIC', domain='ac.nowcoder.com', path='/')
        self.closed = False
    def close(self): self.closed = True; super().close()
    def get(self, url, **kw):
        assert kw['allow_redirects'] is False and kw['stream'] is True
        if url.endswith('/login?callBack=/'): return Response(b'login', 'text/html')
        if url.endswith('/wechat_qr_code'):
            return Response({'code': 0, 'data': {'ticket': 'synthetic-ticket', 'imageUrl': self.image_url, 'expireSecond': 120}})
        if url.startswith('https://mp.weixin.qq.com/'):
            return Response(b'\xff\xd8\xffSYNTHETIC', 'image/jpg')
        if url.endswith('/wechat_mp_status'): return Response(self.result)
        assert url == 'https://ac.nowcoder.com/'
        return Response(self.page, 'text/html')


class StatusAdapter(requests.adapters.BaseAdapter):
    """Exercise Requests Set-Cookie extraction without real network or secrets."""
    status_data = None
    omit_status_data = False
    status_set_cookie = True
    home_page = None
    interrupt_url = None
    on_close = None
    on_chunk = None
    def __init__(self):
        self.calls = []
    def close(self): pass
    def send(self, request, **kwargs):
        assert kwargs['stream'] is True
        connect, read = kwargs['timeout']
        assert 0 < connect <= 5 and 0 < read <= 12
        self.calls.append(request.url)
        headers = {'Content-Type': 'text/html'}
        if '/wechat_mp_status?' in request.url:
            status = {'code': 0}
            if not self.omit_status_data:
                status['data'] = self.status_data
            body = json.dumps(status).encode()
            if self.status_set_cookie:
                headers['Set-Cookie'] = 'test_session=EXCHANGED; Path=/; Secure; HttpOnly'
        elif request.url == ORIGIN + '/':
            body = self.home_page if self.home_page is not None else (
                Upstream.page if request.headers.get('Cookie') == 'test_session=EXCHANGED'
                else b'<script>window.isLogin = false;</script>')
        elif request.url.endswith('/login?callBack=/'):
            body = b'login'
        elif request.url.endswith('/wechat_qr_code'):
            body = json.dumps({'code': 0, 'data': {'ticket': 'synthetic-ticket',
                'imageUrl': Upstream.image_url, 'expireSecond': 120}}).encode()
        elif request.url == Upstream.image_url:
            body = b'\xff\xd8\xffSYNTHETIC'
            headers['Content-Type'] = 'image/jpeg'
        else:
            pytest.fail('request outside the fixed official endpoints')
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response.request = request
        response.headers.update(headers)
        response._content = body
        response._content_consumed = True
        message = Message()
        for key, value in headers.items():
            message[key] = value
        response.raw = SimpleNamespace(_original_response=SimpleNamespace(msg=message))
        if request.url == self.interrupt_url:
            if self.on_chunk:
                def chunks(size):
                    yield body[:1]
                    self.on_chunk()
                    yield body[1:]
                response.iter_content = chunks
            if self.on_close:
                close = response.close
                def finish():
                    close()
                    self.on_close()
                response.close = finish
        return response


class AnonymousUpstream(requests.Session):
    def __init__(self):
        super().__init__()
        self.adapter = StatusAdapter()
        self.mount('https://', self.adapter)


def manager(tmp_path):
    settings = Settings(data_dir=tmp_path, config_path=tmp_path/'missing.toml')
    accounts = Accounts(settings)
    return NowcoderQR(accounts, SimpleNamespace(tasks={}), CredentialChecks(accounts), Upstream)


def test_fixed_upstream_wait_and_unrecognized_failure():
    session = Upstream()
    ticket, image, ttl = _create(session)
    assert image.startswith('data:image/jpeg;base64,') and ttl == 120
    assert _poll(session, ticket) == ('waiting', None, None)
    session.result = {'code': 1, 'msg': 'ticket长度错误'}
    assert _poll(session, ticket)[0] == 'failed'


@pytest.mark.parametrize('message', [
    '二维码已扫描，请在微信确认',
    '等待用户确认',
    '官方状态文案更新',
    '',
    None,
])
def test_unknown_code_one_status_keeps_flow_waiting(message):
    session = Upstream()
    session.result = {'code': 1, 'msg': message}
    assert _poll(session, 'synthetic-ticket') == ('waiting', None, None)


@pytest.mark.parametrize('url', [
    'http://mp.weixin.qq.com/cgi-bin/showqrcode?ticket=synthetic-ticket',
    'https://mp.weixin.qq.com:443/cgi-bin/showqrcode?ticket=synthetic-ticket',
    'https://evil.invalid/cgi-bin/showqrcode?ticket=synthetic-ticket',
    'https://mp.weixin.qq.com/cgi-bin/showqrcode?ticket=other',
    'https://mp.weixin.qq.com/cgi-bin/showqrcode?ticket=synthetic-ticket&redirect=evil',
    'https://user@mp.weixin.qq.com/cgi-bin/showqrcode?ticket=synthetic-ticket',
])
def test_reject_unsafe_image_url(url):
    session = Upstream(); session.image_url = url
    with pytest.raises(ValueError): _create(session)


def test_success_requires_current_identity_not_callback():
    session = Upstream()
    session.result = {'code': 0, 'data': {'callBack': 'https://evil.invalid/'}}
    assert _poll(session, 'synthetic-ticket') == ('verified', '123', 'test_session=SYNTHETIC')
    session.page = b'<a href="/profile/123">Public profile</a>'
    assert _poll(session, 'synthetic-ticket')[0] == 'unverified'
    session.result['data']['needComplete'] = True
    assert _poll(session, 'synthetic-ticket')[0] == 'incomplete'


def test_null_success_status_sets_cookie_then_verifies_fixed_homepage():
    session = AnonymousUpstream()
    session.adapter.status_set_cookie = True
    assert not session.cookies
    assert _poll(session, 'synthetic-ticket') == ('verified', '123', 'test_session=EXCHANGED')
    assert session.adapter.calls == [
        ORIGIN + '/nccommon/oauth2/login/wechat_mp_status?ticket=synthetic-ticket', ORIGIN + '/']
    assert session.cookies.get('test_session') == 'EXCHANGED'


@pytest.mark.parametrize('set_cookie, page', [
    (False, None),
    (True, b'<script>window.isLogin = false;</script>'),
    (False, Upstream.page),
])
def test_null_success_needs_authenticated_homepage_and_valid_cookie(set_cookie, page):
    session = AnonymousUpstream()
    session.adapter.status_set_cookie = set_cookie
    session.adapter.home_page = page
    assert _poll(session, 'synthetic-ticket') == ('unverified', None, None)


@pytest.mark.parametrize('value', [
    {'code': 0, 'data': data} for data in [[], '', 'unexpected', 1, False]
])
def test_malformed_status_payload_types_fail_without_homepage(monkeypatch, value):
    monkeypatch.setattr('acmweb.nowcoder_qr._json', lambda *a, **kw: value)
    def unexpected(*a, **kw):
        pytest.fail('unknown payload must not trigger homepage request')
    monkeypatch.setattr('acmweb.nowcoder_qr._body', unexpected)
    assert _poll(object(), 'synthetic-ticket') == ('failed', None, None)


def test_success_without_data_still_requires_authenticated_homepage():
    session = AnonymousUpstream()
    session.adapter.omit_status_data = True
    session.adapter.status_set_cookie = True
    assert _poll(session, 'synthetic-ticket') == ('verified', '123', 'test_session=EXCHANGED')
    assert session.adapter.calls == [
        ORIGIN + '/nccommon/oauth2/login/wechat_mp_status?ticket=synthetic-ticket', ORIGIN + '/']


def test_success_without_data_does_not_bind_without_authenticated_homepage():
    session = AnonymousUpstream()
    session.adapter.omit_status_data = True
    session.adapter.status_set_cookie = False
    session.adapter.home_page = b'<script>window.isLogin = false;</script>'
    assert _poll(session, 'synthetic-ticket') == ('unverified', None, None)
    assert session.adapter.calls[-1] == ORIGIN + '/'


@pytest.mark.parametrize('set_cookie, page', [
    (False, None),
    (True, b'<script>window.isLogin = false;</script>'),
    (False, Upstream.page),
])
def test_missing_data_scan_without_identity_or_session_never_creates_binding(tmp_path, set_cookie, page):
    async def run():
        value = manager(tmp_path)
        value.session_factory = AnonymousUpstream
        started = await value.start('owner', True)
        session = value.flow['session']
        session.adapter.omit_status_data = True
        session.adapter.status_set_cookie = set_cookie
        session.adapter.home_page = page
        try:
            result = await value.poll('owner', started['login_id'])
            assert result['status'] == 'authorizing' and result['retryable'] is True
            assert 'accounts' not in result and 'verified_uid' not in result
            assert not value.accounts.path.exists()
            assert value.checker.statuses()['items'] == []
        finally:
            value.close()
    asyncio.run(run())


@pytest.mark.parametrize('callback', [
    'https://evil.invalid/', ORIGIN + '/Abcd_12-xyZ9/oauth?rtype=1',
])
def test_anonymous_callback_payload_never_triggers_callback_request(callback):
    session = AnonymousUpstream()
    session.adapter.status_data = {'callBack': callback, 'rtype': '1', 'needComplete': False}
    session.adapter.status_set_cookie = False
    assert _poll(session, 'synthetic-ticket') == ('unverified', None, None)
    assert session.adapter.calls == [
        ORIGIN + '/nccommon/oauth2/login/wechat_mp_status?ticket=synthetic-ticket', ORIGIN + '/']


@pytest.mark.parametrize('omit_data', [False, True])
def test_qr_session_binds_empty_configuration_only_after_homepage_identity(tmp_path, omit_data):
    async def run():
        value = manager(tmp_path)
        value.session_factory = AnonymousUpstream
        started = await value.start('owner', True)
        session = value.flow['session']
        session.adapter.omit_status_data = omit_data
        try:
            assert not session.cookies and not value.accounts.path.exists()
            result = await value.poll('owner', started['login_id'])
            assert result['status'] == 'verified' and result['verified_uid'] == '123'
            assert 'EXCHANGED' not in str(result)
            assert value.accounts.document()['accounts'][0]['cookie'] == 'test_session=EXCHANGED'
            assert value.accounts.public()['items'][0]['login_method'] == 'qr'
            assert value.checker.statuses()['items'][0]['login_verified'] is True
        finally:
            value.close()
    asyncio.run(run())


@pytest.mark.parametrize('invalidate', ['cancel', 'expire'])
@pytest.mark.parametrize('phase', ['status_close', 'status_stream', 'home_stream'])
@pytest.mark.parametrize('omit_data', [False, True])
def test_status_homepage_flow_stops_when_cancelled_or_expired(tmp_path, invalidate, phase, omit_data):
    async def run():
        value = manager(tmp_path)
        value.session_factory = AnonymousUpstream
        started = await value.start('owner', True)
        flow = value.flow
        session = flow['session']
        session.adapter.omit_status_data = omit_data
        def interrupt():
            if invalidate == 'cancel':
                value.cancel('owner', started['login_id'])
            else:
                flow['deadline'] = 0
        session.adapter.interrupt_url = (ORIGIN + '/' if phase == 'home_stream' else
            ORIGIN + '/nccommon/oauth2/login/wechat_mp_status?ticket=synthetic-ticket')
        if phase == 'status_close':
            session.adapter.on_close = interrupt
        else:
            session.adapter.on_chunk = interrupt
        try:
            if invalidate == 'cancel':
                with pytest.raises(HTTPException) as error:
                    await value.poll('owner', started['login_id'])
                assert error.value.status_code == 404
            else:
                result = await value.poll('owner', started['login_id'])
                assert result['status'] == 'expired'
            assert session.adapter.calls[-1] == session.adapter.interrupt_url
            assert not hasattr(session, '_acm_qr_guard')
            assert not value.accounts.path.exists()
        finally:
            value.close()
    asyncio.run(run())


def test_body_caps_timeout_and_checks_flow_during_stream():
    session = Upstream()
    checks = []
    def guard():
        checks.append(True)
        if len(checks) > 1:
            raise ValueError('expired during stream')
        return 0.25
    session._acm_qr_guard = guard
    get = session.get
    def checked_get(url, **kwargs):
        assert kwargs['timeout'] == (0.25, 0.25)
        return get(url, **kwargs)
    session.get = checked_get
    with pytest.raises(ValueError, match='expired during stream'):
        _body(session, ORIGIN + '/', 1048576)
    assert len(checks) == 2


def test_owner_throttle_and_verified_binding(tmp_path):
    async def run():
        value = manager(tmp_path)
        claims = []
        value.checker.on_verified = lambda platform, handle: claims.append((platform, handle))
        result = await value.start('owner', True)
        try:
            with pytest.raises(HTTPException) as error:
                await value.poll('other', result['login_id'])
            assert error.value.status_code == 404
            waiting = await value.poll('owner', result['login_id'])
            assert waiting['status'] == 'waiting' and waiting['login_id'] == result['login_id']
            assert claims == []
            with pytest.raises(HTTPException) as error:
                await value.poll('owner', result['login_id'])
            assert error.value.status_code == 429
            value.flow['last_poll'] = 0
            value.flow['session'].result = {'code': 0, 'data': {}}
            login_id = result['login_id']
            result = await value.poll('owner', login_id)
            assert result['login_id'] == login_id
            assert result['status'] == 'verified' and result['verified_uid'] == '123'
            assert 'SYNTHETIC' not in str(result)
            assert value.checker.statuses()['items'][0]['login_verified'] is True
            assert claims == [('nowcoder', '123')]
            assert value.accounts.document()['accounts'][0]['cookie'] == 'test_session=SYNTHETIC'
            assert value.accounts.document()['accounts'][0]['login_method'] == 'qr'
            public = value.accounts.public()['items'][0]
            assert public['login_method'] == 'qr' and public['has_cookie'] is True
            assert value.flow is None
        finally: value.close()
    asyncio.run(run())


@pytest.mark.parametrize('change', ['mismatch', 'revision', 'revoked', 'crawl'])
def test_failed_commit_never_writes(tmp_path, change):
    async def run():
        value = manager(tmp_path)
        result = await value.start('owner', True, '456' if change == 'mismatch' else None)
        try:
            value.flow['session'].result = {'code': 0, 'data': {}}
            if change == 'revision': value.flow['revision'] = 'stale'
            if change == 'revoked': value.owner_valid = lambda _: False
            if change == 'crawl': value.jobs.tasks = {'task': ('crawl', SimpleNamespace(done=lambda: False))}
            try:
                result = await value.poll('owner', result['login_id'])
                assert result['status'] == 'failed'
            except HTTPException as error: assert error.status_code in {401, 409}
            assert not value.accounts.path.exists()
        finally: value.close()
    asyncio.run(run())


def test_expiry_and_cancel_close_without_credentials(tmp_path):
    async def run():
        value = manager(tmp_path)
        result = await value.start('owner', True)
        session = value.flow['session']
        value._expire('owner', result['login_id'])
        assert session.closed
        assert (await value.poll('owner', result['login_id']))['status'] == 'expired'
        assert not value.accounts.path.exists()
        value.close()
    asyncio.run(run())


def test_start_expired_during_network_and_old_id_cannot_clear_replacement(tmp_path, monkeypatch):
    async def run():
        value = manager(tmp_path)
        original_create = __import__('acmweb.nowcoder_qr', fromlist=['_create'])._create
        def slow_create(session):
            result = original_create(session)
            value.flow['deadline'] = 0  # Synthetic elapsed deadline, no real sleep.
            return result
        monkeypatch.setattr('acmweb.nowcoder_qr._create', slow_create)
        expired = await value.start('owner', True)
        assert expired['status'] == 'expired' and expired['login_id']
        assert 'qr_image' not in expired and value.flow is None
        assert (await value.poll('owner', expired['login_id']))['login_id'] == expired['login_id']
        monkeypatch.setattr('acmweb.nowcoder_qr._create', original_create)
        value.last_start = 0
        current = await value.start('owner', True)
        with pytest.raises(HTTPException) as error:
            value.cancel('owner', expired['login_id'])
        assert error.value.status_code == 404
        value._expire('owner', expired['login_id'])
        assert value.flow['id'] == current['login_id']
        cancelled = value.cancel('owner', current['login_id'])
        assert cancelled['login_id'] == current['login_id'] and cancelled['status'] == 'cancelled'
        value.close()
    asyncio.run(run())


def test_verified_qr_replaces_cookie_alternative_without_requesting_another_cookie(tmp_path):
    from acmweb.accounts import AccountList
    async def run():
        value = manager(tmp_path)
        value.accounts.save(AccountList(items=[
            {'platform': 'nowcoder', 'handle': '123', 'cookie': 'old_session=OLD-SYNTHETIC'},
            {'platform': 'atcoder', 'handle': 'fixture', 'cookie': 'sid=KEEP-SYNTHETIC'},
        ]))
        before = value.accounts.public()['items'][0]
        assert before['login_method'] == 'cookie'
        started = await value.start('owner', True, '123')
        try:
            value.flow['session'].result = {'code': 0, 'data': None}
            result = await value.poll('owner', started['login_id'])
            assert result['status'] == 'verified' and result['verified_uid'] == '123'
            saved = value.accounts.document()['accounts']
            assert saved[0]['cookie'] == 'test_session=SYNTHETIC'
            assert saved[0]['login_method'] == 'qr'
            assert saved[1]['cookie'] == 'sid=KEEP-SYNTHETIC'
            assert 'SYNTHETIC' not in str(result)
        finally:
            value.close()
    asyncio.run(run())


def test_unbound_wechat_scan_does_not_replace_existing_account_credentials(tmp_path):
    from acmweb.accounts import AccountList
    async def run():
        value = manager(tmp_path)
        value.accounts.save(AccountList(items=[{'platform': 'nowcoder', 'handle': '123', 'cookie': 'old_session=KEEP-SYNTHETIC'}]))
        before = value.accounts.path.read_bytes()
        started = await value.start('owner', True)
        try:
            value.flow['session'].result = {'code': 0, 'data': {'needComplete': True}}
            result = await value.poll('owner', started['login_id'])
            assert result['status'] == 'failed'
            assert '完善账号' in result['message']
            assert value.accounts.path.read_bytes() == before
        finally:
            value.close()
    asyncio.run(run())


def _synthetic_response(request, body, status=200, mime='application/json'):
    """A local adapter response; no real QR request or credentials are used."""
    response = requests.Response()
    response.status_code = status
    response.request = request
    response.url = request.url
    response.headers['Content-Type'] = mime
    response._content = body
    response._content_consumed = True
    return response


@pytest.mark.parametrize('failure', ['timeout', 'connection', 'http429', 'http503', 'json', 'unexpected', 'typed'])
def test_poll_temporary_failure_preserves_flow_and_retries_without_leaking_details(tmp_path, monkeypatch, caplog, failure):
    async def run():
        value = manager(tmp_path)
        value.session_factory = AnonymousUpstream
        started = await value.start('owner', True)
        flow = value.flow
        session, deadline, ticket = flow['session'], flow['deadline'], flow['ticket']
        timer = flow['timer']
        send = session.adapter.send
        interrupted = False

        def flaky_send(request, **kwargs):
            nonlocal interrupted
            if '/wechat_mp_status?' in request.url and not interrupted:
                interrupted = True
                if failure == 'timeout':
                    raise requests.Timeout('PRIVATE-TICKET PRIVATE-COOKIE')
                if failure == 'connection':
                    raise requests.ConnectionError('PRIVATE-TICKET PRIVATE-COOKIE')
                if failure == 'unexpected':
                    raise RuntimeError('PRIVATE-TICKET PRIVATE-COOKIE')
                if failure == 'typed':
                    from acmweb.nowcoder_qr import QRUpstreamError
                    raise QRUpstreamError('PRIVATE-TICKET PRIVATE-COOKIE', upstream_status=999999)
                if failure == 'json':
                    return _synthetic_response(request, b'PRIVATE-TICKET invalid JSON PRIVATE-COOKIE')
                return _synthetic_response(request, b'PRIVATE-TICKET PRIVATE-COOKIE',
                                           status=429 if failure == 'http429' else 503)
            return send(request, **kwargs)

        monkeypatch.setattr(session.adapter, 'send', flaky_send)
        try:
            result = await value.poll('owner', started['login_id'])
            assert result['status'] == 'waiting' and result['retryable'] is True
            assert isinstance(result['reason'], str) and result['reason']
            assert result['poll_after_seconds'] == 5
            if failure in {'http429', 'http503'}:
                assert result['reason'] == 'upstream_http'
                assert result['upstream_status'] == (429 if failure == 'http429' else 503)
            elif failure in {'typed', 'unexpected'}:
                assert result['reason'] == 'status_check_unavailable'
                assert result['upstream_status'] is None
            assert 'PRIVATE' not in str(result)
            assert value.flow is flow and flow['session'] is session
            assert flow['deadline'] == deadline and flow['ticket'] == ticket
            assert flow['timer'] is timer
            assert not flow['busy'] and not value.accounts.path.exists()
            assert not any(hasattr(session, name) for name in (
                '_acm_qr_guard', '_acm_qr_confirm', '_acm_qr_observe'))
            flow['last_poll'] = 0
            retried = await value.poll('owner', started['login_id'])
            assert retried['status'] == 'verified' and retried['verified_uid'] == '123'
        finally:
            value.close()
    asyncio.run(run())
    assert 'PRIVATE' not in caplog.text


def test_busy_network_pool_does_not_discard_valid_qr_flow(tmp_path, monkeypatch):
    import acmweb.nowcoder_qr as qr

    class BusyNetwork:
        def acquire(self, blocking): return False
        def release(self): pytest.fail('an unacquired network slot must not be released')

    async def run():
        value = manager(tmp_path)
        started = await value.start('owner', True)
        flow = value.flow
        network = qr.NETWORK
        monkeypatch.setattr(qr, 'NETWORK', BusyNetwork())
        try:
            result = await value.poll('owner', started['login_id'])
            assert result['status'] == 'waiting' and result['retryable'] is True
            assert result['reason'] == 'network_busy'
            assert value.flow is flow and not flow['busy']
            assert not value.accounts.path.exists()
            monkeypatch.setattr(qr, 'NETWORK', network)
            flow['last_poll'] = 0
            flow['session'].result = {'code': 0, 'data': None}
            assert (await value.poll('owner', started['login_id']))['status'] == 'verified'
        finally:
            value.close()
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['timeout', 'http503', 'anonymous'])
@pytest.mark.parametrize('omit_data', [False, True])
def test_confirmed_scan_retries_only_identity_and_keeps_session_cookie(tmp_path, monkeypatch, failure, omit_data):
    async def run():
        value = manager(tmp_path)
        value.session_factory = AnonymousUpstream
        started = await value.start('owner', True)
        flow = value.flow
        session, deadline = flow['session'], flow['deadline']
        session.adapter.omit_status_data = omit_data
        timer = flow['timer']
        send = session.adapter.send
        calls = []
        home_failed = False

        def delayed_identity(request, **kwargs):
            nonlocal home_failed
            calls.append(request.url)
            if request.url == ORIGIN + '/' and not home_failed:
                home_failed = True
                if failure == 'timeout':
                    raise requests.Timeout('PRIVATE-COOKIE PRIVATE-TICKET')
                if failure == 'http503':
                    return _synthetic_response(request, b'PRIVATE-COOKIE upstream busy', status=503)
                return _synthetic_response(request, b'<script>window.isLogin = false;</script>', mime='text/html')
            return send(request, **kwargs)

        monkeypatch.setattr(session.adapter, 'send', delayed_identity)
        try:
            result = await value.poll('owner', started['login_id'])
            assert result['status'] == 'authorizing' and result['retryable'] is True
            assert result['reason']
            assert 'PRIVATE' not in str(result)
            assert value.flow is flow and flow['confirmed'] is True
            assert flow['deadline'] == deadline and flow['session'] is session
            assert flow['timer'] is timer
            assert session.cookies.get('test_session') == 'EXCHANGED'
            assert not value.accounts.path.exists()
            flow['last_poll'] = 0
            verified = await value.poll('owner', started['login_id'])
            assert verified['status'] == 'verified' and verified['verified_uid'] == '123'
            assert calls == [
                ORIGIN + '/nccommon/oauth2/login/wechat_mp_status?ticket=synthetic-ticket',
                ORIGIN + '/', ORIGIN + '/',
            ]
            assert value.accounts.document()['accounts'][0]['cookie'] == 'test_session=EXCHANGED'
        finally:
            value.close()
    asyncio.run(run())


@pytest.mark.parametrize('data', [None, {}, {'callBack': 'https://attacker.invalid/'}])
def test_legal_success_payload_marks_confirmation_before_identity_request(data):
    session = Upstream()
    session.result = {'code': 0, 'data': data}
    confirmations = []
    session._acm_qr_confirm = lambda: confirmations.append(True)
    original_get = session.get

    def get(url, **kwargs):
        if url == ORIGIN + '/':
            assert confirmations == [True]
        return original_get(url, **kwargs)

    session.get = get
    try:
        assert _poll(session, 'synthetic-ticket')[0] == 'verified'
        assert confirmations == [True]
    finally:
        session.close()


@pytest.mark.parametrize('payload', [
    {'code': 1, 'msg': '二维码未被扫描'}, {'code': '0', 'data': {}},
    {'code': 0, 'data': []}, {'code': 0, 'data': {'needComplete': True}},
])
def test_pending_malformed_or_incomplete_status_never_marks_confirmation(payload):
    session = Upstream()
    session.result = payload
    confirmations = []
    session._acm_qr_confirm = lambda: confirmations.append(True)
    try:
        assert _poll(session, 'synthetic-ticket')[0] != 'verified'
        assert confirmations == []
    finally:
        session.close()


@pytest.mark.parametrize('omit_data', [False, True])
def test_expired_identity_pending_flow_preserves_original_account_credentials(tmp_path, omit_data):
    from acmweb.accounts import AccountList

    async def run():
        value = manager(tmp_path)
        value.accounts.save(AccountList(items=[{'platform': 'nowcoder', 'handle': '123',
                                               'cookie': 'sid=KEEP-SYNTHETIC'}]))
        before = value.accounts.path.read_bytes()
        value.session_factory = AnonymousUpstream
        started = await value.start('owner', True)
        flow = value.flow
        flow['session'].adapter.omit_status_data = omit_data
        flow['session'].adapter.home_page = b'<script>window.isLogin = false;</script>'
        try:
            pending = await value.poll('owner', started['login_id'])
            assert pending['status'] == 'authorizing' and flow['confirmed'] is True
            assert value.accounts.path.read_bytes() == before
            flow['deadline'] = 0
            expired = await value.poll('owner', started['login_id'])
            assert expired['status'] == 'expired'
            assert expired['reason'] == 'identity_verification_timeout'
            assert value.flow is None
            assert value.accounts.path.read_bytes() == before
        finally:
            value.close()
    asyncio.run(run())


@pytest.mark.parametrize('payload', [
    {'code': 1, 'msg': 'ticket长度错误'}, {'code': '0', 'data': {}},
    {'code': 0, 'data': []}, {'code': 0, 'data': {'needComplete': True}},
])
def test_explicit_rejected_or_malformed_status_terminates_without_writing_credentials(tmp_path, payload):
    async def run():
        value = manager(tmp_path)
        started = await value.start('owner', True)
        value.flow['session'].result = payload
        try:
            result = await value.poll('owner', started['login_id'])
            assert result['status'] == 'failed' and result['retryable'] is False
            assert value.flow is None
            assert not value.accounts.path.exists()
        finally:
            value.close()
    asyncio.run(run())

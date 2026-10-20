"""Synthetic upstream responses test the QR boundary, not real WeChat login."""
import asyncio
import json
from types import SimpleNamespace

import pytest
import requests
from fastapi import HTTPException
from acmweb.accounts import Accounts
from acmweb.credential_check import CredentialChecks
from acmweb.nowcoder_qr import NowcoderQR, _create, _poll
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


def test_owner_throttle_and_verified_binding(tmp_path):
    async def run():
        value = manager(tmp_path)
        result = await value.start('owner', True)
        try:
            with pytest.raises(HTTPException) as error:
                await value.poll('other', result['login_id'])
            assert error.value.status_code == 404
            waiting = await value.poll('owner', result['login_id'])
            assert waiting['status'] == 'waiting' and waiting['login_id'] == result['login_id']
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
            assert value.accounts.document()['accounts'][0]['cookie'] == 'test_session=SYNTHETIC'
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

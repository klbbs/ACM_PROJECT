"""Ephemeral, fixed-origin Nowcoder WeChat login. No ticket leaves this module."""
import asyncio
import base64
import math
import secrets
import threading
import time
from urllib.parse import urlsplit, parse_qs

import requests
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .codex_bridge import AIError
from .credential_check import diagnostics, inspect_identity
from .cookie_validation import valid_cookie
from ojcrawler.http import browser_headers

ORIGIN = 'https://ac.nowcoder.com'
NETWORK = threading.BoundedSemaphore(4)
PENDING_LOCK = threading.Lock()
PENDING = set()


class Start(BaseModel):
    model_config = ConfigDict(extra='forbid')
    consent: bool = Field(strict=True)
    expected_handle: str | None = Field(default=None, pattern=r'^[1-9][0-9]{0,19}$')


class FlowID(BaseModel):
    model_config = ConfigDict(extra='forbid')
    login_id: str = Field(min_length=20, max_length=100)


def _request_timeout(session):
    guard = getattr(session, '_acm_qr_guard', None)
    remaining = guard() if guard else None
    return (5, 12) if remaining is None else (min(5, remaining), min(12, remaining))


def _check_network_flow(session):
    guard = getattr(session, '_acm_qr_guard', None)
    if guard:
        guard()


def _body(session, url, maximum, **kwargs):
    headers = {**browser_headers(), **kwargs.pop('headers', {})}
    with session.get(url, headers=headers, timeout=_request_timeout(session), allow_redirects=False, stream=True, **kwargs) as response:
        if response.status_code != 200:
            raise ValueError('upstream')
        chunks = []; size = 0
        for part in response.iter_content(16384):
            _check_network_flow(session)
            size += len(part)
            if size > maximum:
                raise ValueError('size')
            chunks.append(part)
        return b''.join(chunks), response.headers.get('Content-Type', '').split(';')[0].lower()


def _json(session, path, **kwargs):
    import json
    body, mime = _body(session, ORIGIN + path, 65536, **kwargs)
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError('json')
    return value


def _create(session):
    _body(session, ORIGIN + '/login?callBack=/', 1048576)
    value = _json(session, '/nccommon/oauth2/login/wechat_qr_code')
    data = value.get('data')
    if type(value.get('code')) is not int or value['code'] != 0 or not isinstance(data, dict):
        raise ValueError('qr')
    ticket = data.get('ticket'); url = data.get('imageUrl'); ttl = data.get('expireSecond')
    if not isinstance(ticket, str) or not 1 <= len(ticket) <= 4096 or not isinstance(url, str):
        raise ValueError('ticket')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'mp.weixin.qq.com' or
            parsed.path != '/cgi-bin/showqrcode' or parsed.fragment or
            parse_qs(parsed.query, keep_blank_values=True) != {'ticket': [ticket]}):
        raise ValueError('image origin')
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not math.isfinite(ttl) or ttl <= 0:
        raise ValueError('ttl')
    image, mime = _body(session, url, 262144)
    if mime in {'image/jpeg', 'image/jpg'} and image.startswith(b'\xff\xd8\xff'):
        mime = 'image/jpeg'
    elif mime == 'image/png' and image.startswith(b'\x89PNG\r\n\x1a\n'):
        pass
    else:
        raise ValueError('image')
    return ticket, 'data:' + mime + ';base64,' + base64.b64encode(image).decode(), min(120, ttl)


def _poll(session, ticket):
    value = _json(session, '/nccommon/oauth2/login/wechat_mp_status', params={'ticket': ticket})
    if type(value.get('code')) is not int:
        return 'failed', None, None
    if value.get('code') == 1:
        message = value.get('msg')
        # The official endpoint uses code=1 for pending QR states, but its
        # wording varies with scan/confirmation state. Only the known malformed
        # ticket response is terminal; all other code=1 states remain bounded
        # by the QR expiry timer and never authorize binding.
        if isinstance(message, str) and message.strip() == 'ticket长度错误':
            return 'failed', None, None
        return 'waiting', None, None
    if value.get('code') != 0:
        return 'failed', None, None
    if 'data' not in value:
        return 'failed', None, None
    data = value.get('data')
    # Some successful official responses complete login via Set-Cookie and
    # return JSON null. They still need the fixed homepage identity check.
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return 'failed', None, None
    if data.get('needComplete'):
        return 'incomplete', None, None
    # Only the fixed ACM homepage's authenticated bootstrap authorizes binding.
    # Upstream callBack URLs are never requested.
    page, _ = _body(session, ORIGIN + '/', 1048576)
    status, uid = inspect_identity('nowcoder', page.decode('utf-8'))
    if status != 'authenticated' or not uid:
        return 'unverified', None, None
    request = session.prepare_request(requests.Request('GET', ORIGIN + '/'))
    cookie = request.headers.get('Cookie', '')
    if not valid_cookie(cookie):
        return 'unverified', None, None
    return 'verified', uid, cookie


class NowcoderQR:
    def __init__(self, accounts, jobs, checker, session_factory=requests.Session):
        self.accounts, self.jobs, self.checker = accounts, jobs, checker
        self.session_factory = session_factory
        self.lock = threading.RLock()
        self.flow = None
        self.generation = 0
        self.last_start = 0
        self.closed = False
        self.owner_valid = lambda owner: True
        self.expired = None

    def _discard(self):
        flow = self.flow
        self.generation += 1
        self.flow = None
        if flow:
            if flow.get('timer'):
                flow['timer'].cancel()
            with PENDING_LOCK:
                PENDING.discard(flow['id'])
            # Requests may still be using the session in a worker. That worker
            # owns close until it returns; generation prevents a stale commit.
            flow['cancelled'] = True
            if not flow['busy']:
                flow['session'].close()
            flow['ticket'] = None

    def clear(self, owner=None):
        with self.lock:
            if self.flow and (owner is None or owner == self.flow['owner']):
                self._discard()

    def _expire(self, owner, login_id):
        with self.lock:
            if self.flow and self.flow['owner'] == owner and self.flow['id'] == login_id:
                self.expired = (owner, login_id)
                self._discard()

    def close(self):
        with self.lock:
            self.closed = True
            self._discard()

    def _current(self, owner, login_id):
        if not self.owner_valid(owner):
            self.clear(owner)
            raise HTTPException(401, '登录会话已失效，请重新登录 ACM Lab')
        flow = self.flow
        if not flow or flow['owner'] != owner or not secrets.compare_digest(flow['id'], login_id):
            raise HTTPException(404, '扫码流程不存在')
        return flow

    async def _network(self, flow, function, *args):
        def guard():
            with self.lock:
                remaining = flow['deadline'] - time.monotonic()
                if flow.get('cancelled') or remaining <= 0:
                    raise ValueError('inactive flow')
                return remaining
        def run():
            try:
                if not NETWORK.acquire(blocking=False):
                    raise ValueError('busy')
                try:
                    flow['session']._acm_qr_guard = guard
                    return function(flow['session'], *args)
                finally:
                    if getattr(flow['session'], '_acm_qr_guard', None) is guard:
                        del flow['session']._acm_qr_guard
                    NETWORK.release()
            finally:
                # A cancelled coroutine must not close a Session still in use.
                with self.lock:
                    if flow.get('cancelled'):
                        flow['session'].close()
        task = asyncio.create_task(asyncio.to_thread(run))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            with self.lock:
                if self.flow is flow:
                    self._discard()
                flow['cancelled'] = True
            def finished(done):
                if not done.cancelled():
                    done.exception()
                flow['session'].close()
            task.add_done_callback(finished)
            raise

    async def start(self, owner, consent, expected_handle=None):
        if not consent:
            raise HTTPException(400, '请先同意使用牛客官方微信扫码登录')
        with self.lock:
            now = time.monotonic()
            if not self.owner_valid(owner):
                raise HTTPException(401, '登录会话已失效，请重新登录 ACM Lab')
            if self.closed:
                raise HTTPException(503, '扫码服务已关闭')
            if now - self.last_start < 10:
                raise HTTPException(429, '请等待 10 秒后重新获取二维码', headers={'Retry-After': '10'})
            self.last_start = now
            self.expired = None
            self._discard()
            revision = self.accounts.revision()
            flow_id = secrets.token_urlsafe(32)
            with PENDING_LOCK:
                if len(PENDING) >= 32:
                    raise HTTPException(429, '扫码服务繁忙，请稍后重试')
                PENDING.add(flow_id)
            try:
                session = self.session_factory()
            except Exception:
                with PENDING_LOCK:
                    PENDING.discard(flow_id)
                raise HTTPException(503, '扫码服务暂时不可用') from None
            flow = dict(id=flow_id, owner=owner, session=session,
                        expected=expected_handle, revision=revision,
                        deadline=now + 120, busy=True, ticket=None, last_poll=0,
                        cancelled=False, timer=None, generation=self.generation)
            self.flow = flow
        try:
            ticket, image, ttl = await self._network(flow, _create)
        except Exception:
            with self.lock:
                flow['busy'] = False
                if self.flow is flow:
                    self._discard()
                else:
                    flow['session'].close()
            raise HTTPException(502, '牛客二维码获取失败，请稍后重试或使用 Cookie 绑定') from None
        with self.lock:
            flow['busy'] = False
            if not self.owner_valid(owner):
                if self.flow is flow:
                    self._discard()
                flow['session'].close()
                raise HTTPException(401, '登录会话已失效，请重新登录 ACM Lab')
            if self.flow is not flow or flow['cancelled']:
                flow['session'].close()
                raise HTTPException(409, '扫码流程已取消或被替换')
            flow['ticket'] = ticket
            flow['deadline'] = min(flow['deadline'], time.monotonic() + ttl)
            remaining = max(0, flow['deadline'] - time.monotonic())
            if remaining <= 0:
                self._expire(owner, flow_id)
                return self._result(flow_id, 'expired', '二维码获取期间已过期，请重新获取')
            flow['timer'] = asyncio.get_running_loop().call_later(remaining, self._expire, owner, flow_id)
            return dict(login_id=flow_id, status='waiting', qr_image=image,
                        expires_at=int(time.time() + remaining), poll_after_seconds=3,
                        method='wechat', message='请使用微信扫描牛客官方二维码，并在手机上确认登录')

    async def poll(self, owner, login_id):
        with self.lock:
            if self.owner_valid(owner) and self.expired == (owner, login_id):
                return self._result(login_id, 'expired', '二维码已过期，请重新获取')
            flow = self._current(owner, login_id)
            now = time.monotonic()
            if now >= flow['deadline']:
                self._discard()
                return self._result(login_id, 'expired', '二维码已过期，请重新获取')
            if flow['busy'] or now - flow['last_poll'] < 3:
                raise HTTPException(429, '请等待 3 秒后查询扫码状态', headers={'Retry-After': '3'})
            flow['busy'] = True
            flow['last_poll'] = now
            ticket = flow['ticket']
        try:
            status, uid, cookie = await self._network(flow, _poll, ticket)
        except Exception:
            status, uid, cookie = 'failed', None, None
        with self.lock:
            flow['busy'] = False
            if not self.owner_valid(owner):
                if self.flow is flow:
                    self._discard()
                flow['session'].close()
                raise HTTPException(401, '登录会话已失效，请重新登录 ACM Lab')
            if self.flow is not flow or flow['cancelled']:
                flow['session'].close()
                raise HTTPException(404, '扫码流程不存在')
            if time.monotonic() >= flow['deadline']:
                self._discard()
                return self._result(login_id, 'expired', '二维码已过期，请重新获取')
            if status == 'waiting':
                return self._result(login_id, 'waiting', '等待微信扫码确认')
            if status == 'verified':
                if flow['expected'] and flow['expected'] != uid:
                    self._discard()
                    return self._result(login_id, 'failed', '扫码账号与指定 UID 不一致，原绑定未修改')
                if any(kind == 'crawl' and not task.done() for kind, task in self.jobs.tasks.values()):
                    self._discard()
                    raise HTTPException(409, '正在采集，请完成后重新扫码绑定')
                try:
                    accounts = self.accounts.bind_verified_account('nowcoder', uid, cookie, flow['revision'], login_method='qr')
                except AIError as exc:
                    self._discard()
                    raise HTTPException(409, str(exc)) from None
                _, digest = self.checker.credential('nowcoder', uid)
                result = diagnostics(dict(platform='nowcoder', handle=uid, checked_at=int(time.time()),
                    status='authenticated', reason='identity_verified', login_verified=True,
                    public_reachable=True, auto_submit_supported=False, submission_method='official_page',
                    retry_after_seconds=10, cached=False, matched_account=uid, upstream_status=200))
                with self.checker.lock:
                    self.checker.cache[('nowcoder', uid)] = (digest, result)
                self._discard()
                return dict(self._result(login_id, 'verified', '牛客当前登录身份已验证并绑定'), verified_uid=uid, accounts=accounts)
            self._discard()
            message = {'incomplete': '请前往牛客官方页面完善账号后重新扫码，或使用 Cookie 绑定',
                       'unverified': '扫码已完成，但尚未验证牛客当前身份；可确认微信已绑定牛客账号后重试，或使用 Cookie 绑定'}.get(status,
                       '牛客未确认本次登录，请重新获取二维码或使用 Cookie 绑定')
            return self._result(login_id, 'failed', message)

    @staticmethod
    def _result(login_id, status, message):
        return dict(login_id=login_id, status=status, message=message, poll_after_seconds=3)

    def cancel(self, owner, login_id):
        with self.lock:
            self._current(owner, login_id)
            self._discard()
        return self._result(login_id, 'cancelled', '扫码登录已取消')


def register(app, accounts, jobs):
    manager = NowcoderQR(accounts, jobs, app.state.credential_checks)
    app.state.nowcoder_qr = manager

    def owner(request):
        value = request.scope.get('state', {}).get('browser_login_owner')
        if getattr(app.state, 'multi_child', False) and not value:
            raise HTTPException(401, '请重新登录 ACM Lab')
        return value or 'local-workspace'

    @app.post('/api/platform-login/nowcoder/start')
    async def start(payload: Start, request: Request):
        return await manager.start(owner(request), payload.consent, payload.expected_handle)

    @app.post('/api/platform-login/nowcoder/poll')
    async def poll(payload: FlowID, request: Request):
        return await manager.poll(owner(request), payload.login_id)

    @app.post('/api/platform-login/nowcoder/cancel')
    async def cancel(payload: FlowID, request: Request):
        return manager.cancel(owner(request), payload.login_id)

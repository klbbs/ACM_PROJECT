"""Ephemeral, fixed-origin Nowcoder WeChat login. No ticket leaves this module."""
import asyncio
import base64
import logging
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
from .runtime_logs import emit_event
from ojcrawler.http import browser_headers

ORIGIN = 'https://ac.nowcoder.com'
NETWORK = threading.BoundedSemaphore(4)
PENDING_LOCK = threading.Lock()
PENDING = set()
log = logging.getLogger(__name__)


class QRUpstreamError(ValueError):
    """Only safe internal reason codes and HTTP status; never retain upstream bodies."""
    def __init__(self, reason, upstream_status=None):
        if reason not in {'upstream_http', 'upstream_response_too_large',
                          'upstream_response_invalid', 'network_busy'}:
            reason = 'status_check_unavailable'
        if type(upstream_status) is not int or not 100 <= upstream_status <= 599:
            upstream_status = None
        super().__init__(reason)
        self.reason = reason
        self.upstream_status = upstream_status


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
            raise QRUpstreamError('upstream_http', response.status_code)
        chunks = []; size = 0
        for part in response.iter_content(16384):
            _check_network_flow(session)
            size += len(part)
            if size > maximum:
                raise QRUpstreamError('upstream_response_too_large')
            chunks.append(part)
        return b''.join(chunks), response.headers.get('Content-Type', '').split(';')[0].lower()


def _json(session, path, **kwargs):
    import json
    body, mime = _body(session, ORIGIN + path, 65536, **kwargs)
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        raise QRUpstreamError('upstream_response_invalid') from None
    if not isinstance(value, dict):
        raise QRUpstreamError('upstream_response_invalid')
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
    observe = getattr(session, '_acm_qr_observe', None)
    if observe:
        observe(value.get('code'))
    def reject(reason):
        if observe:
            observe(value.get('code'), reason)
        return 'failed', None, None
    if type(value.get('code')) is not int:
        return reject('upstream_code_invalid')
    if value.get('code') == 1:
        message = value.get('msg')
        # The official endpoint uses code=1 for pending QR states, but its
        # wording varies with scan/confirmation state. Only the known malformed
        # ticket response is terminal; all other code=1 states remain bounded
        # by the QR expiry timer and never authorize binding.
        if isinstance(message, str) and message.strip() == 'ticket长度错误':
            return reject('invalid_ticket')
        return 'waiting', None, None
    if value.get('code') != 0:
        return reject('upstream_status_rejected')
    if 'data' not in value:
        return reject('success_data_missing')
    data = value.get('data')
    # Some successful official responses complete login via Set-Cookie and
    # return JSON null. They still need the fixed homepage identity check.
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return reject('success_data_invalid')
    if data.get('needComplete'):
        return 'incomplete', None, None
    confirm = getattr(session, '_acm_qr_confirm', None)
    if confirm:
        # Preserve the Set-Cookie-bearing Session even if the next identity
        # request is temporarily unavailable. A QR ticket can be single-use.
        confirm()
    return _identity(session)


def _identity(session):
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
        self.expired_confirmed = False

    def _event(self, event, *, level='INFO', **details):
        emit_event(getattr(self.jobs, 'event_log', None), event, level=level,
                   platform='nowcoder', **details)

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
                self.expired_confirmed = self.flow.get('confirmed', False)
                self._event('platform.qr.expired', level='WARNING',
                            reason='identity_verification_timeout' if self.expired_confirmed else 'qr_expired')
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
        def confirm():
            with self.lock:
                guard()
                flow['confirmed'] = True
        def observe(code, reason=''):
            with self.lock:
                flow['upstream_code'] = code if type(code) is int and abs(code) <= 10**9 else None
                flow['upstream_reason'] = reason
        def run():
            try:
                if not NETWORK.acquire(blocking=False):
                    raise QRUpstreamError('network_busy')
                try:
                    flow['session']._acm_qr_guard = guard
                    flow['session']._acm_qr_confirm = confirm
                    flow['session']._acm_qr_observe = observe
                    return function(flow['session'], *args)
                finally:
                    for name, callback in (('_acm_qr_guard', guard), ('_acm_qr_confirm', confirm),
                                           ('_acm_qr_observe', observe)):
                        if getattr(flow['session'], name, None) is callback:
                            delattr(flow['session'], name)
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
            self.expired_confirmed = False
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
                        cancelled=False, timer=None, generation=self.generation,
                        confirmed=False, upstream_code=None, upstream_reason='', last_diagnostic=None)
            self.flow = flow
        try:
            ticket, image, ttl = await self._network(flow, _create)
        except Exception:
            with self.lock:
                flow['busy'] = False
                if self.flow is flow:
                    self._event('platform.qr.failed', level='WARNING', reason='qr_creation_failed')
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
                return self._expired_result(login_id, self.expired_confirmed)
            flow = self._current(owner, login_id)
            now = time.monotonic()
            if now >= flow['deadline']:
                confirmed = flow['confirmed']
                self._event('platform.qr.expired', level='WARNING',
                            reason='identity_verification_timeout' if confirmed else 'qr_expired')
                self._discard()
                return self._expired_result(login_id, confirmed)
            if flow['busy'] or now - flow['last_poll'] < 3:
                raise HTTPException(429, '请等待 3 秒后查询扫码状态', headers={'Retry-After': '3'})
            flow['busy'] = True
            flow['last_poll'] = now
            ticket = flow['ticket']
            confirmed = flow['confirmed']
        reason, upstream_status = '', None
        try:
            if confirmed:
                status, uid, cookie = await self._network(flow, _identity)
            else:
                status, uid, cookie = await self._network(flow, _poll, ticket)
        except QRUpstreamError as exc:
            status, uid, cookie = 'retrying', None, None
            reason, upstream_status = exc.reason, exc.upstream_status
        except requests.Timeout:
            status, uid, cookie = 'retrying', None, None
            reason = 'upstream_timeout'
        except requests.RequestException:
            status, uid, cookie = 'retrying', None, None
            reason = 'upstream_connection_failed'
        except (ValueError, UnicodeError):
            status, uid, cookie = 'retrying', None, None
            reason = 'upstream_response_invalid'
        except Exception:
            status, uid, cookie = 'retrying', None, None
            reason = 'status_check_unavailable'
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
                confirmed = flow['confirmed']
                self._event('platform.qr.expired', level='WARNING',
                            reason='identity_verification_timeout' if confirmed else 'qr_expired')
                self._discard()
                return self._expired_result(login_id, confirmed)
            if status == 'waiting':
                return self._result(login_id, 'waiting', '等待微信扫码确认')
            if status in {'retrying', 'unverified'}:
                phase = 'identity' if flow['confirmed'] else 'scan_status'
                reason = reason or 'identity_not_ready'
                diagnostic = (phase, reason, upstream_status, flow['upstream_code'])
                if diagnostic != flow['last_diagnostic']:
                    # No ticket, URL, UID, cookie, response body or exception text.
                    log.warning('牛客扫码检测暂未完成：phase=%s reason=%s http=%s code=%s', *diagnostic)
                    self._event('platform.qr.retry', level='WARNING', reason=reason,
                                upstream_status=upstream_status, upstream_code=flow['upstream_code'])
                    flow['last_diagnostic'] = diagnostic
                if status == 'unverified':
                    message = '微信扫码已确认，牛客当前身份尚未就绪；正在继续核验，无需再次扫码。'
                else:
                    detail = {
                        'network_busy': '检测服务暂忙',
                        'upstream_timeout': '牛客请求超时',
                        'upstream_connection_failed': '暂时无法连接牛客',
                        'upstream_http': f'牛客检测接口返回 HTTP {upstream_status}',
                        'upstream_response_invalid': '牛客检测响应暂无法解析',
                        'upstream_response_too_large': '牛客检测响应超出大小限制',
                    }.get(reason, '扫码状态检测暂时不可用')
                    message = detail + '，正在重试；请保留当前二维码，无需重新扫码。'
                return dict(self._result(login_id, 'authorizing' if flow['confirmed'] else 'waiting', message),
                            reason=reason, retryable=True, upstream_status=upstream_status,
                            upstream_code=flow['upstream_code'], poll_after_seconds=5)
            if status == 'verified':
                if flow['expected'] and flow['expected'] != uid:
                    self._event('platform.qr.failed', level='WARNING', reason='account_mismatch')
                    self._discard()
                    return self._result(login_id, 'failed', '扫码账号与指定 UID 不一致，原绑定未修改')
                if any(kind == 'crawl' and not task.done() for kind, task in self.jobs.tasks.values()):
                    self._event('platform.qr.failed', level='WARNING', reason='crawl_in_progress')
                    self._discard()
                    raise HTTPException(409, '正在采集，请完成后重新扫码绑定')
                try:
                    accounts = self.accounts.bind_verified_account('nowcoder', uid, cookie, flow['revision'], login_method='qr')
                except AIError as exc:
                    self._event('platform.qr.failed', level='WARNING', reason='binding_failed')
                    self._discard()
                    raise HTTPException(409, str(exc)) from None
                _, digest = self.checker.credential('nowcoder', uid)
                result = diagnostics(dict(platform='nowcoder', handle=uid, checked_at=int(time.time()),
                    status='authenticated', reason='identity_verified', login_verified=True,
                    public_reachable=True, auto_submit_supported=False, submission_method='official_page',
                    retry_after_seconds=10, cached=False, matched_account=uid, upstream_status=200))
                with self.checker.lock:
                    self.checker.cache[('nowcoder', uid)] = (digest, result)
                self._event('platform.qr.verified', reason='identity_verified')
                self._discard()
                return dict(self._result(login_id, 'verified', '牛客当前登录身份已验证并绑定'), verified_uid=uid, accounts=accounts)
            reason = 'account_incomplete' if status == 'incomplete' else (flow['upstream_reason'] or 'upstream_status_rejected')
            log.warning('牛客扫码已停止：reason=%s code=%s', reason, flow['upstream_code'])
            self._event('platform.qr.failed', level='WARNING', reason=reason,
                        upstream_code=flow['upstream_code'])
            self._discard()
            message = {
                'account_incomplete': '请前往牛客官方页面完善账号后重新扫码，或使用 Cookie 绑定',
                'invalid_ticket': '牛客返回二维码票据无效，请重新获取二维码；原绑定未修改。',
                'upstream_code_invalid': '牛客状态接口未返回可识别的状态码，尚未绑定，请稍后重新获取二维码。',
                'success_data_missing': '牛客返回成功状态但缺少登录数据，尚未绑定，请稍后重新获取二维码。',
                'success_data_invalid': '牛客返回的登录数据格式暂不支持，尚未绑定，请稍后重新获取二维码。',
            }.get(reason, '牛客扫码接口拒绝本次登录或返回暂不支持的状态，请重新获取二维码。')
            return dict(self._result(login_id, 'failed', message),
                        reason=reason,
                        upstream_code=flow['upstream_code'], retryable=False)

    @staticmethod
    def _expired_result(login_id, confirmed):
        message = ('微信扫码已确认，但牛客当前身份未能在有效期内验证，请重新获取二维码；原绑定未修改。'
                   if confirmed else '二维码已过期，请重新获取')
        return dict(NowcoderQR._result(login_id, 'expired', message),
                    reason='identity_verification_timeout' if confirmed else 'qr_expired')

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

"""Owner-scoped, human-operated official login pages in ephemeral Chromium.

Only screenshots and a small input vocabulary cross the HTTP boundary. There
is deliberately no remote URL, JavaScript, CDP, or challenge-solving API.
``clear``/``close`` revoke immediately; await ``aclose`` during app shutdown.
"""
import asyncio
import json
import inspect
import math
import secrets
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from fastapi import HTTPException

from .codex_bridge import AIError
from .browser_runtime import browser_environment
from .cookie_validation import valid_cookie
from .credential_check import URLS, inspect_identity

try:
    from playwright.async_api import async_playwright
except ImportError:  # Browser login is optional; the remaining app still works.
    async_playwright = None


LOGIN_URLS = {
    'codeforces': 'https://codeforces.com/enter',
    'atcoder': 'https://atcoder.jp/login',
    'luogu': 'https://www.luogu.com.cn/auth/login',
}
ROOT_HOSTS = {
    'codeforces': 'codeforces.com',
    'atcoder': 'atcoder.jp',
    'luogu': 'luogu.com.cn',
}
WIDTH, HEIGHT = 1000, 720
MAX_SECONDS, IDLE_SECONDS = 300, 180
MAX_HTML, MAX_SCREENSHOT = 2 * 1024 * 1024, 4 * 1024 * 1024
MAX_TEXT = 512
KEYS = frozenset({
    'Enter', 'Tab', 'Shift+Tab', 'Backspace', 'Delete', 'Escape', 'Space',
    'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End',
    'Control+A', 'Meta+A', 'ControlOrMeta+A',
})
# Shared by every per-user manager in this process. The slot is held until the
# previous browser and Playwright transport have actually finished closing.
_SLOT = threading.BoundedSemaphore(1)
_RESPONSE_PATTERNS = [{'urlPattern': '*', 'requestStage': 'Response'}]
_AUTO_ATTACH = {'autoAttach': True, 'waitForDebuggerOnStart': True, 'flatten': False}
END_MESSAGES = {
    'cancelled': '浏览器登录已取消，请重新开始',
    'owner_revoked': '本站登录会话已失效，浏览器登录已停止',
    'service_closed': '浏览器登录服务已关闭',
    'expired': '浏览器登录已超过有效期，请重新开始',
    'configuration_changed': '登录期间账号配置已更改，请重新登录',
    'crawl_started': '采集已开始，浏览器登录已停止',
    'unsafe_navigation': '官方页面离开了允许的登录站点，登录已停止',
    'unsafe_redirect': '官方页面返回了不允许的跳转，登录已停止',
    'guard_capability_unavailable': '服务器浏览器缺少必需的网络防护能力，登录已停止',
    'guard_protocol_error': '服务器浏览器网络防护连接中断，登录已停止',
    'startup_failed': '服务器无法启动或连接官方浏览器页面',
    'identity_verified': '官方当前登录身份已验证，登录窗口已关闭',
}


class _ProtocolFailure(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('browser guard ' + reason)


def _protocol_reason(error):
    """Classify only fixed protocol phrases; never retain the remote message."""
    if isinstance(error, _ProtocolFailure):
        return error.reason
    message = str(error).casefold()
    if any(phrase in message for phrase in (
            'no session with given id', 'session with given id not found',
            'no target with given id', 'target closed', 'session closed',
            'target page, context or browser has been closed')):
        return 'target_closed'
    if any(phrase in message for phrase in ("wasn't found", 'method not found',
                                           'not supported')):
        return 'unsupported_capability'
    if any(phrase in message for phrase in ('invalid interceptionid',
                                           'invalid interception id')):
        return 'request_gone'
    return 'protocol_failed'


class _ChildCDPSession:
    """Private non-flattened protocol transport for paused OOPIF/worker targets.

    Playwright exposes a page CDPSession, but no public child-session factory.
    Target.sendMessageToTarget keeps child sessions inside that private parent;
    it never opens a debugging port or accepts a command from the HTTP API.
    """
    def __init__(self, parent, session_id):
        self.parent, self.id = parent, session_id
        self.sequence = 0
        self.pending, self.handlers = {}, {}
        self.closed = False
        parent.on('Target.receivedMessageFromTarget', self._receive)
        parent.on('Target.detachedFromTarget', self._detached)

    def on(self, event, callback):
        self.handlers.setdefault(event, []).append(callback)

    async def send(self, method, params=None):
        if self.closed:
            raise _ProtocolFailure('target_closed')
        self.sequence += 1
        command_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[command_id] = future
        try:
            await self.parent.send('Target.sendMessageToTarget', {
                'sessionId': self.id,
                'message': json.dumps({'id': command_id, 'method': method,
                                       'params': params or {}})})
            return await asyncio.wait_for(future, timeout=10)
        finally:
            self.pending.pop(command_id, None)
            if future.done() and not future.cancelled():
                future.exception()
            elif not future.done():
                future.cancel()

    async def _receive(self, event):
        if self.closed or event.get('sessionId') != self.id:
            return
        try:
            message = json.loads(event.get('message', ''))
        except (ValueError, TypeError):
            return
        if not isinstance(message, dict):
            return
        if 'id' in message:
            future = self.pending.get(message['id'])
            if future and not future.done():
                if 'error' in message:
                    future.set_exception(_ProtocolFailure(_protocol_reason(
                        message['error'].get('message', ''))))
                else:
                    future.set_result(message.get('result', {}))
            return
        for callback in self.handlers.get(message.get('method'), ()):
            result = callback(message.get('params', {}))
            if inspect.isawaitable(result):
                await result

    def _detached(self, event):
        if event.get('sessionId') == self.id:
            self.closed = True
            for future in tuple(self.pending.values()):
                if not future.done():
                    future.set_exception(_ProtocolFailure('target_closed'))


def allowed_url(platform, url, *, top_level=False):
    """Allow HTTPS platform traffic and Cloudflare's embedded challenge only."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        if (platform not in ROOT_HOSTS or parsed.scheme != 'https'
                or parsed.username or parsed.password or parsed.port is not None
                or not host or host.endswith('.')):
            return False
        root = ROOT_HOSTS[platform]
        if top_level:
            return host in {root, 'www.' + root}
        return (host == root or host.endswith('.' + root)
                or host == 'challenges.cloudflare.com')
    except (ValueError, TypeError):
        return False


def _cookie_header(platform, cookies):
    """The caller asks Chromium for cookies matching the fixed identity URL."""
    host = urlsplit(URLS[platform]).hostname
    pairs = []
    for cookie in sorted(cookies, key=lambda item: -len(item.get('path', '/'))
                         if isinstance(item, dict) and isinstance(item.get('path', '/'), str) else 0):
        if not isinstance(cookie, dict):
            continue
        domain = cookie.get('domain', '')
        if not isinstance(domain, str):
            continue
        domain = domain.lstrip('.')
        # Do not import an embedded challenge provider's credentials.
        if not domain or not (host == domain or host.endswith('.' + domain)):
            continue
        if domain != ROOT_HOSTS[platform] and not domain.endswith('.' + ROOT_HOSTS[platform]):
            continue
        name, value = cookie.get('name'), cookie.get('value')
        if not isinstance(name, str) or not isinstance(value, str) or ';' in value:
            return None
        pair = name + '=' + value
        if valid_cookie(pair):
            pairs.append(pair)
        else:
            return None
    value = '; '.join(pairs)
    return value if valid_cookie(value) else None


@dataclass(repr=False)
class _Flow:
    id: str
    owner: str
    platform: str
    expected: str | None
    revision: str
    loop: object
    deadline: float
    touched: float
    operation: asyncio.Lock = field(default_factory=asyncio.Lock)
    cancelled: bool = False
    end_reason: str | None = None
    timer: object = None
    driver: object = None
    runtime_dir: str | None = None
    browser: object = None
    context: object = None
    page: object = None
    verification_page: object = None
    creating_page: bool = False
    page_created: asyncio.Event = field(default_factory=asyncio.Event)
    launch_task: object = None
    close_task: object = None
    slot: bool = True
    last_input: float = 0
    last_screenshot: float = 0
    last_confirm: float = 0
    input_events: list = field(default_factory=list)


class BrowserLogin:
    def __init__(self, accounts, jobs, checker=None, playwright_factory=None):
        self.accounts, self.jobs = accounts, jobs
        self.checker = checker or getattr(accounts, 'credential_checks', None)
        self.playwright_factory = playwright_factory or async_playwright
        self.owner_valid = lambda owner: True
        self.lock = threading.RLock()
        self.flow = None
        self.closed = False
        self.cleanup_tasks = set()
        self.cleanup_failed = False
        self.retry_at = 0
        self.last_ended = None

    def _authorize(self, owner):
        if not self.owner_valid(owner):
            self.clear(owner, reason='owner_revoked')
            raise HTTPException(401, '登录会话已失效，请重新登录 ACM Lab')
        if self.closed:
            raise HTTPException(503, '浏览器登录服务已关闭')

    def _idle(self):
        if any(kind == 'crawl' and not task.done() for kind, task in self.jobs.tasks.values()):
            raise HTTPException(409, '正在采集，请完成后重新登录绑定')

    def _invalidate(self, flow, reason='cancelled'):
        with self.lock:
            if not flow.end_reason:
                flow.end_reason = reason if reason in END_MESSAGES else 'cancelled'
                self.last_ended = dict(owner=flow.owner, login_id=flow.id,
                                       platform=flow.platform, reason=flow.end_reason,
                                       deadline=time.monotonic() + MAX_SECONDS)
            flow.cancelled = True
            if flow.timer:
                flow.timer.cancel()
                flow.timer = None
            if self.flow is flow:
                self.flow = None
        try:
            if asyncio.get_running_loop() is flow.loop:
                self._begin_dispose(flow)
            else:
                flow.loop.call_soon_threadsafe(self._begin_dispose, flow)
        except RuntimeError:
            # The application must call aclose before its loop is stopped.
            if not flow.loop.is_closed():
                flow.loop.call_soon_threadsafe(self._begin_dispose, flow)

    def _begin_dispose(self, flow):
        if flow.close_task is None:
            flow.close_task = flow.loop.create_task(self._dispose(flow))
            self.cleanup_tasks.add(flow.close_task)
            flow.close_task.add_done_callback(self.cleanup_tasks.discard)

    async def _dispose(self, flow):
        async with flow.operation:
            stopped = flow.browser is None and flow.driver is None
            try:
                for obj, method in ((flow.context, 'close'), (flow.browser, 'close'),
                                    (flow.driver, 'stop')):
                    if obj is not None:
                        try:
                            await asyncio.wait_for(getattr(obj, method)(), timeout=5)
                            if method in {'close', 'stop'} and obj is not flow.context:
                                stopped = True
                        except (Exception, asyncio.CancelledError):
                            # Continue closing the browser/driver even when an
                            # individual context is already gone or times out.
                            pass
            finally:
                flow.page = flow.verification_page = flow.context = flow.browser = flow.driver = None
                if stopped and flow.runtime_dir:
                    try:
                        shutil.rmtree(flow.runtime_dir)
                        flow.runtime_dir = None
                    except FileNotFoundError:
                        flow.runtime_dir = None
                    except OSError:
                        stopped = False
                if flow.slot and stopped:
                    flow.slot = False
                    _SLOT.release()
                elif flow.slot:
                    # If neither browser.close nor driver.stop succeeded, do
                    # not launch another process beside a possibly live one.
                    # A service restart is required to recover this slot.
                    self.cleanup_failed = True

    def clear(self, owner=None, *, reason='cancelled'):
        with self.lock:
            flow = self.flow
            if flow and (owner is None or flow.owner == owner):
                self._invalidate(flow, reason)

    def close(self):
        with self.lock:
            self.closed = True
            self.clear(reason='service_closed')

    async def aclose(self):
        self.close()
        # Also lets a thread-safe disposal callback enter this event loop.
        await asyncio.sleep(0)
        while self.cleanup_tasks:
            tasks = tuple(self.cleanup_tasks)
            await asyncio.gather(*tasks, return_exceptions=True)
            # Python 3.12+ can finish gather(already_done_tasks) without
            # yielding. Their scheduled discard callbacks may not have run;
            # remove drained tasks explicitly instead of spinning forever.
            self.cleanup_tasks.difference_update(task for task in tasks if task.done())

    def _arm_timer(self, flow):
        if flow.timer:
            flow.timer.cancel()
        delay = max(0, min(flow.deadline, flow.touched + IDLE_SECONDS) - time.monotonic())
        flow.timer = flow.loop.call_later(delay, self._expire, flow)

    def _expire(self, flow):
        with self.lock:
            if self.flow is not flow or flow.cancelled:
                return
            now = time.monotonic()
            if now >= min(flow.deadline, flow.touched + IDLE_SECONDS):
                self._invalidate(flow, 'expired')
            else:
                self._arm_timer(flow)

    def _ensure(self, flow, *, touch=False):
        with self.lock:
            try:
                self._authorize(flow.owner)
                if flow.cancelled or self.flow is not flow:
                    raise self._ended_error(flow.end_reason)
                now = time.monotonic()
                if now >= min(flow.deadline, flow.touched + IDLE_SECONDS):
                    raise HTTPException(410, '浏览器登录已过期，请重新开始')
                self._idle()
                if self.accounts.revision() != flow.revision:
                    raise HTTPException(409, '登录期间账号配置已更改，请重新登录')
            except HTTPException as exc:
                reason = ('owner_revoked' if exc.status_code == 401 else
                          'service_closed' if exc.status_code == 503 else
                          'expired' if exc.status_code == 410 else
                          'configuration_changed' if self.accounts.revision() != flow.revision else
                          'crawl_started' if any(kind == 'crawl' and not task.done()
                                                for kind, task in self.jobs.tasks.values()) else
                          'cancelled')
                self._invalidate(flow, reason)
                raise
            if touch:
                flow.touched = now
                self._arm_timer(flow)

    def _current(self, owner, login_id, *, touch=False):
        with self.lock:
            self._authorize(owner)
            flow = self.flow
            if (not isinstance(login_id, str) or not login_id.isascii() or not flow or flow.owner != owner
                    or not secrets.compare_digest(flow.id, login_id)):
                ended = self._ended(owner, login_id)
                if ended:
                    raise self._ended_error(ended['reason'], 410)
                raise HTTPException(404, '浏览器登录流程不存在')
            self._ensure(flow, touch=touch)
            return flow

    def _ended(self, owner, login_id):
        ended = self.last_ended
        if (ended and ended['deadline'] > time.monotonic()
                and ended['owner'] == owner and isinstance(login_id, str)
                and login_id.isascii() and secrets.compare_digest(ended['login_id'], login_id)):
            return ended
        return None

    @staticmethod
    def _ended_error(reason, status=409):
        reason = reason if reason in END_MESSAGES else 'cancelled'
        return HTTPException(status, END_MESSAGES[reason] + ' [' + reason + ']',
                             headers={'X-ACM-Login-Reason': reason})

    @staticmethod
    def _result(flow, status='human_required', reason='official_page', **extra):
        return dict(login_id=flow.id, platform=flow.platform, status=status, reason=reason,
                    width=WIDTH, height=HEIGHT,
                    expires_in=max(0, math.ceil(min(flow.deadline, flow.touched + IDLE_SECONDS)
                                               - time.monotonic())),
                    official_url=LOGIN_URLS[flow.platform],
                    message={
                        'official_page': '请在下方官方页面中亲自输入账号密码并完成验证，然后点击确认登录。',
                        'identity_verified': '官方当前登录身份已验证，账号已绑定。',
                        'identity_unconfirmed': '尚未确认当前登录身份，请继续在原官方页面完成登录和验证。',
                        'account_mismatch': '当前官方身份与指定账号不一致，原绑定未修改。',
                        'cancelled': '浏览器登录已取消。',
                    }[reason], **extra)

    async def _route(self, flow, route):
        request = route.request
        try:
            top = request.is_navigation_request() and request.frame.parent_frame is None
            controlled = (not top or request.frame.page is flow.page
                          or request.frame.page is flow.verification_page)
            permitted = (not flow.cancelled and controlled and request.method in {'GET', 'HEAD', 'POST'}
                         and allowed_url(flow.platform, request.url, top_level=top))
        except Exception:
            permitted = False
        try:
            if permitted:
                await route.continue_()
            else:
                await route.abort('blockedbyclient')
        except Exception:
            # Closing a context can race with pending intercepted requests.
            # Do not let a transport exception log an upstream URL or token.
            pass

    @staticmethod
    async def _block_socket(route):
        try:
            await route.close(code=1008, reason='disabled')
        except Exception:
            pass

    @staticmethod
    async def _cancel_download(download):
        try:
            await download.cancel()
        except Exception:
            pass

    @staticmethod
    async def _dismiss_dialog(dialog):
        try:
            await dialog.dismiss()
        except Exception:
            pass

    async def _close_uncontrolled_page(self, flow, page):
        if flow.creating_page:
            await flow.page_created.wait()
        if page is not flow.page and page is not flow.verification_page:
            try:
                await page.close()
            except Exception:
                pass

    def _page_safe(self, flow):
        self._ensure(flow)
        if not flow.page or not allowed_url(flow.platform, flow.page.url, top_level=True):
            self._invalidate(flow, 'unsafe_navigation')
            raise HTTPException(502, '官方页面离开了允许的登录站点，登录已停止')

    def _configure_page(self, page):
        page.set_default_timeout(10000)
        page.set_default_navigation_timeout(20000)
        page.on('download', self._cancel_download)
        page.on('dialog', self._dismiss_dialog)

    async def _install_response_guard(self, flow, page):
        """Inspect every redirect *before* Chromium sends its next request.

        Playwright route handlers see only the first URL of a redirect chain.
        A private CDP response interceptor closes that gap without replacing
        Chromium's network stack, cookies, or POST redirect semantics. Nothing
        in this session is exposed as an HTTP/CDP control endpoint.
        """
        session = await flow.context.new_cdp_session(page)
        try:
            frame_tree = await session.send('Page.getFrameTree')
            main_frame = frame_tree['frameTree']['frame']['id']
            session.on('Fetch.requestPaused', lambda event: self._guard_response(
                flow, page, session, main_frame, event))
            session.on('Target.attachedToTarget', lambda event: self._attach_child_guard(
                flow, page, session, event))
            await session.send('Fetch.enable', {'patterns': _RESPONSE_PATTERNS})
            await session.send('Target.setAutoAttach', _AUTO_ATTACH)
        except BaseException:
            try:
                await session.detach()
            except Exception:
                pass
            raise
        return session

    async def _attach_child_guard(self, flow, page, parent, event):
        """Guard iframe targets and track dedicated workers' creator targets."""
        try:
            kind = event.get('targetInfo', {}).get('type')
            if kind == 'shared_worker':
                # Shared workers have no verified creator-page network owner.
                # Do not allow an untested transport to execute in this broker.
                closed = await parent.send('Target.closeTarget', {'targetId': event['targetInfo']['targetId']})
                if not closed.get('success'):
                    raise RuntimeError('guard_capability_unavailable')
                return
            child = _ChildCDPSession(parent, event['sessionId'])
            child.on('Fetch.requestPaused', lambda paused: self._guard_response(
                flow, page, child, None, paused))
            child.on('Target.attachedToTarget', lambda attached: self._attach_child_guard(
                flow, page, child, attached))
            if kind != 'worker':
                await child.send('Fetch.enable', {'patterns': _RESPONSE_PATTERNS})
            # Dedicated-worker requests are intercepted by their creator's
            # already-installed page/iframe Fetch session. Chromium has no
            # worker Fetch domain; Network.setBlockedURLs is not a reliable
            # worker boundary. Real transport probes cover ordinary, nested,
            # and OOPIF-created workers, including hostile redirect responses.
            await child.send('Target.setAutoAttach', _AUTO_ATTACH)
            if flow.cancelled:
                return
            await child.send('Runtime.runIfWaitingForDebugger')
        except Exception as exc:
            if _protocol_reason(exc) == 'target_closed':
                # Short-lived workers/frames may disappear during attachment;
                # a destroyed target cannot send an unguarded request.
                return
            # A missing CDP capability never falls back to unguarded requests.
            if not flow.cancelled:
                self._invalidate(flow, 'guard_capability_unavailable')

    async def _guard_response(self, flow, page, session, main_frame, event):
        request_id = event.get('requestId')
        try:
            top = (event.get('resourceType') == 'Document'
                   and event.get('frameId') == main_frame)
            url = event.get('request', {}).get('url', '')
            allowed = (not flow.cancelled
                       and allowed_url(flow.platform, url, top_level=top))
            status = event.get('responseStatusCode', 0)
            locations = [item.get('value', '') for item in event.get('responseHeaders', [])
                         if item.get('name', '').casefold() == 'location']
            if status in {301, 302, 303, 307, 308} and locations:
                allowed = (allowed and len(locations) == 1
                           and allowed_url(flow.platform, urljoin(url, locations[0]),
                                           top_level=top))
            if not allowed:
                await session.send('Fetch.failRequest', {'requestId': request_id,
                                                        'errorReason': 'Aborted'})
                if top:
                    self._invalidate(flow, 'unsafe_redirect')
                return
            command = 'Fetch.continueResponse' if 'responseStatusCode' in event else 'Fetch.continueRequest'
            await session.send(command, {'requestId': request_id})
        except Exception as exc:
            protocol_reason = _protocol_reason(exc)
            if (protocol_reason == 'target_closed'
                    or protocol_reason == 'request_gone' and event.get('responseErrorReason')):
                return
            # An interception failure must fail closed. Exceptions can contain
            # the official URL, so never send them to clients or event logs.
            try:
                await session.send('Fetch.failRequest', {'requestId': request_id,
                                                        'errorReason': 'Aborted'})
            except Exception:
                pass
            if not flow.cancelled:
                self._invalidate(flow, 'guard_protocol_error')

    async def _launch(self, flow):
        async with flow.operation:
            self._ensure(flow)
            flow.driver = await self.playwright_factory().start()
            self._ensure(flow)
            flow.runtime_dir = tempfile.mkdtemp(prefix='acm-login-')
            flow.browser = await flow.driver.chromium.launch(
                headless=True, channel='chromium', chromium_sandbox=True,
                env=browser_environment(flow.runtime_dir))
            self._ensure(flow)
            flow.context = await flow.browser.new_context(
                viewport={'width': WIDTH, 'height': HEIGHT}, accept_downloads=False,
                service_workers='block', permissions=[], ignore_https_errors=False)
            await flow.context.route('**/*', lambda route: self._route(flow, route))
            if hasattr(flow.context, 'route_web_socket'):
                await flow.context.route_web_socket('**/*', self._block_socket)
            flow.page = await flow.context.new_page()
            self._configure_page(flow.page)
            # No new window becomes a second, uncontrolled interaction target.
            flow.context.on('page', lambda page: self._close_uncontrolled_page(flow, page))
            await self._install_response_guard(flow, flow.page)
            self._ensure(flow)
            # Hand control to the human once the official document commits.
            # Slow scripts may delay DOMContentLoaded while the page is already
            # visible; screenshots can refresh as it continues loading.
            await flow.page.goto(LOGIN_URLS[flow.platform], wait_until='commit')
            self._ensure(flow)
            if not allowed_url(flow.platform, flow.page.url, top_level=True):
                raise HTTPException(502, '官方页面返回了不支持的跳转，登录已停止')

    async def start(self, owner, platform, expected_handle=None):
        with self.lock:
            self._authorize(owner)
            self._idle()
            if platform not in LOGIN_URLS:
                raise HTTPException(422, '此平台不支持浏览器密码登录')
            if (expected_handle is not None and (not isinstance(expected_handle, str)
                    or not expected_handle or len(expected_handle) > 100
                    or any(ord(c) < 33 or ord(c) == 127 for c in expected_handle))):
                raise HTTPException(422, '绑定目标账号格式无效')
            if self.playwright_factory is None:
                raise HTTPException(503, '浏览器登录不可用：服务器尚未安装 Playwright 和 Chromium')
            if self.cleanup_failed:
                raise HTTPException(503, '浏览器登录清理未完成，请联系管理员重启服务')
            if self.flow:
                raise HTTPException(409, '已有浏览器登录流程，请先完成或取消')
            now = time.monotonic()
            if now < self.retry_at:
                raise HTTPException(429, '请稍后重新开始浏览器登录', headers={'Retry-After': '10'})
            if not _SLOT.acquire(blocking=False):
                raise HTTPException(429, '浏览器登录服务繁忙，请稍后重试', headers={'Retry-After': '10'})
            self.retry_at = now + 10
            try:
                flow = _Flow(secrets.token_urlsafe(32), owner, platform, expected_handle,
                             self.accounts.revision(), asyncio.get_running_loop(),
                             now + MAX_SECONDS, now)
            except BaseException:
                _SLOT.release()
                raise
            self.flow = flow
            self._arm_timer(flow)
        flow.launch_task = asyncio.create_task(asyncio.wait_for(self._launch(flow), timeout=35))
        # If its requesting HTTP coroutine is cancelled, the launch retains its
        # references and disposal waits until it can close the resulting driver.
        flow.launch_task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        try:
            await asyncio.shield(flow.launch_task)
            self._ensure(flow, touch=True)
            return self._result(flow)
        except BaseException as exc:
            self._invalidate(flow, 'startup_failed')
            if isinstance(exc, (HTTPException, asyncio.CancelledError)):
                raise
            raise HTTPException(503, '浏览器登录不可用，服务器无法启动或连接官方浏览器页面') from None

    async def status(self, owner, login_id):
        # Status polling deliberately does not renew the human inactivity TTL.
        self._authorize(owner)
        ended = self._ended(owner, login_id)
        if ended and ended['reason'] != 'identity_verified':
            reason = ended['reason']
            return dict(login_id=ended['login_id'], platform=ended['platform'],
                        status=('expired' if reason == 'expired' else 'cancelled' if reason == 'cancelled'
                                else 'unavailable' if reason.startswith('guard_') else 'failed'),
                        reason=reason, message=END_MESSAGES[reason], expires_in=0,
                        width=WIDTH, height=HEIGHT, official_url=LOGIN_URLS[ended['platform']])
        flow = self._current(owner, login_id)
        return self._result(flow)

    async def screenshot(self, owner, login_id):
        flow = self._current(owner, login_id)
        if flow.operation.locked():
            raise HTTPException(409, '官方页面正在处理操作，请稍后重试')
        async with flow.operation:
            self._page_safe(flow)
            now = time.monotonic()
            delay = 0.4 - (now - flow.last_screenshot)
            if delay > 0:
                # Fast Tab/text/Enter actions should still show their latest
                # page, while preserving the screenshot resource limit.
                await asyncio.sleep(delay)
                self._page_safe(flow)
            flow.last_screenshot = time.monotonic()
            try:
                data = await flow.page.screenshot(type='png', full_page=False, timeout=10000)
            except Exception:
                self._ensure(flow)
                raise HTTPException(502, '暂时无法显示官方页面，请稍后重试') from None
            self._page_safe(flow)
            if not isinstance(data, bytes) or len(data) > MAX_SCREENSHOT:
                raise HTTPException(502, '官方页面截图超过大小上限')
            return data

    async def interact(self, owner, login_id, kind, x=None, y=None, key=None, text=None):
        flow = self._current(owner, login_id)
        if kind == 'type':
            kind = 'text'
        if kind == 'click':
            if (key is not None or text is not None or isinstance(x, bool) or isinstance(y, bool)
                    or not isinstance(x, (int, float)) or not isinstance(y, (int, float))
                    or not math.isfinite(x) or not math.isfinite(y)
                    or not 0 <= x < WIDTH or not 0 <= y < HEIGHT):
                raise HTTPException(422, '点击位置无效')
        elif kind == 'key':
            if not isinstance(key, str) or key not in KEYS or x is not None or y is not None or text is not None:
                raise HTTPException(422, '不支持此按键')
        elif kind == 'text':
            if (not isinstance(text, str) or not 1 <= len(text) <= MAX_TEXT
                    or any(ord(c) < 32 or ord(c) == 127 for c in text)
                    or x is not None or y is not None or key is not None):
                raise HTTPException(422, '输入文字格式无效或超过长度上限')
        else:
            raise HTTPException(422, '不支持此浏览器操作')
        if flow.operation.locked():
            raise HTTPException(409, '官方页面正在处理操作，请稍后重试')
        async with flow.operation:
            self._page_safe(flow)
            now = time.monotonic()
            flow.input_events = [stamp for stamp in flow.input_events if stamp > now - 60]
            if now - flow.last_input < 0.05 or len(flow.input_events) >= 120:
                raise HTTPException(429, '官方页面操作过于频繁，请稍后重试', headers={'Retry-After': '1'})
            flow.last_input = now
            flow.input_events.append(now)
            try:
                if kind == 'click':
                    await flow.page.mouse.click(x, y)
                elif kind == 'key':
                    await flow.page.keyboard.press(key)
                else:
                    await flow.page.keyboard.insert_text(text)
            except Exception:
                self._ensure(flow)
                raise HTTPException(502, '本次官方页面操作未完成，请查看页面后重试') from None
            finally:
                text = None
            self._page_safe(flow)
            self._ensure(flow, touch=True)
            return self._result(flow)

    async def confirm(self, owner, login_id):
        flow = self._current(owner, login_id)
        if flow.operation.locked():
            raise HTTPException(409, '官方页面正在处理操作，请稍后重试')
        completed = False
        async with flow.operation:
            self._page_safe(flow)
            now = time.monotonic()
            if now - flow.last_confirm < 3:
                raise HTTPException(429, '请等待 3 秒后确认登录', headers={'Retry-After': '3'})
            flow.last_confirm = now
            verification = None
            verification_guard = None
            try:
                # The fixed private identity page is loaded in the context in
                # which the human just completed the official login/challenge.
                # A separate page preserves the form/challenge the human is
                # currently using; merely checking login cannot erase it.
                if flow.page.url == URLS[flow.platform]:
                    verification = flow.page
                else:
                    flow.creating_page = True
                    flow.page_created.clear()
                    try:
                        verification = await flow.context.new_page()
                        flow.verification_page = verification
                    finally:
                        flow.creating_page = False
                        flow.page_created.set()
                    self._configure_page(verification)
                    verification_guard = await self._install_response_guard(flow, verification)
                    await verification.goto(URLS[flow.platform], wait_until='domcontentloaded')
                self._ensure(flow)
                if not allowed_url(flow.platform, verification.url, top_level=True):
                    raise HTTPException(502, '官方页面返回了不支持的跳转')
                html = await verification.content()
                self._ensure(flow)
                if len(html.encode('utf-8')) > MAX_HTML:
                    raise HTTPException(502, '官方身份页面超过大小上限')
                status, handle = inspect_identity(flow.platform, html)
                html = None
                if status != 'authenticated' or not handle:
                    return self._result(flow, reason='identity_unconfirmed')
                if flow.expected and (flow.expected.casefold() != handle.casefold()
                                      if flow.platform == 'codeforces' else flow.expected != handle):
                    return self._result(flow, status='failed', reason='account_mismatch')
                cookies = await flow.context.cookies([URLS[flow.platform]])
                self._ensure(flow)
                cookie = _cookie_header(flow.platform, cookies)
                cookies = None
                if not cookie:
                    return self._result(flow, reason='identity_unconfirmed')
                # No await between the last authorization/revision check and
                # the atomic account update. Raw cookies never leave this API.
                with self.lock:
                    self._ensure(flow)
                    saved = self.accounts.bind_verified_account(flow.platform, handle, cookie, flow.revision)
                    if self.checker:
                        try:
                            self.checker.remember_verified(flow.platform, handle, cookie)
                        except Exception:
                            # Cache failure cannot undo an already committed,
                            # verified binding or reveal a secret exception.
                            pass
                    account = next(item for item in saved['items']
                                   if item['platform'] == flow.platform and item['handle'] == handle)
                    result = self._result(flow, 'authenticated', 'identity_verified', account=account)
                    completed = True
                    self._invalidate(flow, 'identity_verified')
                return result
            except HTTPException:
                raise
            except AIError:
                self._invalidate(flow, 'configuration_changed')
                raise HTTPException(409, '登录期间账号配置已更改，请重新登录') from None
            except Exception:
                self._ensure(flow)
                raise HTTPException(502, '暂时无法确认官方当前登录身份，请稍后重试') from None
            finally:
                if verification is not None and verification is not flow.page:
                    try:
                        await asyncio.wait_for(verification.close(), timeout=5)
                    except (Exception, asyncio.CancelledError):
                        pass
                    flow.verification_page = None
                    if verification_guard:
                        try:
                            await verification_guard.detach()
                        except Exception:
                            pass
                if completed:
                    cookie = None

    async def poll(self, owner, login_id):
        return await self.confirm(owner, login_id)

    async def cancel(self, owner, login_id):
        flow = self._current(owner, login_id)
        result = self._result(flow, 'cancelled', 'cancelled')
        self._invalidate(flow)
        if flow.close_task:
            await asyncio.shield(flow.close_task)
        return result

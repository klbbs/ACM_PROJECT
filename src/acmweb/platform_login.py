"""Ephemeral official password forms; persist only a verified session Cookie.

No browser is needed on the server. Challenges deliberately stop this flow:
solving one in another browser cannot complete this requests.Session.
"""
import asyncio
import threading
import time
from typing import Literal
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .codex_bridge import AIError
from .cookie_validation import valid_cookie
from .credential_check import URLS, challenge_reason, inspect_identity
from ojcrawler.http import browser_headers

LOGIN_URLS = {'codeforces': 'https://codeforces.com/enter',
              'atcoder': 'https://atcoder.jp/login'}
NETWORK = threading.BoundedSemaphore(2)
MAX_BODY = 1048576
MAX_SECONDS = 45


class PasswordInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    platform: Literal['codeforces', 'atcoder']
    username: str = Field(min_length=1, max_length=150, pattern=r'^[^\s\x00-\x1f\x7f]+$')
    password: SecretStr = Field(min_length=1, max_length=256, repr=False)
    expected_handle: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_.-]{1,100}$')


MESSAGES = {
    'identity_verified': '官方当前登录身份已验证，账号已绑定。',
    'credentials_not_accepted': '平台未接受本次登录，请检查用户名和密码，或在官方页面登录。',
    'account_mismatch': '本次登录身份与指定账号不一致，原绑定未修改。',
    'captcha': '平台要求验证码或人机验证，本次服务器登录已停止；请在官方页面登录后使用 Cookie 绑定。其他浏览器的验证不会完成本次服务器会话。',
    'browser_challenge': '平台要求浏览器验证，本次服务器登录已停止；请在官方页面登录后使用 Cookie 绑定。其他浏览器的验证不会完成本次服务器会话。',
    'access_restricted': '平台限制了服务器访问，本次未绑定账号；可在官方页面登录后尝试 Cookie 绑定。',
    'rate_limited': '平台限制了请求频率，请稍后重试。',
    'unsafe_redirect': '平台返回了不支持的跳转，本次登录已停止。',
    'unsupported_form': '未找到受支持的官方登录表单，本次未发送密码。',
    'response_too_large': '平台响应超过大小上限，本次登录已停止。',
    'identity_unconfirmed': '未取得可靠的官方当前登录身份，本次未绑定账号。',
    'timeout': '服务器连接平台超时，请稍后重试。',
    'network_error': '服务器暂时无法完成平台登录，请稍后重试。',
    'configuration_changed': '登录期间账号配置已更改，请重新登录，原绑定未修改。',
}


def _result(platform, status, reason):
    return dict(status=status, reason=reason, message=MESSAGES[reason],
                official_url=LOGIN_URLS[platform])


class LoginFailure(Exception):
    def __init__(self, status, reason):
        # Only fixed local reasons are ever rendered or logged by callers.
        super().__init__(reason)
        self.status, self.reason = status, reason


def _redirect(platform, location):
    origin = urlsplit(LOGIN_URLS[platform])
    parsed = urlsplit(urljoin(LOGIN_URLS[platform], location))
    paths = {'/', origin.path, urlsplit(URLS[platform]).path}
    callback_ok = not parsed.query
    if parsed.query and parsed.path == origin.path:
        key = 'continue' if platform == 'atcoder' else 'back'
        # Official anonymous settings redirects carry exactly this return
        # target. It is inspected only; never navigate to a returned callback.
        callback = parse_qs(parsed.query, keep_blank_values=True)
        callback_ok = callback in ({key: [URLS[platform]]},
                                   {key: [urlsplit(URLS[platform]).path]})
    if (not location or parsed.scheme != 'https' or parsed.netloc != origin.netloc
            or parsed.path not in paths or not callback_ok or parsed.fragment):
        raise LoginFailure('unknown', 'unsafe_redirect')
    return parsed.path


def _request(session, platform, method, url, deadline, **kwargs):
    """Exactly three fixed requests; redirects are inspected, never followed."""
    if url not in {LOGIN_URLS[platform], URLS[platform]}:
        raise LoginFailure('unknown', 'unsafe_redirect')
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LoginFailure('unknown', 'timeout')
    headers = {**browser_headers(), **kwargs.pop('headers', {})}
    with session.request(method, url, headers=headers, timeout=(min(5, remaining), min(12, remaining)),
                         allow_redirects=False, stream=True, **kwargs) as response:
        body = bytearray()
        for part in response.iter_content(16384):
            if time.monotonic() >= deadline:
                raise LoginFailure('unknown', 'timeout')
            if len(body) + len(part) > MAX_BODY:
                raise LoginFailure('unknown', 'response_too_large')
            body.extend(part)
        html = body.decode('utf-8', errors='replace')
        soup = BeautifulSoup(html, 'html.parser')
        challenge = challenge_reason(soup)
        if challenge:
            raise LoginFailure('blocked', challenge)
        if response.status_code == 429:
            raise LoginFailure('blocked', 'rate_limited')
        if response.status_code in {403, 418}:
            raise LoginFailure('blocked', 'access_restricted')
        redirect = None
        if 300 <= response.status_code < 400:
            redirect = _redirect(platform, response.headers.get('Location', ''))
        elif response.status_code != 200:
            raise LoginFailure('unknown', 'network_error')
        return html, soup, redirect


def _form(platform, soup):
    user_field = 'handleOrEmail' if platform == 'codeforces' else 'username'
    forms = [f for f in soup.select('form') if f.select_one('input[name="password"]')
             and f.find('input', attrs={'name': user_field})]
    if len(forms) != 1:
        raise LoginFailure('unknown', 'unsupported_form')
    form = forms[0]
    # The official AtCoder form can render a Turnstile widget from JavaScript
    # without a .cf-turnstile class or response input in its initial HTML.
    # A passive script alone is not enough evidence: require its form widget.
    turnstile_script = False
    for script in soup.select('script[src]'):
        src = urlsplit(urljoin(LOGIN_URLS[platform], script.get('src', '')))
        if (src.scheme, src.netloc, src.path) == ('https', 'challenges.cloudflare.com', '/turnstile/v0/api.js'):
            turnstile_script = True
            break
    if turnstile_script and form.select_one('[data-sitekey][data-theme]'):
        raise LoginFailure('blocked', 'browser_challenge')
    # Form action, hidden callback and redirect fields are untrusted input.
    if (form.get('method', '').casefold() != 'post'
            or urljoin(LOGIN_URLS[platform], form.get('action', '')) != LOGIN_URLS[platform]):
        raise LoginFailure('unknown', 'unsupported_form')
    allowed = {'csrf_token'} | ({'action', 'ftaa', 'bfaa', '_tta'} if platform == 'codeforces' else set())
    fields = {}
    for field in form.select('input[type="hidden"][name]'):
        name, value = field['name'], field.get('value', '')
        if name not in allowed or name in fields or len(value) > 4096:
            raise LoginFailure('unknown', 'unsupported_form')
        fields[name] = value
    if not fields.get('csrf_token') or any(len(v) > 4096 for v in fields.values()):
        raise LoginFailure('unknown', 'unsupported_form')
    if platform == 'codeforces':
        if fields.get('action', 'enter') != 'enter':
            raise LoginFailure('unknown', 'unsupported_form')
        fields['action'] = 'enter'
    return fields, user_field


def _login(session, platform, username, password):
    deadline = time.monotonic() + MAX_SECONDS
    _, soup, redirect = _request(session, platform, 'GET', LOGIN_URLS[platform], deadline)
    if redirect:
        raise LoginFailure('unknown', 'unsupported_form')
    fields, user_field = _form(platform, soup)
    fields.update({user_field: username, 'password': password})
    try:
        _, _, redirect = _request(session, platform, 'POST', LOGIN_URLS[platform], deadline,
                                 data=fields, headers={'Origin': 'https://' + urlsplit(LOGIN_URLS[platform]).netloc,
                                                      'Referer': LOGIN_URLS[platform]})
    finally:
        fields.clear()
    # A successful POST/redirect is not authentication evidence. Only the
    # independent fixed settings page may identify this requests session.
    html, _, redirect = _request(session, platform, 'GET', URLS[platform], deadline)
    if redirect:
        if redirect == urlsplit(LOGIN_URLS[platform]).path:
            raise LoginFailure('rejected', 'credentials_not_accepted')
        raise LoginFailure('unknown', 'identity_unconfirmed')
    status, handle = inspect_identity(platform, html)
    if status == 'expired':
        raise LoginFailure('rejected', 'credentials_not_accepted')
    if status != 'authenticated' or not handle:
        raise LoginFailure('unknown', 'identity_unconfirmed')
    cookie = session.prepare_request(requests.Request('GET', URLS[platform])).headers.get('Cookie', '')
    if not valid_cookie(cookie):
        raise LoginFailure('unknown', 'identity_unconfirmed')
    return handle, cookie


class PasswordLogin:
    def __init__(self, accounts, jobs, session_factory=requests.Session):
        self.accounts, self.jobs = accounts, jobs
        self.session_factory = session_factory
        self.owner_valid = lambda owner: True
        self.lock = threading.RLock()
        self.active = None
        self.closed = False
        self.retry_at = 0

    def clear(self, owner=None):
        with self.lock:
            if self.active and (owner is None or self.active['owner'] == owner):
                self.active['cancelled'] = True

    def close(self):
        with self.lock:
            self.closed = True
            self.clear()

    def _authorize(self, owner):
        if not self.owner_valid(owner):
            raise HTTPException(401, '登录会话已失效，请重新登录 ACM Lab')
        if self.closed:
            raise HTTPException(503, '平台登录服务已关闭')

    def _idle(self):
        if any(kind == 'crawl' and not task.done() for kind, task in self.jobs.tasks.values()):
            raise HTTPException(409, '正在采集，请完成后重新登录绑定')

    async def login(self, owner, payload):
        with self.lock:
            self._authorize(owner)
            self._idle()
            if self.active:
                raise HTTPException(429, '已有平台登录正在处理，请稍后重试')
            now = time.monotonic()
            if now < self.retry_at:
                delay = max(1, int(self.retry_at - now + 0.999))
                raise HTTPException(429, '平台登录尝试过于频繁，请稍后重试',
                                    headers={'Retry-After': str(delay)})
            self.retry_at = now + 10
            flow = dict(owner=owner, cancelled=False, revision=self.accounts.revision())
            self.active = flow

        def run():
            if not NETWORK.acquire(blocking=False):
                raise HTTPException(429, '平台登录服务繁忙，请稍后重试')
            try:
                with self.session_factory() as session:
                    return _login(session, payload.platform, payload.username, payload.password.get_secret_value())
            finally:
                NETWORK.release()

        task = asyncio.create_task(asyncio.to_thread(run))
        try:
            try:
                handle, cookie = await asyncio.shield(task)
            except LoginFailure as exc:
                if exc.reason == 'rate_limited':
                    with self.lock:
                        self.retry_at = max(self.retry_at, time.monotonic() + 60)
                return _result(payload.platform, exc.status, exc.reason)
            except requests.Timeout:
                return _result(payload.platform, 'unknown', 'timeout')
            except HTTPException:
                raise
            except Exception:
                return _result(payload.platform, 'unknown', 'network_error')
            with self.lock:
                self._authorize(owner)
                if flow['cancelled']:
                    raise HTTPException(409, '本次平台登录已取消，请重新登录')
                self._idle()
                expected = payload.expected_handle
                matches = (expected.casefold() == handle.casefold() if payload.platform == 'codeforces'
                           else expected == handle) if expected else True
                if not matches:
                    return _result(payload.platform, 'rejected', 'account_mismatch')
                try:
                    saved = self.accounts.bind_verified_account(payload.platform, handle, cookie, flow['revision'])
                except AIError:
                    return _result(payload.platform, 'rejected', 'configuration_changed')
                result = _result(payload.platform, 'authenticated', 'identity_verified')
                checker = getattr(self.accounts, 'credential_checks', None)
                if checker:
                    checker.remember_verified(payload.platform, handle, cookie)
                result['account'] = next(item for item in saved['items']
                                         if item['platform'] == payload.platform and item['handle'] == handle)
                return result
        except asyncio.CancelledError:
            with self.lock:
                flow['cancelled'] = True
            # A running Session is closed by its worker, never by cancellation.
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            raise
        finally:
            with self.lock:
                if self.active is flow:
                    self.active = None


def register(app, accounts):
    manager = PasswordLogin(accounts, app.state.jobs)
    app.state.platform_login = manager

    @app.post('/api/platform-login/password')
    async def password_login(payload: PasswordInput, request: Request):
        owner = request.scope.get('state', {}).get('platform_login_owner')
        if getattr(app.state, 'multi_child', False) and not owner:
            raise HTTPException(401, '请重新登录 ACM Lab')
        return await manager.login(owner or 'local-workspace', payload)

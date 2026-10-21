"""Synthetic browser transport tests; none of these establish live OJ login."""
import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from acmweb.accounts import AccountInput, AccountList, Accounts
from acmweb.browser_login import (HEIGHT, IDLE_SECONDS, LOGIN_URLS, MAX_SECONDS,
                                  WIDTH, BrowserLogin, _ChildCDPSession,
                                  _cookie_header, allowed_url)
from acmweb.credential_check import CredentialChecks, URLS
from acmweb.settings import Settings

SECRET = 'SYNTHETIC-cookie-never-return'
PASSWORD = 'SYNTHETIC-password-never-return'


def identity_page(platform, handle='tourist'):
    if platform == 'luogu':
        return '<script id="lentille-context">{"currentUser":{"uid":123}}</script>'
    nav = 'class="lang-chooser"' if platform == 'codeforces' else 'id="navbar-collapse"'
    path = '/profile/' if platform == 'codeforces' else '/users/'
    return (f'<div {nav}><a href="{path}{handle}">current</a></div>'
            '<a href="/logout">logout</a><form action="/settings"><input name="email"></form>')


class Keyboard:
    def __init__(self): self.events = []
    async def insert_text(self, value): self.events.append(('text', value))
    async def press(self, value): self.events.append(('key', value))


class Mouse:
    def __init__(self): self.events = []
    async def click(self, x, y): self.events.append((x, y))


class Page:
    def __init__(self, platform):
        self.platform = platform
        self.url = 'about:blank'
        self.html = identity_page(platform)
        self.mouse, self.keyboard = Mouse(), Keyboard()
        self.visits, self.events = [], {}
        self.closed = False
        self.hook = None
    def set_default_timeout(self, value): assert value <= 10000
    def set_default_navigation_timeout(self, value): assert value <= 20000
    def on(self, name, callback): self.events[name] = callback
    async def goto(self, url, **kwargs):
        assert kwargs == {'wait_until': 'domcontentloaded'}
        assert url in {LOGIN_URLS[self.platform], URLS[self.platform]}
        self.url = url
        self.visits.append(url)
        if self.hook: self.hook()
    async def content(self): return self.html
    async def screenshot(self, **kwargs):
        assert kwargs['full_page'] is False and kwargs['type'] == 'png'
        if self.hook: self.hook()
        return b'\x89PNG\r\n\x1a\nSYNTHETIC'
    async def close(self): self.closed = True


class Context:
    def __init__(self, platform):
        self.page = Page(platform)
        self.pages = []
        self.platform, self.closed, self.routes, self.events = platform, False, {}, {}
        root = {'atcoder': 'atcoder.jp', 'codeforces': 'codeforces.com', 'luogu': '.luogu.com.cn'}[platform]
        self.values = [{'name': 'session', 'value': SECRET, 'domain': root, 'path': '/',
                        'httpOnly': True, 'secure': True}]
    async def route(self, pattern, handler): self.routes[pattern] = handler
    async def route_web_socket(self, pattern, handler): self.socket_handler = handler
    async def new_cdp_session(self, page): return CDPSession()
    async def new_page(self):
        if not self.pages:
            page = self.page
        else:
            page = Page(self.platform)
            page.html, page.hook = self.page.html, self.page.hook
        self.pages.append(page)
        return page
    def on(self, name, callback): self.events[name] = callback
    async def cookies(self, urls):
        assert urls == [URLS[self.platform]]
        return self.values
    async def close(self): self.closed = True


class CDPSession:
    def __init__(self): self.commands, self.events, self.detached = [], {}, False
    async def send(self, command, params=None):
        self.commands.append((command, params))
        if command == 'Page.getFrameTree': return {'frameTree': {'frame': {'id': 'main-frame'}}}
    def on(self, event, handler): self.events[event] = handler
    async def detach(self): self.detached = True


class Browser:
    def __init__(self, platform): self.context, self.closed = Context(platform), False
    async def new_context(self, **kwargs):
        assert kwargs == {'viewport': {'width': WIDTH, 'height': HEIGHT},
                          'accept_downloads': False, 'service_workers': 'block',
                          'permissions': [], 'ignore_https_errors': False}
        return self.context
    async def close(self): self.closed = True


class Factory:
    def __init__(self, platform):
        self.browser, self.stopped, self.starts = Browser(platform), False, 0
        self.chromium = self
        self.failure = None
    def __call__(self): return self
    async def start(self):
        self.starts += 1
        if self.failure: raise self.failure
        return self
    async def launch(self, **kwargs):
        env = kwargs.pop('env')
        self.runtime_dir = Path(env['XDG_CONFIG_HOME']).parent
        assert env['CHROME_CONFIG_HOME'] == env['XDG_CONFIG_HOME']
        assert env['XDG_CACHE_HOME'] == str(self.runtime_dir / 'cache')
        assert self.runtime_dir.stat().st_mode & 0o777 == 0o700
        assert Path(env['XDG_CONFIG_HOME']).is_dir()
        assert kwargs == {'headless': True, 'channel': 'chromium', 'chromium_sandbox': True}
        return self.browser
    async def stop(self): self.stopped = True


def setup_manager(tmp_path, platform='atcoder'):
    accounts = Accounts(Settings(data_dir=tmp_path, config_path=tmp_path/'absent.toml'))
    checker = CredentialChecks(accounts)
    factory = Factory(platform)
    manager = BrowserLogin(accounts, SimpleNamespace(tasks={}), checker, factory)
    return manager, factory


@pytest.mark.parametrize('platform', ['codeforces', 'atcoder', 'luogu'])
def test_human_page_then_same_context_verified_binding_without_secret_response(tmp_path, platform):
    async def run():
        manager, factory = setup_manager(tmp_path, platform)
        try:
            started = await manager.start('owner', platform)
            assert started['status'] == 'human_required'
            assert (started['width'], started['height']) == (1000, 720)
            assert 0 < started['expires_in'] <= IDLE_SECONDS
            assert factory.browser.context.page.visits == [LOGIN_URLS[platform]]
            assert not manager.accounts.path.exists()
            assert (await manager.screenshot('owner', started['login_id'])).startswith(b'\x89PNG')
            await manager.interact('owner', started['login_id'], 'text', text=PASSWORD)
            assert factory.browser.context.page.keyboard.events == [('text', PASSWORD)]
            result = await manager.confirm('owner', started['login_id'])
            expected = '123' if platform == 'luogu' else 'tourist'
            assert result['status'] == 'authenticated' and result['account']['handle'] == expected
            assert factory.browser.context.page.visits == [LOGIN_URLS[platform]]
            verification = factory.browser.context.pages[-1]
            assert verification.visits == [URLS[platform]] and verification.closed
            assert manager.accounts.document()['accounts'][0]['cookie'] == 'session=' + SECRET
            assert manager.checker.statuses()['items'][0]['login_verified'] is True
            assert SECRET not in json.dumps(result) and PASSWORD not in json.dumps(result)
            assert PASSWORD not in manager.accounts.path.read_text()
            assert manager.flow is None
        finally:
            await manager.aclose()
        assert factory.stopped and factory.browser.closed and factory.browser.context.closed
        assert not factory.runtime_dir.exists()
    asyncio.run(run())


def test_challenge_and_unverified_identity_keep_exact_browser_available(tmp_path):
    async def run():
        manager, factory = setup_manager(tmp_path)
        try:
            started = await manager.start('owner', 'atcoder')
            flow = manager.flow
            factory.browser.context.page.html = '<title>Just a moment</title>'
            result = await manager.confirm('owner', started['login_id'])
            assert result['status'] == 'human_required' and result['reason'] == 'identity_unconfirmed'
            assert manager.flow is flow and not factory.browser.closed
            assert factory.browser.context.page.url == LOGIN_URLS['atcoder']
            assert factory.browser.context.pages[-1].closed
            assert not manager.accounts.path.exists()
            assert await manager.screenshot('owner', flow.id)
            await manager.interact('owner', flow.id, 'click', x=100, y=200)
            assert factory.browser.context.page.mouse.events == [(100, 200)]
        finally:
            await manager.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('url,top,allowed', [
    ('https://atcoder.jp/login', True, True),
    ('https://img.atcoder.jp/style.css', False, True),
    ('https://img.atcoder.jp/login', True, False),
    ('https://challenges.cloudflare.com/turnstile/v0/api.js', False, True),
    ('https://challenges.cloudflare.com/turnstile/frame', True, False),
    ('https://evil.atcoder.jp.evil.invalid/', False, False),
    ('https://evil.invalid/', True, False),
    ('http://atcoder.jp/', False, False),
    ('https://atcoder.jp:443/', False, False),
    ('https://username:password@atcoder.jp/', False, False),
    ('https://atcoder.jp./', False, False),
    ('file:///etc/passwd', True, False),
    ('javascript:alert(1)', True, False),
    ('data:text/html,test', True, False),
])
def test_origin_allowlist(url, top, allowed):
    assert allowed_url('atcoder', url, top_level=top) is allowed


def test_route_accepts_embedded_turnstile_but_blocks_top_level_external_and_unsafe_methods(tmp_path):
    async def run():
        manager, _ = setup_manager(tmp_path)
        class Route:
            def __init__(self, url, top=False, method='GET'):
                self.request = SimpleNamespace(url=url, method=method,
                    frame=SimpleNamespace(parent_frame=None if top else object(), page=manager.flow.page),
                    is_navigation_request=lambda: True)
                self.result = None
            async def continue_(self): self.result = 'allowed'
            async def abort(self, reason): self.result = 'blocked'
        try:
            await manager.start('owner', 'atcoder')
            for url, top, method, expected in [
                ('https://challenges.cloudflare.com/frame', False, 'GET', 'allowed'),
                ('https://challenges.cloudflare.com/frame', True, 'GET', 'blocked'),
                ('https://atcoder.jp/login', True, 'POST', 'allowed'),
                ('https://atcoder.jp/login', True, 'DELETE', 'blocked'),
                ('https://evil.invalid/', False, 'GET', 'blocked'),
            ]:
                route = Route(url, top, method)
                await manager._route(manager.flow, route)
                assert route.result == expected
        finally: await manager.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('operation', ['status', 'screenshot', 'confirm', 'cancel', 'interact'])
def test_wrong_owner_cannot_read_or_control_flow(tmp_path, operation):
    async def run():
        manager, factory = setup_manager(tmp_path)
        try:
            started = await manager.start('owner', 'atcoder')
            args = ('other-owner', started['login_id'])
            if operation == 'interact': args += ('key',)
            with pytest.raises(HTTPException) as error:
                await getattr(manager, operation)(*args)
            assert error.value.status_code == 404
            assert manager.flow is not None and not factory.browser.closed
        finally: await manager.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('change', ['revision', 'revoked', 'crawl', 'expired', 'idle'])
@pytest.mark.parametrize('operation', ['status', 'screenshot', 'confirm', 'cancel', 'interact'])
def test_every_public_flow_operation_rechecks_authorization_and_state(tmp_path, change, operation):
    async def run():
        manager, factory = setup_manager(tmp_path)
        try:
            started = await manager.start('owner', 'atcoder')
            if change == 'revision':
                manager.accounts.save(AccountList(items=[AccountInput(platform='luogu', handle='42')]))
            elif change == 'revoked': manager.owner_valid = lambda _: False
            elif change == 'crawl': manager.jobs.tasks['crawl'] = ('crawl', SimpleNamespace(done=lambda: False))
            elif change == 'expired': manager.flow.deadline = time.monotonic() - 1
            elif change == 'idle': manager.flow.touched = time.monotonic() - IDLE_SECONDS - 1
            args = ('owner', started['login_id'])
            if operation == 'interact': args += ('key',)
            with pytest.raises(HTTPException) as error:
                await getattr(manager, operation)(*args)
            assert error.value.status_code in {401, 409, 410}
            assert manager.flow is None
            assert all(not a.get('cookie') for a in manager.accounts.document().get('accounts', []))
        finally: await manager.aclose()
        assert factory.stopped and factory.browser.closed
    asyncio.run(run())


@pytest.mark.parametrize('change', ['revoked', 'revision', 'crawl', 'clear'])
def test_inflight_identity_check_cannot_commit_after_revocation(tmp_path, change):
    async def run():
        manager, factory = setup_manager(tmp_path)
        try:
            started = await manager.start('owner', 'atcoder')
            def hook():
                if change == 'revoked': manager.owner_valid = lambda _: False
                elif change == 'revision': manager.flow.revision = 'stale'
                elif change == 'crawl': manager.jobs.tasks['crawl'] = ('crawl', SimpleNamespace(done=lambda: False))
                else: manager.clear('owner')
            factory.browser.context.page.hook = hook
            with pytest.raises(HTTPException): await manager.confirm('owner', started['login_id'])
            assert not manager.accounts.path.exists()
        finally: await manager.aclose()
    asyncio.run(run())


def test_global_slot_spans_managers_and_is_released_after_actual_close(tmp_path):
    async def run():
        first, factory1 = setup_manager(tmp_path/'one')
        second, factory2 = setup_manager(tmp_path/'two')
        try:
            started = await first.start('owner1', 'atcoder')
            with pytest.raises(HTTPException) as error: await second.start('owner2', 'atcoder')
            assert error.value.status_code == 429 and factory2.starts == 0
            first.clear('different-owner')
            assert first.flow is not None
            await first.cancel('owner1', started['login_id'])
            assert factory1.stopped and factory1.browser.closed
            assert (await second.start('owner2', 'atcoder'))['status'] == 'human_required'
        finally:
            await first.aclose()
            await second.aclose()
    asyncio.run(run())


def test_status_and_screenshot_polling_do_not_keep_abandoned_flow_alive(tmp_path):
    async def run():
        manager, _ = setup_manager(tmp_path)
        try:
            started = await manager.start('owner', 'atcoder')
            flow = manager.flow
            flow.touched -= 10
            touched = flow.touched
            await manager.status('owner', flow.id)
            await manager.screenshot('owner', flow.id)
            assert flow.touched == touched and flow.deadline - touched <= MAX_SECONDS + 10
            # monotonic's epoch is arbitrary; a freshly booted CI runner can
            # legitimately have an uptime below the inactivity limit.
            flow.touched = time.monotonic() - IDLE_SECONDS - 1
            manager._expire(flow)
            assert manager.flow is None
            expired = await manager.status('owner', started['login_id'])
            assert expired['status'] == 'expired' and expired['reason'] == 'expired'
        finally: await manager.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('kwargs', [
    {'kind': 'url', 'text': 'https://evil.invalid'},
    {'kind': 'javascript', 'text': 'alert(1)'},
    {'kind': 'click', 'x': -1, 'y': 0},
    {'kind': 'click', 'x': WIDTH, 'y': 0},
    {'kind': 'click', 'x': True, 'y': 1},
    {'kind': 'click', 'x': float('nan'), 'y': 1},
    {'kind': 'key', 'key': 'Control+L'},
    {'kind': 'text', 'text': 'secret\nsecond-line'},
    {'kind': 'text', 'text': 'x' * 513},
    {'kind': 'text', 'text': PASSWORD, 'x': 0},
])
def test_input_surface_is_bounded_and_cannot_navigate_or_execute_code(tmp_path, kwargs):
    async def run():
        manager, factory = setup_manager(tmp_path)
        try:
            started = await manager.start('owner', 'atcoder')
            with pytest.raises(HTTPException) as error:
                await manager.interact('owner', started['login_id'], **kwargs)
            assert error.value.status_code == 422
            assert factory.browser.context.page.keyboard.events == []
            assert factory.browser.context.page.mouse.events == []
            assert PASSWORD not in str(error.value.detail)
        finally: await manager.aclose()
    asyncio.run(run())


def test_fast_input_is_limited_but_latest_screenshot_is_paced_not_rejected(tmp_path):
    async def run():
        manager, _ = setup_manager(tmp_path)
        try:
            started = await manager.start('owner', 'atcoder')
            await manager.interact('owner', started['login_id'], 'key', key='Tab')
            with pytest.raises(HTTPException) as error:
                await manager.interact('owner', started['login_id'], 'text', text=PASSWORD)
            assert error.value.status_code == 429
            await manager.screenshot('owner', started['login_id'])
            before = asyncio.get_running_loop().time()
            assert await manager.screenshot('owner', started['login_id'])
            assert asyncio.get_running_loop().time() - before >= 0.35
        finally: await manager.aclose()
    asyncio.run(run())


def test_mismatch_does_not_bind_and_browser_remains_for_human_correction(tmp_path):
    async def run():
        manager, _ = setup_manager(tmp_path)
        try:
            started = await manager.start('owner', 'atcoder', 'different-user')
            result = await manager.confirm('owner', started['login_id'])
            assert result['status'] == 'failed' and result['reason'] == 'account_mismatch'
            assert manager.flow is not None and not manager.accounts.path.exists()
        finally: await manager.aclose()
    asyncio.run(run())


def test_missing_runtime_and_launch_failures_are_explicit_and_do_not_expose_errors(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr('acmweb.browser_login.async_playwright', None)
        manager, factory = setup_manager(tmp_path)
        manager.playwright_factory = None
        with pytest.raises(HTTPException) as error: await manager.start('owner', 'atcoder')
        assert error.value.status_code == 503 and 'Playwright' in error.value.detail
        manager.playwright_factory = factory
        factory.failure = RuntimeError(PASSWORD)
        try:
            with pytest.raises(HTTPException) as error: await manager.start('owner', 'atcoder')
            assert error.value.status_code == 503 and PASSWORD not in error.value.detail
            assert manager.flow is None
        finally: await manager.aclose()
    asyncio.run(run())


def test_private_runtime_is_removed_after_failed_browser_launch(tmp_path):
    async def run():
        manager, factory = setup_manager(tmp_path)
        original_launch = factory.launch
        async def failed_launch(**kwargs):
            await original_launch(**kwargs)
            raise RuntimeError(PASSWORD)
        factory.launch = failed_launch
        try:
            with pytest.raises(HTTPException) as error:
                await manager.start('owner', 'atcoder')
            assert error.value.status_code == 503
            assert PASSWORD not in error.value.detail
        finally:
            await manager.aclose()
        assert not factory.runtime_dir.exists()
        assert factory.stopped and not manager.cleanup_failed
    asyncio.run(run())


def test_shutdown_drains_done_tasks_when_gather_returns_without_yielding(tmp_path, monkeypatch):
    async def run():
        manager, _ = setup_manager(tmp_path)
        done = asyncio.get_running_loop().create_future()
        done.set_result(None)
        # Reproduce a completed disposal whose scheduled discard callback has
        # not run, with Python 3.12+'s immediate completed-gather behavior.
        manager.cleanup_tasks.add(done)
        calls = 0
        async def immediate_gather(*tasks, **kwargs):
            nonlocal calls
            calls += 1
            assert calls <= 2, 'shutdown spun without yielding for discard callbacks'
            assert all(task.done() for task in tasks)
            return [task.result() for task in tasks]
        monkeypatch.setattr(asyncio, 'gather', immediate_gather)
        await manager.aclose()
        assert manager.closed and not manager.cleanup_tasks and calls == 1
    asyncio.run(run())


def test_cookie_export_excludes_challenge_and_foreign_domain_secrets():
    cookies = [
        {'name': 'session', 'value': SECRET, 'domain': '.atcoder.jp'},
        {'name': 'cf', 'value': 'private-challenge', 'domain': '.cloudflare.com'},
        {'name': 'other', 'value': 'foreign', 'domain': 'evil.invalid'},
    ]
    assert _cookie_header('atcoder', cookies) == 'session=' + SECRET
    for value in ['injected; second=value', 'line\nbreak']:
        assert _cookie_header('atcoder', cookies + [
            {'name': 'bad', 'value': value, 'domain': 'atcoder.jp'}]) is None


def test_cancelled_start_retains_launch_references_until_worker_can_close(tmp_path):
    async def run():
        manager, factory = setup_manager(tmp_path)
        entered, resume = asyncio.Event(), asyncio.Event()
        original_start = factory.start
        async def slow_start():
            entered.set()
            await resume.wait()
            return await original_start()
        factory.start = slow_start
        start = asyncio.create_task(manager.start('owner', 'atcoder'))
        try:
            await entered.wait()
            start.cancel()
            with pytest.raises(asyncio.CancelledError): await start
            assert manager.flow is None
            resume.set()
        finally:
            resume.set()
            await manager.aclose()
        assert factory.stopped
    asyncio.run(run())


@pytest.mark.parametrize('operation', ['screenshot', 'interact', 'confirm'])
def test_unexpected_page_origin_cannot_be_screenshot_or_input_target(tmp_path, operation):
    async def run():
        manager, factory = setup_manager(tmp_path)
        try:
            started = await manager.start('owner', 'atcoder')
            factory.browser.context.page.url = 'data:text/html,untrusted'
            args = ('owner', started['login_id'])
            kwargs = {}
            if operation == 'interact':
                args += ('text',)
                kwargs['text'] = PASSWORD
            with pytest.raises(HTTPException) as error:
                await getattr(manager, operation)(*args, **kwargs)
            assert error.value.status_code == 502 and manager.flow is None
            assert not factory.browser.context.page.keyboard.events
        finally: await manager.aclose()
    asyncio.run(run())


def test_only_controlled_pages_can_navigate_and_popups_are_closed(tmp_path):
    async def run():
        manager, _ = setup_manager(tmp_path)
        try:
            await manager.start('owner', 'atcoder')
            popup = Page('atcoder')
            await manager._close_uncontrolled_page(manager.flow, popup)
            assert popup.closed
            class Route:
                request = SimpleNamespace(url='https://atcoder.jp/login', method='GET',
                    frame=SimpleNamespace(parent_frame=None, page=popup),
                    is_navigation_request=lambda: True)
                result = None
                async def abort(self, reason): self.result = 'blocked'
                async def continue_(self): self.result = 'allowed'
            route = Route()
            await manager._route(manager.flow, route)
            assert route.result == 'blocked'
        finally: await manager.aclose()
    asyncio.run(run())


def test_non_ascii_or_unknown_flow_id_is_not_a_compare_digest_error(tmp_path):
    async def run():
        manager, _ = setup_manager(tmp_path)
        try:
            await manager.start('owner', 'atcoder')
            for login_id in ['非ASCII', None, 'unknown']:
                with pytest.raises(HTTPException) as error:
                    await manager.status('owner', login_id)
                assert error.value.status_code == 404
        finally: await manager.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('location,frame,allowed', [
    ('/settings', 'main-frame', True),
    ('https://atcoder.jp/settings', 'main-frame', True),
    ('https://evil.invalid/', 'main-frame', False),
    ('https://127.0.0.1/admin', 'main-frame', False),
    ('https://atcoder.jp:444/', 'main-frame', False),
    ('https://challenges.cloudflare.com/frame', 'main-frame', False),
    ('https://challenges.cloudflare.com/frame', 'child-frame', True),
    ('https://evil.invalid/', 'child-frame', False),
])
def test_response_guard_blocks_disallowed_redirect_before_continuation(tmp_path, location, frame, allowed):
    async def run():
        manager, factory = setup_manager(tmp_path)
        try:
            await manager.start('owner', 'atcoder')
            flow = manager.flow
            session = CDPSession()
            await manager._guard_response(flow, flow.page, session, 'main-frame', {
                'requestId': 'synthetic-request', 'frameId': frame,
                'resourceType': 'Document',
                'request': {'url': 'https://atcoder.jp/login'}, 'responseStatusCode': 302,
                'responseHeaders': [{'name': 'Location', 'value': location}],
            })
            assert session.commands[0][0] == ('Fetch.continueResponse' if allowed else 'Fetch.failRequest')
            assert (manager.flow is flow) is (allowed or frame != 'main-frame')
        finally: await manager.aclose()
    asyncio.run(run())


def test_response_guard_allows_main_frame_subresources_without_treating_them_as_top_navigation(tmp_path):
    async def run():
        manager, _ = setup_manager(tmp_path)
        try:
            await manager.start('owner', 'atcoder')
            flow = manager.flow
            for resource_type, url in [
                ('Script', 'https://challenges.cloudflare.com/turnstile/v0/api.js'),
                ('Stylesheet', 'https://img.atcoder.jp/style.css'),
            ]:
                session = CDPSession()
                await manager._guard_response(flow, flow.page, session, 'main-frame', {
                    'requestId': 'synthetic-request', 'frameId': 'main-frame',
                    'resourceType': resource_type, 'request': {'url': url},
                    'responseStatusCode': 200, 'responseHeaders': [],
                })
                assert session.commands == [('Fetch.continueResponse', {'requestId': 'synthetic-request'})]
                assert manager.flow is flow
        finally: await manager.aclose()
    asyncio.run(run())


def test_unconfirmed_browser_cleanup_keeps_global_slot_closed(tmp_path, monkeypatch):
    import threading
    monkeypatch.setattr('acmweb.browser_login._SLOT', threading.BoundedSemaphore(1))
    async def run():
        manager, factory = setup_manager(tmp_path)
        async def failed_close(): raise RuntimeError(SECRET)
        try:
            started = await manager.start('owner', 'atcoder')
            factory.browser.context.close = factory.browser.close = factory.stop = failed_close
            await manager.cancel('owner', started['login_id'])
            assert manager.cleanup_failed
            with pytest.raises(HTTPException) as error: await manager.start('owner', 'atcoder')
            assert error.value.status_code == 503 and SECRET not in error.value.detail
        finally: await manager.aclose()
    asyncio.run(run())


class ChildTransport:
    """Replies to child-target commands without ever opening a browser."""
    def __init__(self): self.handlers, self.commands = {}, []
    def on(self, name, callback): self.handlers.setdefault(name, []).append(callback)
    async def emit(self, name, event):
        for callback in self.handlers.get(name, []):
            value = callback(event)
            if asyncio.iscoroutine(value): await value
    async def send(self, command, params):
        assert command == 'Target.sendMessageToTarget'
        message = json.loads(params['message'])
        self.commands.append(message)
        await self.emit('Target.receivedMessageFromTarget', {
            'sessionId': params['sessionId'],
            'message': json.dumps({'id': message['id'], 'result': {'acknowledged': True}}),
        })


def test_child_target_is_guarded_before_runtime_resumes_and_redirects_stay_guarded(tmp_path):
    async def run():
        manager, _ = setup_manager(tmp_path)
        try:
            await manager.start('owner', 'atcoder')
            parent = ChildTransport()
            flow = manager.flow
            await manager._attach_child_guard(flow, flow.page, parent, {'sessionId': 'child-session'})
            assert [command['method'] for command in parent.commands] == [
                'Fetch.enable', 'Target.setAutoAttach', 'Runtime.runIfWaitingForDebugger']
            assert parent.commands[0]['params']['patterns'][0]['requestStage'] == 'Response'
            assert parent.commands[1]['params']['waitForDebuggerOnStart'] is True
            await parent.emit('Target.receivedMessageFromTarget', {
                'sessionId': 'child-session', 'message': json.dumps({
                    'method': 'Fetch.requestPaused', 'params': {
                        'requestId': 'child-request', 'frameId': 'child-frame',
                        'resourceType': 'Image',
                        'request': {'url': 'https://challenges.cloudflare.com/image'},
                        'responseStatusCode': 302,
                        'responseHeaders': [{'name': 'Location', 'value': 'https://evil.invalid/'}],
                    }})})
            assert parent.commands[-1]['method'] == 'Fetch.failRequest'
            assert manager.flow is flow
        finally: await manager.aclose()
    asyncio.run(run())


def test_child_protocol_transport_does_not_expose_remote_errors():
    async def run():
        parent = ChildTransport()
        child = _ChildCDPSession(parent, 'child-session')
        assert await child.send('Fetch.enable') == {'acknowledged': True}
        await parent.emit('Target.detachedFromTarget', {'sessionId': 'child-session'})
        with pytest.raises(RuntimeError) as error: await child.send('Fetch.enable')
        assert SECRET not in str(error.value)
    asyncio.run(run())


def test_missing_redirect_guard_capability_fails_before_official_navigation(tmp_path):
    async def run():
        manager, factory = setup_manager(tmp_path)
        class NoAutoAttach(CDPSession):
            async def send(self, command, params=None):
                if command == 'Target.setAutoAttach': raise RuntimeError(SECRET)
                return await super().send(command, params)
        async def new_session(page): return NoAutoAttach()
        factory.browser.context.new_cdp_session = new_session
        try:
            with pytest.raises(HTTPException) as error: await manager.start('owner', 'atcoder')
            assert error.value.status_code == 503 and SECRET not in error.value.detail
            assert not factory.browser.context.page.visits
        finally: await manager.aclose()
    asyncio.run(run())


def test_dedicated_worker_keeps_creator_frame_network_guard_without_unsupported_fetch(tmp_path):
    async def run():
        manager, _ = setup_manager(tmp_path)
        try:
            await manager.start('owner', 'atcoder')
            parent = ChildTransport()
            flow = manager.flow
            await manager._attach_child_guard(flow, flow.page, parent, {
                'sessionId': 'worker-session', 'targetInfo': {'type': 'worker'}})
            assert [command['method'] for command in parent.commands] == [
                'Target.setAutoAttach', 'Runtime.runIfWaitingForDebugger']
            assert manager.flow is flow
        finally: await manager.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('failure,target_gone', [
    ('Target.setAutoAttach was not supported', False),
    ('No session with given id', True),
])
def test_worker_tracking_capability_failure_is_explicit_and_handles_destroyed_targets(tmp_path, failure, target_gone):
    async def run():
        manager, _ = setup_manager(tmp_path)
        class BrokenTransport(ChildTransport):
            async def send(self, command, params):
                message = json.loads(params['message'])
                self.commands.append(message)
                if message['method'] == 'Target.setAutoAttach':
                    await self.emit('Target.receivedMessageFromTarget', {
                        'sessionId': params['sessionId'], 'message': json.dumps({
                            'id': message['id'], 'error': {'code': -32601, 'message': failure}})})
                else:
                    await self.emit('Target.receivedMessageFromTarget', {
                        'sessionId': params['sessionId'], 'message': json.dumps({
                            'id': message['id'], 'result': {}})})
        try:
            started = await manager.start('owner', 'atcoder')
            flow = manager.flow
            parent = BrokenTransport()
            await manager._attach_child_guard(flow, flow.page, parent, {
                'sessionId': 'worker-session', 'targetInfo': {'type': 'worker'}})
            assert 'Runtime.runIfWaitingForDebugger' not in [command['method'] for command in parent.commands]
            if target_gone:
                assert manager.flow is flow
            else:
                assert manager.flow is None
                status = await manager.status('owner', started['login_id'])
                assert status['status'] == 'unavailable'
                assert status['reason'] == 'guard_capability_unavailable'
                assert failure not in json.dumps(status)
        finally: await manager.aclose()
    asyncio.run(run())



def test_shared_worker_is_closed_without_resuming_an_unverified_transport(tmp_path):
    async def run():
        manager, _ = setup_manager(tmp_path)
        class SharedParent:
            def __init__(self): self.closed = []
            async def send(self, command, params):
                assert command == 'Target.closeTarget'
                self.closed.append(params['targetId'])
                return {'success': True}
        try:
            await manager.start('owner', 'atcoder')
            parent = SharedParent()
            flow = manager.flow
            await manager._attach_child_guard(flow, flow.page, parent, {
                'sessionId': 'shared-session',
                'targetInfo': {'type': 'shared_worker', 'targetId': 'shared-target'}})
            assert parent.closed == ['shared-target'] and manager.flow is flow
        finally: await manager.aclose()
    asyncio.run(run())


def test_terminal_reason_is_owner_scoped_stable_and_visible_to_status_and_image(tmp_path):
    async def run():
        manager, _ = setup_manager(tmp_path)
        try:
            started = await manager.start('owner', 'atcoder')
            manager._invalidate(manager.flow, 'guard_protocol_error')
            manager.clear('owner')
            status = await manager.status('owner', started['login_id'])
            assert status['status'] == 'unavailable' and status['reason'] == 'guard_protocol_error'
            with pytest.raises(HTTPException) as error: await manager.screenshot('owner', started['login_id'])
            assert error.value.status_code == 410
            assert error.value.headers['X-ACM-Login-Reason'] == 'guard_protocol_error'
            assert '[guard_protocol_error]' in error.value.detail
            with pytest.raises(HTTPException) as error: await manager.status('different-owner', started['login_id'])
            assert error.value.status_code == 404
        finally: await manager.aclose()
    asyncio.run(run())


@pytest.mark.skipif(os.environ.get('ACM_TEST_BROWSER_OFFICIAL_PASSIVE') != '1',
                    reason='Explicit opt-in only: loads official AtCoder login without credentials')
def test_official_atcoder_passive_page_survives_delayed_challenge_worker_creation(tmp_path):
    """Read-only availability probe; never fill, click, or solve a challenge."""
    async def run():
        accounts = Accounts(Settings(data_dir=tmp_path, config_path=tmp_path/'absent.toml'))
        manager = BrowserLogin(accounts, SimpleNamespace(tasks={}))
        try:
            started = await manager.start('synthetic-passive-owner', 'atcoder')
            for delay in (1, 4, 10):
                await asyncio.sleep(delay)
                status = await manager.status('synthetic-passive-owner', started['login_id'])
                assert status['status'] == 'human_required', status['reason']
                assert (await manager.screenshot('synthetic-passive-owner', started['login_id'])).startswith(b'\x89PNG')
            assert not accounts.path.exists()
        finally: await manager.aclose()
    asyncio.run(run())

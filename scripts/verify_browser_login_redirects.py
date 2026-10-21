"""Opt-in real Chromium redirect probe against disposable loopback HTTPS only.

Usage: .venv/bin/python scripts/verify_browser_login_redirects.py --run-local-transport-probe
Requires the project, requirements-browser.lock, its Chromium install, and openssl.
No personal browser profile, credentials, page dump, or raw headers. A separate
--run-official-atcoder-passive opt-in visits the official login page anonymously
for 15 seconds without typing, clicking, logging in, or solving challenges.
"""
import argparse
import asyncio
import json
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace


def fixture_server():
    """Bind an OS-selected free port; record synthetic request metadata only."""
    received = []
    worker_js = """(async () => {
      postMessage({phase:'started', calc:6*7});
      const targets = [self.location.origin + '/worker-fetch-same',
                       'https://bad.invalid/worker-fetch-outside',
                       self.location.origin + '/worker-fetch302'];
      const failures = [];
      for (const url of targets) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 3000);
        try { await fetch(url, {signal:controller.signal}); failures.push(false); }
        catch { failures.push(true); } finally { clearTimeout(timer); }
      }
      postMessage({phase:'done', calc:6*7, failures});
    })();"""
    nested_js = ("const child = new Worker(URL.createObjectURL(new Blob(["
                 + json.dumps(worker_js) + "], {type:'text/javascript'})));"
                 "child.onmessage = event => postMessage({...event.data, nested:true, parentCalc:6*7});")

    def worker_page(source):
        return ('<html><script>window.workerProbe={phase:"pending"};'
                'const worker=new Worker(URL.createObjectURL(new Blob(['
                + json.dumps(source) + '],{type:"text/javascript"})));'
                'worker.onmessage=event=>window.workerProbe=event.data;'
                '</script><body>Local worker probe</body></html>')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            # Do not retain headers or content from any real platform.
            received.append((self.path, self.command, body == b'payload=synthetic'))
            redirects = {
                '/allowed302': (302, 'https://atcoder.jp/final'),
                '/bad302': (302, 'https://bad.invalid/forbidden'),
                '/private307': (307, f'https://127.0.0.1:{self.server.server_port}/forbidden'),
                '/badtop302': (302, 'https://img.atcoder.jp/forbidden'),
                '/chain302': (302, 'https://atcoder.jp/bad302'),
                '/image-redirect': (302, 'https://bad.invalid/forbidden-image'),
                '/post307': (307, 'https://atcoder.jp/post-final'),
                '/post302': (302, 'https://atcoder.jp/post-final'),
                '/worker-fetch302': (302, 'https://bad.invalid/forbidden-worker'),
            }
            if self.path in redirects:
                status, target = redirects[self.path]
                self.send_response(status)
                self.send_header('Location', target)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            pages = {
                '/login': ('<html><body>'
                           '<form method="post" action="/post307"><input name="payload" value="synthetic">'
                           '<button id="submit307">Send307</button></form>'
                           '<form method="post" action="/post302"><input name="payload" value="synthetic">'
                           '<button id="submit302">Send302</button></form></body></html>'),
                '/subresource-test': '<html><img src="/image-redirect"></html>',
                '/subresource-good': '<html><img src="https://img.atcoder.jp/pixel"></html>',
                '/embedded-good': '<html><iframe src="https://challenges.cloudflare.com/frame"></iframe></html>',
                '/embedded-redirect-test': ('<html><iframe '
                                            'src="https://challenges.cloudflare.com/frame-with-image"></iframe></html>'),
                '/frame-with-image': '<html><img src="/image-redirect"></html>',
                '/worker-test': worker_page(worker_js),
                '/nested-worker-test': worker_page(nested_js),
                '/oopif-worker-test': ('<html><iframe '
                                       'src="https://challenges.cloudflare.com/worker-frame"></iframe></html>'),
                '/worker-frame': worker_page(worker_js),
                '/oopif-nested-worker-test': ('<html><iframe '
                                              'src="https://challenges.cloudflare.com/nested-worker-frame"></iframe></html>'),
                '/nested-worker-frame': worker_page(nested_js),
            }
            content = pages.get(self.path, '<html><body>Local probe final</body></html>').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass  # An intentionally blocked redirect may close its page.

    return ThreadingHTTPServer(('127.0.0.1', 0), Handler), received


class LocalRuntime:
    """Use production manager defaults, changing only local TLS and DNS mapping."""
    def __init__(self, port):
        self.port = port

    def __call__(self):
        return self

    async def start(self):
        from playwright.async_api import async_playwright
        self.driver = await async_playwright().start()
        self.chromium = self
        return self

    async def launch(self, **kwargs):
        assert kwargs['chromium_sandbox'] is True
        hosts = ('atcoder.jp', 'img.atcoder.jp', 'bad.invalid', 'challenges.cloudflare.com')
        rules = ', '.join(f'MAP {host} 127.0.0.1:{self.port}' for host in hosts)
        # Every named host is loopback or fails DNS; no external destination is needed.
        kwargs['args'] = ['--no-proxy-server', '--host-resolver-rules=' + rules + ', MAP * ~NOTFOUND']
        self.browser = await self.driver.chromium.launch(**kwargs)
        return self

    async def new_context(self, **kwargs):
        assert kwargs['ignore_https_errors'] is False
        kwargs['ignore_https_errors'] = True  # This fixture's self-signed certificate only.
        return await self.browser.new_context(**kwargs)

    async def close(self):
        await self.browser.close()

    async def stop(self):
        await self.driver.stop()


async def probe(directory, port, received):
    from acmweb.accounts import Accounts
    from acmweb.browser_login import BrowserLogin
    from acmweb.settings import Settings

    # These ten cases were first run independently before the OOPIF regression.
    cases = [
        ('allowed302', '/allowed302', False, True, '/final'),
        ('outside302', '/bad302', True, False, None),
        ('private307', '/private307', True, False, None),
        ('top_subdomain302', '/badtop302', True, False, None),
        ('second_hop302', '/chain302', True, False, None),
        ('subresource302', '/subresource-test', True, True, '/image-redirect'),
        ('subresource_good', '/subresource-good', False, True, '/pixel'),
        ('embedded_good', '/embedded-good', False, True, '/frame'),
        ('post307', 'submit307', False, True, '/post-final'),
        ('post302', 'submit302', False, True, '/post-final'),
        # A cross-site iframe runs in a separate Chromium target; its redirects
        # must be guarded too, not just requests issued by the main page target.
        ('oopif_subresource302', '/embedded-redirect-test', True, True, '/image-redirect'),
        ('blob_worker_guarded', '/worker-test', True, True, '/worker-test'),
        ('nested_blob_worker_guarded', '/nested-worker-test', True, True, '/nested-worker-test'),
        ('oopif_blob_worker_guarded', '/oopif-worker-test', True, True, '/worker-frame'),
        ('oopif_nested_blob_worker_guarded', '/oopif-nested-worker-test', True, True, '/nested-worker-frame'),
    ]
    results = []
    for name, target, must_block, survives, required_path in cases:
        local_data = directory / name
        accounts = Accounts(Settings(data_dir=local_data, config_path=local_data / 'absent.toml'))
        manager = BrowserLogin(accounts, SimpleNamespace(tasks={}), playwright_factory=LocalRuntime(port))
        offset = len(received)
        try:
            await manager.start('synthetic-owner', 'atcoder')
            flow = manager.flow
            try:
                if target.startswith('/'):
                    await flow.page.goto('https://atcoder.jp' + target,
                                         wait_until='domcontentloaded', timeout=5000)
                else:
                    await flow.page.locator('#' + target).click(timeout=5000)
                # Let iframe/image requests settle without depending on page load
                # (intentionally blocked subresources can fail independently).
                await asyncio.sleep(0.4)
            except Exception:
                if not must_block:
                    raise
            worker_report = None
            if 'worker_guarded' in name:
                worker_frame = flow.page
                if name.startswith('oopif_'):
                    worker_frame = next(frame for frame in flow.page.frames
                                        if frame.url.startswith('https://challenges.cloudflare.com/'))
                await worker_frame.wait_for_function('window.workerProbe?.phase === "done"', timeout=7000)
                worker_report = await worker_frame.evaluate('window.workerProbe')
                assert worker_report['calc'] == 42, 'worker computation/postMessage did not run'
                assert worker_report['failures'] == [False, True, True], (
                    f"{name}: worker fetch failures={worker_report['failures']}")
                if 'nested' in name:
                    assert worker_report.get('nested') is True and worker_report.get('parentCalc') == 42
                assert not flow.cancelled, f'{name}: worker must not revoke the page flow'
            # Sample after every asynchronous worker fetch has settled.
            observed = received[offset:]
            forbidden = [row for row in observed if row[0].startswith('/forbidden')
                         or row[0] == '/worker-fetch-outside']
            assert not forbidden, f'{name}: forbidden destination received {len(forbidden)} request(s)'
            assert flow.cancelled is not survives, f'{name}: unexpected lifecycle state'
            if required_path:
                assert any(row[0] == required_path for row in observed), f'{name}: fixture path not exercised'
            final = [row for row in observed if row[0] == '/post-final']
            if name == 'post307':
                assert final[-1][1:] == ('POST', True), '307 must preserve POST and synthetic body'
            if name == 'post302':
                assert final[-1][1:] == ('GET', False), '302 must use browser GET redirect semantics'
            result = {'case': name, 'passed': True,
                      'blocked_destination_requests': len(forbidden),
                      'fixture_requests': len(observed)}
            if worker_report:
                same = sum(row[0] == '/worker-fetch-same' for row in observed)
                redirect = sum(row[0] == '/worker-fetch302' for row in observed)
                assert same and redirect, f'{name}: allowed fetch and redirect start must execute'
                result.update(worker_compute_message=True, same_origin_fetch_requests=same,
                              outside_fetch_requests=0, redirect_start_requests=redirect)
            results.append(result)
        finally:
            await manager.aclose()
    return results


async def official_atcoder_passive(directory):
    """Fresh anonymous production context; observe availability, never login."""
    from acmweb.accounts import Accounts
    from acmweb.browser_login import BrowserLogin
    from acmweb.settings import Settings

    accounts = Accounts(Settings(data_dir=directory, config_path=directory / 'absent.toml'))
    manager = BrowserLogin(accounts, SimpleNamespace(tasks={}))
    checks = []
    try:
        started = await manager.start('anonymous-passive-probe', 'atcoder')
        login_id = started['login_id']
        began = time.monotonic()
        for seconds in (1, 5, 10, 15):
            await asyncio.sleep(max(0, seconds - (time.monotonic() - began)))
            status = await manager.status('anonymous-passive-probe', login_id)
            assert status['status'] == 'human_required', 'anonymous page was prematurely revoked'
            image = await manager.screenshot('anonymous-passive-probe', login_id)
            assert image.startswith(b'\x89PNG'), 'anonymous page screenshot unavailable'
            checks.append({'wait_seconds': seconds, 'status': status['status'],
                           'screenshot_available': True, 'screenshot_bytes': len(image)})
            image = None
        assert not accounts.path.exists(), 'passive observation must not persist account credentials'
        return {'official_atcoder_anonymous_passive': True, 'observation_seconds': 15,
                'personal_profiles_used': False, 'login_attempted': False,
                'credentials_persisted': False, 'checks': checks}
    finally:
        await manager.aclose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-local-transport-probe', action='store_true',
                        help='Explicitly launch disposable Chromium and a loopback TLS fixture.')
    parser.add_argument('--run-official-atcoder-passive', action='store_true',
                        help='Explicitly observe a fresh anonymous official AtCoder page for 15 seconds.')
    args = parser.parse_args()
    if not (args.run_local_transport_probe or args.run_official_atcoder_passive):
        parser.error('Select an explicit local transport or official anonymous passive probe.')
    if args.run_official_atcoder_passive:
        with tempfile.TemporaryDirectory(prefix='acm-atcoder-anonymous-passive-') as temporary:
            directory = Path(temporary)
            directory.chmod(0o700)
            print(json.dumps(asyncio.run(official_atcoder_passive(directory)), ensure_ascii=False, indent=2))
        if not args.run_local_transport_probe:
            return
    openssl = shutil.which('openssl')
    if not openssl:
        parser.error('openssl is required to generate the disposable fixture certificate.')
    with tempfile.TemporaryDirectory(prefix='acm-browser-redirect-probe-') as temporary:
        directory = Path(temporary)
        directory.chmod(0o700)
        cert, key = directory / 'cert.pem', directory / 'key.pem'
        subprocess.run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=atcoder.jp'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        server, received = fixture_server()
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            results = asyncio.run(probe(directory, server.server_port, received))
            print(json.dumps({'transport': 'real Chromium / disposable loopback HTTPS',
                              'personal_profiles_used': False,
                              'original_case_count': 10, 'total_case_count': len(results),
                              'blocked_destination_requests': sum(row['blocked_destination_requests'] for row in results),
                              'results': results}, ensure_ascii=False, indent=2))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    main()

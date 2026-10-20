#!/usr/bin/env python3
"""Opt-in real Firefox/Tampermonkey test, using an isolated temporary profile.

Never opens an existing browser profile, reads account.toml, or submits code.
The only test cookie is synthetic; the real AtCoder page remains anonymous.
Requires local Firefox, geckodriver, network, and the project's requests package.
"""
import argparse
import base64
import json
import inspect
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
from zipfile import ZipFile
from urllib.parse import urlsplit

import requests

BETA_URL = 'https://www.tampermonkey.net/xpi/firefox-current-beta.xpi'
ELEMENT = 'element-6066-11e4-a52e-4f735466cecf'
SHADOW = 'shadow-6066-11e4-a52e-4f735466cecf'


def wait_for(operation, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = operation()
            if value:
                return value
        except (requests.RequestException, RuntimeError, KeyError):
            pass
        time.sleep(.2)
    raise RuntimeError('required_browser_state_not_observed_at_line_' + str(inspect.currentframe().f_back.f_lineno))


def verify(output):
    firefox = shutil.which('firefox')
    driver = shutil.which('geckodriver')
    if not firefox or not driver:
        raise RuntimeError('firefox_and_geckodriver_are_required')
    snap_root = Path.home() / 'snap' / 'firefox' / 'common'
    parent = snap_root if snap_root.is_dir() else Path(tempfile.gettempdir())
    report = {'test': 'real-tampermonkey-beta', 'official_login_verified': False,
              'test_page': 'https://atcoder.jp/', 'synthetic_cookie_only': True,
              'real_submission_performed': False}
    with tempfile.TemporaryDirectory(prefix='acm-beta-verify-', dir=parent) as directory:
        work = Path(directory)
        profile = work / 'profile'
        profile.mkdir()
        response = requests.get(BETA_URL, timeout=45)
        response.raise_for_status()
        target = urlsplit(response.url)
        if target.scheme != 'https' or target.hostname not in {'www.tampermonkey.net', 'firefox.tampermonkey.net'}:
            raise RuntimeError('unexpected_beta_download_origin')
        xpi = work / 'tampermonkey-beta.xpi'
        xpi.write_bytes(response.content)
        with ZipFile(xpi) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            report['manager_version'] = manifest['version']
            report['signature_files_present'] = any(name.startswith('META-INF/') for name in archive.namelist())
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()
        process = subprocess.Popen([
            driver, '--allow-system-access', '--host', '127.0.0.1', '--port', str(port),
            '--profile-root', str(work),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        local = requests.Session()
        local.trust_env = False
        base = f'http://127.0.0.1:{port}'
        endpoint = None

        def call(method, path, data=None):
            result = local.request(method, (endpoint or base) + path, json=data, timeout=45)
            if not result.ok:
                # Do not log driver response bodies, which may include page data.
                raise RuntimeError('webdriver_' + str(result.status_code) + '_' + method + '_' + path.split('/')[1])
            return result.json()['value']

        def js(source, *arguments):
            return call('POST', '/execute/sync', {'script': source, 'args': list(arguments)})

        try:
            wait_for(lambda: local.get(base + '/status', timeout=1).ok)
            session = call('POST', '/session', {'capabilities': {'alwaysMatch': {
                'browserName': 'firefox',
                'moz:firefoxOptions': {'args': ['-headless', '-profile', str(profile)], 'prefs': {
                    'browser.shell.checkDefaultBrowser': False,
                    'browser.startup.homepage_override.mstone': 'ignore',
                    'browser.aboutwelcome.enabled': False,
                }},
            }}})
            endpoint = base + '/session/' + session['sessionId']
            report['firefox_version'] = session['capabilities']['browserVersion']
            call('POST', '/timeouts', {'pageLoad': 30000})
            addon_id = call('POST', '/moz/addon/install', {'path': str(xpi), 'temporary': True})
            if addon_id != 'firefoxbeta@tampermonkey.net':
                raise RuntimeError('unexpected_addon_id')
            call('POST', '/moz/context', {'context': 'chrome'})
            uuid = js('return JSON.parse(Services.prefs.getStringPref("extensions.webextensions.uuids"))[arguments[0]]', addon_id)
            call('POST', '/moz/context', {'context': 'content'})
            call('POST', '/url', {'url': f'moz-extension://{uuid}/options.html'})
            wait_for(lambda: js('return !!document.querySelector(".CodeMirror")'))
            js('const no=[...document.querySelectorAll("button")].find(x=>x.textContent==="No");if(no)no.click()')
            js('[...document.querySelectorAll("div[title]")].find(x=>x.title==="Create a new script...").click()')
            source = (Path(__file__).resolve().parents[1] / 'src/acmweb/userscripts/acm-lab-cookie-helper.user.js').read_text()
            report['helper_version'] = re.search(r'// @version\s+(\S+)', source).group(1)
            js('const node=document.querySelector(".CodeMirror");const editor=node.wrappedJSObject?.CodeMirror||node.CodeMirror;editor.setValue(arguments[0]);', source)
            js('document.querySelector("button[title=Save]").click()')
            wait_for(lambda: js('return location.hash.includes("nav=dashboard") && document.body.innerText.includes("ACM Lab")'))
            report['helper_installed_in_real_manager'] = True
            js('location.hash="nav=settings";const field=document.querySelector("select[name=\\\"Config mode\\\"]");field.value="100";field.dispatchEvent(new Event("change",{bubbles:true}))')
            permission = wait_for(lambda: js('return document.querySelector("select[name=\\\"Allow scripts to access cookies\\\"]")?.value'))
            if permission != 'all':
                raise RuntimeError('enable_cookie_permission_in_this_temporary_manager_then_retry')
            report['cookie_permission'] = 'Settings → Config mode: Advanced → Security → Allow scripts to access cookies: All'
            call('POST', '/url', {'url': 'https://atcoder.jp/'})
            wait_for(lambda: js('return !!document.getElementById("acm-lab-cookie-helper")'))
            call('POST', '/cookie', {'cookie': {
                'name': 'acm_beta_synthetic_httponly', 'value': 'synthetic-fixture-only',
                'domain': 'atcoder.jp', 'path': '/', 'secure': True, 'httpOnly': True, 'sameSite': 'Lax',
            }})
            call('POST', '/refresh', {})
            wait_for(lambda: js('return !!document.getElementById("acm-lab-cookie-helper")'))
            report['synthetic_hidden_from_document_cookie'] = not js('return document.cookie.includes("acm_beta_synthetic_httponly")')
            host = call('POST', '/element', {'using': 'css selector', 'value': '#acm-lab-cookie-helper'})[ELEMENT]
            shadow = call('GET', '/element/' + host + '/shadow')[SHADOW]

            def shadow_element(selector):
                return call('POST', '/shadow/' + shadow + '/element', {'using': 'css selector', 'value': selector})[ELEMENT]

            button = shadow_element('button:not(.close)')
            call('POST', '/element/' + button + '/click', {})
            status_element = shadow_element('[role=status]')
            status = wait_for(lambda: (text if (text := call('GET', '/element/' + status_element + '/text')).startswith('已复制') else None))
            diagnostic = call('GET', '/element/' + shadow_element('[role=note]') + '/text')
            if '包含 HttpOnly' not in status or '来源：油猴 API' not in diagnostic:
                raise RuntimeError('real_cookie_api_http_only_not_observed')
            report['assistant_status'] = status
            report['assistant_diagnostic'] = diagnostic
            call('POST', '/moz/context', {'context': 'chrome'})
            copied = js('const transfer=Cc["@mozilla.org/widget/transferable;1"].createInstance(Ci.nsITransferable);transfer.init(null);transfer.addDataFlavor("text/plain");Services.clipboard.getData(transfer,Services.clipboard.kGlobalClipboard);const value={};transfer.getTransferData("text/plain",value);const text=value.value.QueryInterface(Ci.nsISupportsString).data;return text.includes("acm_beta_synthetic_httponly=synthetic-fixture-only")')
            js('Cc["@mozilla.org/widget/clipboardhelper;1"].getService(Ci.nsIClipboardHelper).copyString("")')
            call('POST', '/moz/context', {'context': 'content'})
            if not copied:
                raise RuntimeError('synthetic_httponly_not_in_real_clipboard')
            report['synthetic_httponly_in_real_clipboard'] = copied
            report['clipboard_cleared'] = True
            if output:
                output.mkdir(parents=True, exist_ok=True)
                screenshot = call('GET', '/element/' + host + '/screenshot')
                (output / 'tampermonkey-beta-real-httponly.png').write_bytes(base64.b64decode(screenshot))
                (output / 'tampermonkey-beta-real.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            return report
        finally:
            if endpoint:
                try:
                    call('POST', '/moz/context', {'context': 'chrome'})
                    js('Cc["@mozilla.org/widget/clipboardhelper;1"].getService(Ci.nsIClipboardHelper).copyString("")')
                except (requests.RequestException, RuntimeError):
                    pass
                try:
                    call('DELETE', '')
                except (requests.RequestException, RuntimeError):
                    pass
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            local.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-real-browser', action='store_true', help='Explicitly allow official Beta download and a real anonymous AtCoder page visit')
    parser.add_argument('--output', type=Path, help='Write only a credential-free assistant screenshot and count-only JSON report')
    options = parser.parse_args()
    if not options.run_real_browser:
        parser.error('This is an opt-in manual check; pass --run-real-browser.')
    try:
        result = verify(options.output)
    except Exception as error:
        print(json.dumps({'ok': False, 'error_type': type(error).__name__, 'check': str(error) if type(error) is RuntimeError else 'external_operation_failed', 'note': 'No browser payload, cookie or page body is logged.'}))
        return 1
    print(json.dumps({'ok': True, **result}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

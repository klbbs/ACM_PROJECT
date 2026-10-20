"""Deterministic, credential-free extension downloads, including wheel installs."""
import io
import json
import zipfile
from functools import lru_cache
from importlib.resources import files

from fastapi import HTTPException, Response

ASSETS = ('manifest.json', 'cookies.js', 'popup.html', 'popup.css', 'popup.js')
USERSCRIPT_NAME = 'acm-lab-cookie-helper.user.js'
USERSCRIPT_VERSION = '1.0.0'


@lru_cache(maxsize=1)
def userscript_source():
    """A generic script: never embed an installation URL, credentials or user ID."""
    return files('acmweb').joinpath('userscripts', USERSCRIPT_NAME).read_text(encoding='utf-8')


@lru_cache(maxsize=2)
def build_extension(browser):
    if browser not in {'chromium', 'firefox'}:
        raise HTTPException(404, '没有此浏览器的插件包')
    root = files('acmweb').joinpath('browser_extension')
    manifest = json.loads(root.joinpath('manifest.json').read_text(encoding='utf-8'))
    if browser == 'firefox':
        manifest.pop('minimum_chrome_version', None)
        manifest['browser_specific_settings'] = {'gecko': {
            'id': 'cookie-helper@acm-lab.local', 'strict_min_version': '128.0',
            'data_collection_permissions': {'required': ['none']},
        }}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ASSETS:
            data = json.dumps(manifest, ensure_ascii=False, indent=2).encode() if name == 'manifest.json' else root.joinpath(name).read_bytes()
            item = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            archive.writestr(item, data)
    return output.getvalue()


def register(app):
    # Both endpoints inherit normal single/multi-user API authentication. Script
    # managers may omit the website session during installation, so provide the
    # authenticated source API for manual import rather than bypassing auth.
    @app.get('/api/browser-userscript')
    def script_source():
        return {'filename': USERSCRIPT_NAME, 'version': USERSCRIPT_VERSION, 'source': userscript_source()}

    @app.get('/api/browser-userscript/acm-lab-cookie-helper.user.js')
    def download_script():
        return Response(userscript_source(), media_type='application/javascript', headers={
            'Content-Disposition': f'attachment; filename="{USERSCRIPT_NAME}"',
            'Cache-Control': 'no-store',
        })

    @app.get('/api/browser-extension/{browser}.zip')
    def download(browser: str):
        return Response(build_extension(browser), media_type='application/zip', headers={
            'Content-Disposition': f'attachment; filename="acm-lab-cookie-helper-{browser}-1.0.0.zip"',
        })

"""Private, writable web configuration layered over an optional read-only TOML."""
import hashlib
import json
import os
import re
import tempfile
import threading
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from ojcrawler.config import load, tomllib, validate_cf_api_credentials
from .codex_bridge import AIError
from .cookie_validation import cookie_summary, prepare_cookie


class AccountInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    platform: Literal['codeforces', 'atcoder', 'luogu', 'nowcoder']
    handle: str = Field(min_length=1, max_length=500)
    enrich: bool = False
    # null preserves the saved credential; empty string clears it.
    cookie: str | None = Field(default=None, max_length=16384, repr=False)
    # Both null preserve the pair; both empty explicitly remove it.
    api_key: str | None = Field(default=None, max_length=40, repr=False)
    api_secret: str | None = Field(default=None, max_length=40, repr=False)


class AccountList(BaseModel):
    model_config = ConfigDict(extra='forbid')
    items: list[AccountInput] = Field(max_length=20)
    # Opt in for connecting clients; legacy scripts can still save configuration
    # without unexpectedly contacting an external platform.
    auto_check: bool = Field(default=False, strict=True)


def normalize_handle(platform, value):
    value = value.strip()
    if '://' in value:
        parsed = urlsplit(value)
        hosts, pattern = {
            'codeforces': ({'codeforces.com', 'www.codeforces.com'}, r'/profile/([^/]+)/?'),
            'atcoder': ({'atcoder.jp', 'www.atcoder.jp'}, r'/users/([^/]+)/?'),
            'luogu': ({'luogu.com.cn', 'www.luogu.com.cn'}, r'/user/([0-9]+)/?'),
            'nowcoder': ({'nowcoder.com', 'www.nowcoder.com', 'ac.nowcoder.com'}, r'/(?:profile|acm/contest/profile)/([0-9]+)(?:/.*)?'),
        }[platform]
        match = re.fullmatch(pattern, parsed.path)
        if parsed.scheme != 'https' or parsed.hostname not in hosts or parsed.username or parsed.password or parsed.port or not match:
            raise AIError('请填写所选平台的 HTTPS 个人主页链接，或直接填写用户名 / UID')
        value = match[1]
    pattern = r'[0-9]+' if platform in {'luogu', 'nowcoder'} else r'[A-Za-z0-9_.-]{1,100}'
    if not re.fullmatch(pattern, value) or (platform in {'luogu', 'nowcoder'} and int(value) <= 0):
        raise AIError('账号格式无效；洛谷和牛客使用正整数 UID，CF 和 AtCoder 使用用户名')
    return value


class Accounts:
    def __init__(self, settings):
        self.settings = settings
        self.path = settings.data_dir / 'accounts.web.toml'
        self.lock = threading.RLock()

    @staticmethod
    def _revision(doc):
        return hashlib.sha256(json.dumps(doc.get('accounts', []), sort_keys=True).encode()).hexdigest()

    def revision(self):
        with self.lock:
            return self._revision(self.document())

    def bind_verified_account(self, platform, handle, cookie, revision, *, login_method='browser'):
        """Merge a freshly verified credential atomically, preserving other accounts."""
        with self.lock:
            doc = self.document()
            if self._revision(doc) != revision:
                raise AIError('扫码期间账号配置已更改，请重新开始扫码')
            handle = normalize_handle(platform, handle)
            items = [AccountInput(platform=a['platform'], handle=str(a['handle']),
                                  enrich=a.get('enrich', False)) for a in doc.get('accounts', [])]
            target = next((a for a in items if a.platform == platform and a.handle == handle), None)
            if target is None:
                if len(items) >= 20:
                    raise AIError('账号数量已达上限，请先移除不再使用的绑定')
                target = AccountInput(platform=platform, handle=handle)
                items.append(target)
            target.cookie = cookie
            if login_method not in {'qr', 'browser'}:
                raise ValueError('Unsupported verified login method')
            return self.save(AccountList(items=items), verified_login=(platform, handle, login_method))

    @property
    def effective_path(self):
        return self.path if self.path.exists() else self.settings.config_path

    def document(self):
        try:
            path = self.effective_path
            return tomllib.loads(path.read_text()) if path.exists() else {}
        except (OSError, ValueError):
            raise AIError('账号配置无法读取，请检查服务端 TOML 配置') from None

    def public(self):
        doc = self.document()
        isolated = getattr(self.settings, 'isolated', False)
        def credential_info(account):
            cookie = account.get('cookie') or ('' if isolated else os.environ.get(account.get('cookie_env', ''), ''))
            summary = cookie_summary(cookie)
            if not cookie and account.get('cookie_env'):
                summary = {'status': 'invalid', 'code': 'environment_missing', 'pair_count': 0,
                           'message': '配置了 Cookie 环境变量，但当前服务没有读到值，请联系维护者。'}
            return summary
        return {'configured': bool(doc.get('accounts')), 'source': 'web' if self.path.exists() else 'file',
                'items': [{'platform': a['platform'], 'handle': a['handle'], 'enrich': a.get('enrich', False),
                           'has_cookie': bool(a.get('cookie') or a.get('cookie_env')),
                           'has_api_key': bool(a.get('api_key') and a.get('api_secret')) if a['platform'] == 'codeforces' else False,
                           'cookie_info': credential_info(a),
                           'login_method': a.get('login_method', 'cookie' if a.get('cookie') or a.get('cookie_env') else 'public'),
                           'credential_source': 'environment' if a.get('cookie_env') else ('saved' if a.get('cookie') else 'public')}
                          for a in doc.get('accounts', [])]}

    def save(self, payload, *, verified_login=None):
        with self.lock:
            doc = self.document()
            previous = {(a['platform'], str(a['handle'])): a for a in doc.get('accounts', [])}
            accounts, seen = [], set()
            for item in payload.items:
                try:
                    handle = normalize_handle(item.platform, item.handle)
                except ValueError:
                    raise AIError('主页链接格式无效') from None
                key = (item.platform, handle)
                if key in seen:
                    raise AIError('账号重复，请每个平台账号只保留一条')
                seen.add(key)
                account = {'platform': item.platform, 'handle': handle, 'enrich': item.enrich}
                if item.api_key is None and item.api_secret is None:
                    old = previous.get(key, {})
                    account.update({name: old[name] for name in ('api_key', 'api_secret') if old.get(name)})
                elif item.platform == 'codeforces' and item.api_key == '' and item.api_secret == '':
                    pass
                else:
                    try:
                        validate_cf_api_credentials(item.platform, item.api_key, item.api_secret)
                    except ValueError as error:
                        raise AIError(str(error)) from None
                    account.update(api_key=item.api_key, api_secret=item.api_secret)
                if item.cookie is None:
                    old = previous.get(key, {})
                    account.update({k: old[k] for k in ('cookie', 'cookie_env') if old.get(k)})
                    if old.get('login_method') in {'qr', 'browser', 'cookie'} and (account.get('cookie') or account.get('cookie_env')):
                        account['login_method'] = old['login_method']
                    # Repair safe single-header copy artifacts on explicit save.
                    # Unrelated legacy credentials are retained, but public()
                    # reports their precise syntax error instead of pretending
                    # the session has been verified.
                    if account.get('cookie'):
                        try:
                            account['cookie'] = prepare_cookie(account['cookie'])
                        except ValueError:
                            pass
                elif item.cookie:
                    try:
                        account['cookie'] = prepare_cookie(item.cookie)
                        account['login_method'] = 'cookie'
                    except ValueError as error:
                        raise AIError(str(error)) from None
                if verified_login and key == verified_login[:2]:
                    account['login_method'] = verified_login[2]
                accounts.append(account)
            crawler = dict(doc.get('crawler', {}))
            crawler.update(database=str(self.settings.database), output_dir=str(self.settings.data_dir / 'runs'))
            lines = ['[crawler]'] + [f'{k} = {json.dumps(v, ensure_ascii=True)}' for k, v in crawler.items()]
            for account in accounts:
                lines.extend(['', '[[accounts]]'] + [f'{k} = {json.dumps(v, ensure_ascii=True)}' for k, v in account.items()])
            fd, temp = tempfile.mkstemp(prefix='.accounts-', suffix='.toml', dir=self.settings.data_dir)
            try:
                with os.fdopen(fd, 'w') as output:
                    output.write('\n'.join(lines) + '\n')
                if accounts:
                    load(temp)  # Validate before replacing a working configuration.
                os.replace(temp, self.path)
            except (ValueError, OSError):
                raise AIError('账号配置未保存，请检查 Cookie 环境变量、配置格式和目录写入权限') from None
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        return self.public()

"""Private, writable web configuration layered over an optional read-only TOML."""
import json
import os
import re
import tempfile
import threading
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from ojcrawler.config import load, tomllib
from .codex_bridge import AIError


class AccountInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    platform: Literal['codeforces', 'atcoder', 'luogu', 'nowcoder']
    handle: str = Field(min_length=1, max_length=500)
    enrich: bool = False
    # null preserves the saved credential; empty string clears it.
    cookie: str | None = Field(default=None, max_length=16384, repr=False)


class AccountList(BaseModel):
    model_config = ConfigDict(extra='forbid')
    items: list[AccountInput] = Field(max_length=20)


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
        self.lock = threading.Lock()

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
        return {'configured': bool(doc.get('accounts')), 'source': 'web' if self.path.exists() else 'file',
                'items': [{'platform': a['platform'], 'handle': a['handle'], 'enrich': a.get('enrich', False),
                           'has_cookie': bool(a.get('cookie') or a.get('cookie_env')),
                           'credential_source': 'environment' if a.get('cookie_env') else ('saved' if a.get('cookie') else 'public')}
                          for a in doc.get('accounts', [])]}

    def save(self, payload):
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
                if item.cookie is None:
                    old = previous.get(key, {})
                    account.update({k: old[k] for k in ('cookie', 'cookie_env') if old.get(k)})
                elif item.cookie:
                    cookie = item.cookie.strip()
                    if not cookie or any(ord(c) < 32 or ord(c) == 127 for c in cookie) or '=' not in cookie:
                        raise AIError('Cookie 应为单行 name=value; name2=value2，不能包含换行或请求头')
                    account['cookie'] = cookie
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

"""One private endpoint configuration for QQ practice and the ACM CLI.

Missing configuration means waiting for deployment, never a legacy fallback.
Cookies are usable only on the exact HTTPS origin where they were obtained.
"""
from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path.home() / '.local/share/hermes-acm'
PENDING_ORIGIN = 'https://pending.invalid'


class ConnectionUnavailable(ValueError):
    def __init__(self, state, message):
        self.state = state
        super().__init__(message)


def canonical_origin(value):
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        raise ValueError('invalid ACM HTTPS origin')
    url = urlsplit(value)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path not in ('', '/') or '\\' in value
            or any(ord(c) < 32 for c in value)):
        raise ValueError('invalid ACM HTTPS origin')
    port = url.port
    host = url.hostname.lower()
    if ':' in host:
        host = '[' + host + ']'
    return 'https://' + host + (':' + str(port) if port and port != 443 else '')


@dataclass(frozen=True)
class Connection:
    origin: str = PENDING_ORIGIN
    ready: bool = False

    def require_ready(self):
        if not self.ready:
            raise ConnectionUnavailable('waiting_server', 'ACM 旧服务器已停用，新服务器部署完成后再连接；未发起抽题。')
        return canonical_origin(self.origin)

    def cookie(self, path, *, optional=False):
        origin = self.require_ready()
        try:
            data = json.loads(Path(path).read_text()) if Path(path).exists() else {}
        except (OSError, ValueError):
            raise ConnectionUnavailable('needs_login', 'ACM 本地会话不可用，请在本机运行 hermes acm login。') from None
        if not isinstance(data, dict):
            raise ConnectionUnavailable('needs_login', 'ACM 本地会话无效，请重新登录。')
        cookie = data.get('cookie')
        if not cookie:
            if optional:
                return None
            raise ConnectionUnavailable('needs_login', 'ACM 尚未登录，请在本机运行 hermes acm login。')
        try:
            same = canonical_origin(data.get('origin')) == origin
        except ValueError:
            same = False
        if not same:
            raise ConnectionUnavailable('session_origin_mismatch', 'ACM 站点已变更，旧登录态未发送。请在本机运行 hermes acm login。')
        if not isinstance(cookie, str) or len(cookie) > 8192 or any(ord(c) < 32 for c in cookie):
            raise ConnectionUnavailable('needs_login', 'ACM 本地会话无效，请重新登录。')
        return cookie


def load_connection(path=None):
    path = Path(path) if path is not None else ROOT / 'server.json'
    if not path.exists():
        return Connection()
    try:
        data = json.loads(path.read_text())
        if (not isinstance(data, dict) or data.get('schema') != 1
                or data.get('state') not in {'pending', 'ready'}
                or set(data) - {'schema', 'state', 'origin', 'target'}):
            raise ValueError('invalid server configuration')
        if data['state'] == 'pending':
            return Connection()
        return Connection(canonical_origin(data['origin']), True)
    except (OSError, ValueError, TypeError, KeyError):
        raise ConnectionUnavailable('invalid_server_config', 'ACM 服务器配置无效，已停止连接；请管理员检查。') from None

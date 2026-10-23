import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from .adapters import ADAPTERS


@dataclass
class Account:
    platform: str
    handle: str
    cookie_env: str | None = None
    enrich: bool = False
    cookie: str | None = field(default=None, repr=False)


@dataclass
class Config:
    database: Path
    output_dir: Path
    accounts: list[Account]
    interval: float = 2.1
    timeout: float = 30
    retries: int = 3
    page_size: int = 100
    max_pages: int = 1000
    overlap_seconds: int = 604800


def load(path):
    path = Path(path).resolve()
    with path.open("rb") as f:
        doc = tomllib.load(f)
    settings = doc.get("crawler", {})
    known = {"database", "output_dir", "interval", "timeout", "retries", "page_size", "max_pages", "overlap_seconds"}
    if set(settings) - known:
        raise ValueError(f"未知 crawler 配置字段: {sorted(set(settings) - known)}")
    if set(doc) - {"crawler", "accounts"}:
        raise ValueError("配置仅支持 crawler 和 accounts")
    accounts, seen = [], set()
    for item in doc.get("accounts", []):
        if set(item) - {"platform", "handle", "cookie_env", "enrich", "cookie", "login_method"}:
            raise ValueError("accounts 存在未知字段")
        if item.get('login_method') is not None and item['login_method'] not in {'qr', 'cookie', 'browser', 'public'}:
            raise ValueError('login_method 必须为受支持的登录方式')
        platform, handle = item.get("platform"), str(item.get("handle", "")).strip()
        if platform not in ADAPTERS or not handle:
            raise ValueError("账号需填写支持的 platform 和非空 handle")
        if platform in {"luogu", "nowcoder"} and not handle.isascii():
            raise ValueError(f"{platform} 使用数字 UID")
        if platform in {"luogu", "nowcoder"} and (not handle.isdigit() or int(handle) <= 0):
            raise ValueError(f"{platform} 使用正整数 UID")
        if (platform, handle) in seen:
            raise ValueError(f"重复账号: {platform}/{handle}")
        seen.add((platform, handle))
        cookie_env = item.get("cookie_env")
        cookie = item.get("cookie")
        if cookie is not None and (not isinstance(cookie, str) or not cookie.strip() or '\r' in cookie or '\n' in cookie):
            raise ValueError("cookie 必须为非空单行字符串")
        if cookie and cookie_env:
            raise ValueError("cookie 和 cookie_env 只能选择一种")
        if cookie_env is not None and not isinstance(cookie_env, str):
            raise ValueError("cookie_env 必须是环境变量名")
        if cookie_env and not os.environ.get(cookie_env):
            raise ValueError(f"环境变量 {cookie_env} 未设置，不能静默忽略 Cookie")
        if not isinstance(item.get("enrich", False), bool):
            raise ValueError("enrich 必须为布尔值")
        accounts.append(Account(platform, handle, cookie_env, item.get("enrich", False), cookie))
    if not accounts:
        raise ValueError("至少配置一个账号")
    config = Config(path.parent / settings.pop("database", "data/accounts.sqlite3"),
                    path.parent / settings.pop("output_dir", "data/runs"), accounts, **settings)
    for field in ("interval", "timeout"):
        value = getattr(config, field)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not 0 < value <= 300:
            raise ValueError(f"{field} 应在 (0, 300] 范围")
    if config.interval < 2.1:
        raise ValueError("interval 至少为 2.1 秒，以满足 CF 和 AtCoder Problems 请求间隔")
    for field in ("retries", "page_size", "max_pages", "overlap_seconds"):
        value = getattr(config, field)
        if type(value) is not int or value < (0 if field in {"retries", "overlap_seconds"} else 1):
            raise ValueError(f"{field} 不是有效整数")
    if config.retries > 10 or config.page_size > 1000:
        raise ValueError("retries 最大 10，page_size 最大 1000")
    return config

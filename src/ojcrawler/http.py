import logging
import time
import threading
from collections import defaultdict
from urllib.parse import urlsplit

import requests

from .models import CrawlError

log = logging.getLogger(__name__)
_LAST_REQUEST = defaultdict(float)
_HOST_LOCKS = defaultdict(threading.Lock)


class Client:
    def __init__(self, *, interval=2.1, timeout=30, retries=3, cookie=None, cookie_host=None):
        self.interval = interval
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; OJAccountCrawler/0.1)",
                                     "Accept-Language": "zh-CN,en;q=0.8"})
        # Cookie is scoped to this adapter's client; never stored in collected data.
        self.cookie = cookie
        self.cookie_host = cookie_host
        if cookie and not cookie_host:
            raise ValueError("设置 Cookie 时必须指定所属域名")
        if cookie:
            # requests rebuilds Cookie headers during redirects. A domain-scoped
            # jar preserves the same platform session without forwarding it to
            # unrelated hosts.
            for part in cookie.split(";"):
                name, separator, value = part.strip().partition("=")
                if separator and name:
                    self.session.cookies.set(name, value, domain=cookie_host, path="/", secure=True)
        # Share host pacing across all accounts collected in this process.
        self.last = _LAST_REQUEST

    def get(self, url, params=None, headers=None):
        host = urlsplit(url).netloc
        request_headers = dict(headers or {})
        if self.cookie and host == self.cookie_host:
            request_headers["Cookie"] = self.cookie
        for attempt in range(self.retries + 1):
            with _HOST_LOCKS[host]:
                time.sleep(max(0, self.interval - (time.monotonic() - self.last[host])))
                self.last[host] = time.monotonic()
            try:
                response = self.session.get(url, params=params, headers=request_headers,
                                            timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt == self.retries:
                    # Do not expose exception text: it can contain URLs or request secrets.
                    raise CrawlError(f"{host}: 网络请求失败 ({type(exc).__name__})") from exc
                self._backoff(attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < self.retries:
                    self._backoff(attempt, response.headers.get("Retry-After"))
                    continue
            if response.status_code != 200:
                raise CrawlError(f"{host}: HTTP {response.status_code}，请检查账号、访问权限或 Cookie")
            response.encoding = response.apparent_encoding if not response.encoding else response.encoding
            return response.text
        raise CrawlError(f"{host}: 请求失败")

    @staticmethod
    def _backoff(attempt, retry_after=None):
        try:
            delay = min(60, max(1, float(retry_after))) if retry_after else min(60, 2 ** (attempt + 1))
        except ValueError:
            delay = min(60, 2 ** (attempt + 1))
        time.sleep(delay)

    def json(self, url, params=None, headers=None):
        import json
        try:
            return json.loads(self.get(url, params, headers))
        except (ValueError, TypeError) as exc:
            raise CrawlError("预期 JSON，实际返回登录页、验证页或不兼容的响应") from exc

    def close(self):
        self.session.close()

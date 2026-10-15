from types import SimpleNamespace

import pytest
import requests

from ojcrawler.http import Client
from ojcrawler.models import CrawlError


def response(status=200, text="{}", headers=None):
    return SimpleNamespace(status_code=status, text=text, headers=headers or {}, encoding="utf-8")


def test_retry_and_retry_after(monkeypatch):
    delays = []
    monkeypatch.setattr("ojcrawler.http.time.sleep", delays.append)
    client = Client(retries=1)
    values = iter([response(429, headers={"Retry-After": "4"}), response(text='{"ok":true}')])
    monkeypatch.setattr(client.session, "get", lambda *a, **k: next(values))
    assert client.json("https://example.test/data")["ok"]
    assert 4 in delays


def test_cookie_host_scope_and_unauthorized_not_retried(monkeypatch):
    monkeypatch.setattr("ojcrawler.http.time.sleep", lambda _: None)
    client = Client(cookie="secret-cookie", cookie_host="atcoder.jp")
    calls = []
    def get(url, **kwargs):
        calls.append(kwargs["headers"])
        return response(200 if "kenkoooo" in url else 403)
    monkeypatch.setattr(client.session, "get", get)
    assert client.json("https://kenkoooo.com/data") == {}
    assert "Cookie" not in calls[0]
    with pytest.raises(CrawlError) as error:
        client.get("https://atcoder.jp/profile")
    assert calls[1]["Cookie"] == "secret-cookie"
    assert len(calls) == 2 and "secret-cookie" not in str(error.value)


def test_network_error_does_not_leak_url_or_cookie(monkeypatch):
    monkeypatch.setattr("ojcrawler.http.time.sleep", lambda _: None)
    client = Client(retries=0)
    def get(*a, **k):
        raise requests.ConnectionError("https://host.test/?private=secret")
    monkeypatch.setattr(client.session, "get", get)
    with pytest.raises(CrawlError) as error:
        client.get("https://host.test/")
    assert "secret" not in str(error.value)


def test_non_json_is_not_empty_success(monkeypatch):
    client = Client()
    monkeypatch.setattr(client, "get", lambda *a: '<html>login</html>')
    with pytest.raises(CrawlError):
        client.json("https://host.test/")


def test_cookie_jar_preserves_platform_redirects_without_third_party_leak():
    client = Client(cookie="session=secret", cookie_host="atcoder.jp")
    own = client.session.prepare_request(requests.Request("GET", "https://atcoder.jp/settings"))
    external = client.session.prepare_request(requests.Request("GET", "https://kenkoooo.com/data"))
    assert own.headers.get("Cookie") == "session=secret"
    assert "Cookie" not in external.headers

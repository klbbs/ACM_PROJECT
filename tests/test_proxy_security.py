"""Proxy-chain checks that do not require a live Nginx, Caddy or container.

Two kinds of evidence live here:

* static checks of the shipped samples (``acmweb.deploy_check``), plus negative
  cases proving the checker rejects unsafe variants;
* behavioural checks of the launcher and of the trusted-proxy resolution the
  deployment depends on.

The negative cases are deliberately included: a checker that cannot fail proves
nothing. No test in this file starts Nginx/Caddy or claims that a real proxy
deployment was exercised.
"""
import asyncio
import sys
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from acmweb import deploy_check
from acmweb.app import create_app
from acmweb.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
NGINX = (ROOT / 'deploy/nginx.conf.example').read_text(encoding='utf-8')
CADDY = (ROOT / 'deploy/Caddyfile.example').read_text(encoding='utf-8')
COMPOSE = (ROOT / 'compose.yaml').read_text(encoding='utf-8')
ENV_EXAMPLE = (ROOT / '.env.example').read_text(encoding='utf-8')
TOKEN = 'synthetic-proxy-test-token-long-enough'


def production_app(tmp_path, multi_user=True, hosts=('testserver',)):
    settings = Settings(data_dir=tmp_path, config_path=tmp_path / 'none.toml', production=True,
                        multi_user=multi_user, enable_codex=False, access_token=TOKEN,
                        allowed_hosts=hosts)
    return create_app(settings)


def test_shipped_samples_pass_the_static_checks():
    assert deploy_check.check_nginx(NGINX) == []
    assert deploy_check.check_caddy(CADDY) == []
    assert deploy_check.check_compose(COMPOSE) == []
    assert deploy_check.check_env_example(ENV_EXAMPLE) == []


def test_nginx_sample_forwards_a_client_derived_host_and_discards_client_xff():
    root = deploy_check.parse_nginx(NGINX)
    server = [s for s in root.children if s.header.startswith('server') and
              any(' ssl' in d for d in s.directives)][0]
    proxy = [c for c in server.children if c.header.startswith('location /')][0]
    host = [d for d in proxy.directives if d.startswith('proxy_set_header Host')]
    assert host == ['proxy_set_header Host $host']
    assert not any('acm.example.com' in value for value in host)
    forwarded = [d for d in proxy.directives if d.startswith('proxy_set_header X-Forwarded-For')]
    assert forwarded == ['proxy_set_header X-Forwarded-For $remote_addr']
    assert not any('proxy_add_x_forwarded_for' in d for d in proxy.directives)
    # Application status codes and Retry-After survive the hop.
    assert 'proxy_intercept_errors off' in proxy.directives
    assert 'proxy_next_upstream off' in proxy.directives
    assert 'proxy_hide_header Server' in proxy.directives


def test_nginx_parser_keeps_quoted_json_intact():
    root = deploy_check.parse_nginx(NGINX)
    gateway = [c for c in root.children if c.header.startswith('server') and
               any(child.header.startswith('location @') for child in c.children)][0]
    location = [c for c in gateway.children if c.header.startswith('location @')][0]
    reply = [d for d in location.directives if d.startswith('return 5')][0]
    assert reply.startswith('return 503 \'{"detail"')
    assert location.directives.count('internal') == 1


@pytest.mark.parametrize('before, after, expected', [
    ('proxy_set_header Host $host;', 'proxy_set_header Host acm.example.com;', 'Host'),
    ('proxy_set_header X-Forwarded-For $remote_addr;',
     'proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;', 'proxy_add_x_forwarded_for'),
    ('proxy_intercept_errors off;', 'proxy_intercept_errors on;', 'proxy_intercept_errors'),
    ('proxy_hide_header Server;', '', 'Server'),
    ('proxy_next_upstream off;', '', 'proxy_next_upstream'),
    ('server_tokens off;', 'server_tokens on;', 'server_tokens'),
    ('client_max_body_size 512k;', 'client_max_body_size 10m;', 'client_max_body_size'),
    ('proxy_pass http://127.0.0.1:8000;', 'proxy_pass http://0.0.0.0:8000;', 'proxy_pass'),
    ('ssl_protocols TLSv1.2 TLSv1.3;', 'ssl_protocols TLSv1 TLSv1.1 TLSv1.2 TLSv1.3;', 'TLSv1'),
    ('ssl_protocols TLSv1.2 TLSv1.3;', '', 'ssl_protocols'),
    ('if ($host != acm.example.com) { return 444; }', '', 'Host 守卫'),
    ('proxy_set_header X-Forwarded-Host $host;',
     'proxy_set_header X-Forwarded-Host acm.example.com;', 'X-Forwarded-Host'),
])
def test_nginx_checker_rejects_unsafe_variants(before, after, expected):
    assert before in NGINX, before
    problems = deploy_check.check_nginx(NGINX.replace(before, after, 1))
    assert problems, 'unsafe variant must not pass'
    assert any(expected in problem for problem in problems), problems


def test_nginx_checker_rejects_duplicate_security_headers_on_proxied_responses():
    variant = NGINX.replace(
        'proxy_hide_header Server;',
        'proxy_hide_header Server;\n        add_header Content-Security-Policy "default-src \'none\'" always;',
        1,
    )
    problems = deploy_check.check_nginx(variant)
    assert any('content-security-policy' in problem for problem in problems)
    # The proxy's own error page may still define headers; it never reaches the app.
    assert deploy_check.check_nginx(NGINX) == []


def test_nginx_checker_rejects_unbalanced_configuration():
    problems = deploy_check.check_nginx(NGINX + '\nserver {\n')
    assert problems and '花括号' in problems[0]


@pytest.mark.parametrize('before, after, expected', [
    ('    header -Server\n', '', 'Server'),
    ('reverse_proxy 127.0.0.1:8000', 'reverse_proxy 0.0.0.0:8000', 'reverse_proxy'),
    ('    header -Server\n', '    header -Server\n    header Content-Security-Policy "x"\n',
     'Content-Security-Policy'),
    ('    header -Server\n', '    header -Server\n    header Strict-Transport-Security "max-age=1"\n',
     'Strict-Transport-Security'),
])
def test_caddy_checker_rejects_unsafe_variants(before, after, expected):
    assert before in CADDY, before
    problems = deploy_check.check_caddy(CADDY.replace(before, after, 1))
    assert problems and any(expected in problem for problem in problems), problems


@pytest.mark.parametrize('before, after, expected', [
    ('127.0.0.1:8000:8000', '8000:8000', '回环'),
    ('      - ALL\n', '', 'cap_drop'),
    ('    read_only: true\n', '', '只读'),
    ('    security_opt:\n', '    privileged: true\n    security_opt:\n', 'privileged'),
])
def test_compose_checker_rejects_public_or_privileged_variants(before, after, expected):
    assert before in COMPOSE, before
    problems = deploy_check.check_compose(COMPOSE.replace(before, after, 1))
    assert problems and any(expected in problem for problem in problems), problems


def test_env_checker_rejects_wildcard_trust_and_hosts():
    assert any('ACM_TRUSTED_PROXIES' in problem for problem in
               deploy_check.check_env_example(ENV_EXAMPLE.replace('ACM_TRUSTED_PROXIES=127.0.0.1',
                                                                  'ACM_TRUSTED_PROXIES=*')))
    assert any('ACM_ALLOWED_HOSTS' in problem for problem in
               deploy_check.check_env_example(ENV_EXAMPLE.replace(
                   'ACM_ALLOWED_HOSTS=localhost,127.0.0.1,[::1]', 'ACM_ALLOWED_HOSTS=*')))


def test_deploy_check_cli_exits_zero_for_the_repository():
    assert deploy_check.main(['--root', str(ROOT)]) == 0


def test_launcher_disables_server_header_and_scopes_forwarded_trust(tmp_path, monkeypatch):
    from acmweb import main as launcher
    captured = {}
    monkeypatch.setattr(uvicorn, 'run', lambda app, **kwargs: captured.update(kwargs))
    monkeypatch.setattr(sys, 'argv', ['acm-web'])
    monkeypatch.setenv('ACM_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('FORWARDED_ALLOW_IPS', raising=False)
    monkeypatch.delenv('ACM_MULTI_USER', raising=False)
    launcher.main()
    assert captured['server_header'] is False
    assert captured['proxy_headers'] is True
    assert captured['forwarded_allow_ips'] == '127.0.0.1'
    assert captured['forwarded_allow_ips'] != '*'
    assert captured['limit_concurrency'] == 100

    monkeypatch.setenv('FORWARDED_ALLOW_IPS', '10.9.8.7')
    launcher.main()
    assert captured['forwarded_allow_ips'] == '10.9.8.7'


def resolve_client(peer, headers, trusted='127.0.0.1'):
    seen = {}

    async def app(scope, receive, send):
        seen['client'] = scope['client']
        seen['scheme'] = scope['scheme']

    middleware = ProxyHeadersMiddleware(app, trusted_hosts=trusted)
    scope = {'type': 'http', 'scheme': 'http', 'client': (peer, 12345),
             'headers': [(name.lower().encode(), value.encode()) for name, value in headers]}
    asyncio.run(middleware(scope, None, None))  # type: ignore[arg-type]
    return seen


def test_forwarded_headers_from_an_untrusted_peer_are_ignored():
    # A client connecting directly can send X-Forwarded-For itself; it must not
    # change the address the application rate-limits and logs.
    seen = resolve_client('198.51.100.9', [('X-Forwarded-For', '203.0.113.7')])
    assert seen['client'][0] == '198.51.100.9'
    seen = resolve_client('198.51.100.9', [('X-Forwarded-Proto', 'https')])
    assert seen['scheme'] == 'http'


def test_trusted_proxy_chain_resolves_the_real_client_not_a_spoofed_entry():
    seen = resolve_client('127.0.0.1', [('X-Forwarded-For', '203.0.113.7')])
    assert seen['client'][0] == '203.0.113.7'
    # Client-supplied entries are appended before the address the edge observed,
    # so the right-most untrusted hop wins.
    seen = resolve_client('127.0.0.1', [('X-Forwarded-For', '1.2.3.4, 203.0.113.7')])
    assert seen['client'][0] == '203.0.113.7'
    seen = resolve_client('127.0.0.1', [('X-Forwarded-Proto', 'https')])
    assert seen['scheme'] == 'https'


def test_missing_forwarded_header_leaves_the_proxy_address_in_place():
    # Documents the deployment caveat: when the app is reached through a trusted
    # proxy that does not set X-Forwarded-For, every client shares one address
    # and therefore one per-IP bucket.
    seen = resolve_client('127.0.0.1', [])
    assert seen['client'][0] == '127.0.0.1'


def test_security_headers_are_emitted_once_by_the_application(tmp_path):
    expected = ['content-security-policy', 'x-frame-options', 'x-content-type-options',
                'referrer-policy', 'permissions-policy']
    app = production_app(tmp_path, multi_user=True)
    with TestClient(app, base_url='https://testserver') as client:
        for path in ('/api/health', '/api/auth/status'):
            response = client.get(path)
            for header in expected + ['strict-transport-security']:
                assert len(response.headers.get_list(header)) == 1, (path, header)
            assert response.headers.get_list('server') == []
            assert response.headers['strict-transport-security'] == 'max-age=31536000'

    single = production_app(tmp_path / 'single', multi_user=False)
    with TestClient(single, base_url='https://testserver') as client:
        response = client.get('/api/health')
        # The single-user middleware sends these; Permissions-Policy and HSTS are
        # currently multi-user only (tracked as a residual in docs/SECURITY_REVIEW.md).
        for header in ['content-security-policy', 'x-frame-options', 'x-content-type-options',
                       'referrer-policy']:
            assert len(response.headers.get_list(header)) == 1, header
        assert response.headers.get_list('server') == []


def test_host_allowlist_accepts_configured_host_with_port_and_rejects_unknown(tmp_path):
    app = production_app(tmp_path, multi_user=True)
    with TestClient(app, base_url='https://testserver') as client:
        assert client.get('/api/health', headers={'Host': 'testserver'}).status_code == 200
        assert client.get('/api/health', headers={'Host': 'testserver:443'}).status_code == 200
        rejected = client.get('/api/health', headers={'Host': 'evil.invalid'})
        assert rejected.status_code == 400
        assert 'Invalid host header' in rejected.text
        assert rejected.headers['x-content-type-options'] == 'nosniff'


def test_host_matching_is_exact_and_case_sensitive(tmp_path):
    # Characterisation, not approval: Starlette compares the header verbatim, so
    # an uppercase or trailing-dot host is rejected even though DNS is case
    # insensitive. The shipped samples normalise the host at the edge ($host);
    # update this test together with any app-side normalisation.
    app = production_app(tmp_path, multi_user=True)
    with TestClient(app, base_url='https://testserver') as client:
        assert client.get('/api/health', headers={'Host': 'TESTserver'}).status_code == 400
        assert client.get('/api/health', headers={'Host': 'testserver.'}).status_code == 400

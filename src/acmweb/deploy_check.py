"""Static checks for the reverse-proxy samples shipped in ``deploy/``.

These checks parse configuration text only. They never start Nginx, Caddy or a
container, so a passing run proves the samples keep the properties this project
depends on -- not that a particular deployment is reachable or hardened:

* the application's ``Host`` allowlist still receives a client-derived host,
* a single edge proxy discards client-supplied ``X-Forwarded-For`` instead of
  appending to it,
* the proxy does not retry or intercept application responses, so status codes
  and ``Retry-After`` survive the hop,
* security headers are defined once, by the application (except for the proxy's
  own gateway error page, which has no application response to inherit from),
* the published port stays on loopback and the container keeps its runtime
  restrictions.

Run ``python -m acmweb.deploy_check`` from the repository root, or pass
``--root``. Exit code 1 means at least one sample drifted from those properties.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Keep in sync with the bounded body in multiuser.py (524288 bytes).
APP_BODY_LIMIT = 524288


class ConfigError(ValueError):
    """The sample could not be parsed at all."""


@dataclass
class Block:
    header: str
    directives: list[str] = field(default_factory=list)
    children: list["Block"] = field(default_factory=list)


def parse_nginx(text: str) -> Block:
    """Parse an Nginx-style block tree, ignoring comments and quoted braces."""
    root = Block("<root>")
    stack = [root]
    buffer: list[str] = []
    quote: str | None = None
    for char in _strip_comments(text):
        if quote:
            buffer.append(char)
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            buffer.append(char)
        elif char == "{":
            stack[-1].children.append(Block(" ".join("".join(buffer).split())))
            stack.append(stack[-1].children[-1])
            buffer = []
        elif char == "}":
            if len(stack) == 1:
                raise ConfigError("出现多余的右花括号")
            _flush(stack[-1], buffer)
            buffer = []
            stack.pop()
        elif char == ";":
            _flush(stack[-1], buffer)
            buffer = []
        else:
            buffer.append(char)
    if quote:
        raise ConfigError("引号未闭合")
    if len(stack) != 1:
        raise ConfigError("花括号未闭合")
    _flush(stack[-1], buffer)
    return root


def _flush(block: Block, buffer: list[str]) -> None:
    value = " ".join("".join(buffer).split())
    if value:
        block.directives.append(value)


def _strip_comments(text: str) -> str:
    out: list[str] = []
    quote: str | None = None
    comment = False
    for char in text:
        if comment:
            if char == "\n":
                comment = False
                out.append(char)
            continue
        if quote:
            out.append(char)
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            out.append(char)
        elif char == "#":
            comment = True
        else:
            out.append(char)
    return "".join(out)


def _blocks(block: Block) -> list[Block]:
    found = [block]
    for child in block.children:
        found.extend(_blocks(child))
    return found


def _servers(root: Block) -> list[Block]:
    return [child for child in root.children if child.header.split()[:1] == ["server"]]


def _locations(server: Block) -> list[Block]:
    return [child for child in server.children if child.header.startswith("location")]


def _directives(block: Block, prefix: str) -> list[str]:
    return [value for value in block.directives if value.startswith(prefix)]


def _size(value: str) -> int:
    match = re.fullmatch(r"(\d+)([kKmM]?)", value.strip())
    if not match:
        raise ConfigError(f"无法解析大小 {value!r}")
    return int(match[1]) * {"": 1, "k": 1024, "m": 1024 * 1024}[match[2].lower()]


def check_nginx(text: str) -> list[str]:
    """Return the problems found in an Nginx sample (empty means pass)."""
    try:
        root = parse_nginx(text)
    except ConfigError as exc:
        return [f"Nginx 配置无法解析：{exc}"]
    problems: list[str] = []
    servers = _servers(root)
    if len(servers) < 2:
        return ["需要默认 444 vhost 与站点 vhost 两段 server"]
    token_off = [server for server in servers if not _directives(server, "server_tokens off")]
    if token_off:
        problems.append("每个 server 都应设置 server_tokens off，避免暴露版本")
    default = [s for s in servers if any("default_server" in d for d in _directives(s, "listen"))]
    if not default:
        problems.append("缺少 default_server，未知 Host 会落到站点 vhost")
    for server in default:
        if not any(d.startswith("return 444") for d in server.directives):
            problems.append("默认 vhost 应对未知 Host 返回 444")

    tls = [s for s in servers if any(" ssl" in d for d in _directives(s, "listen"))]
    if len(tls) != 1:
        return problems + ["应恰好有一个 TLS 站点 vhost"]
    site = tls[0]
    names = [d.split()[1] for d in _directives(site, "server_name") if len(d.split()) > 1]
    domain = names[0] if names else ""
    protocols = " ".join(_directives(site, "ssl_protocols"))
    tokens = protocols.split()[1:] if protocols else []
    if not protocols:
        problems.append("TLS vhost 应显式设置 ssl_protocols")
    elif {"TLSv1", "TLSv1.1"} & set(tokens):
        problems.append("ssl_protocols 不应启用 TLSv1.0/1.1")
    elif "TLSv1.3" not in tokens:
        problems.append("ssl_protocols 应包含 TLSv1.3")
    if not any("ssl_session_tickets off" in d for d in site.directives):
        problems.append("建议 ssl_session_tickets off")

    guards = [child for child in site.children if child.header.startswith("if ($host")]
    if not guards:
        problems.append(f"TLS vhost 缺少基于域名的 Host 守卫（if ($host != {domain or '<域名>'})）")
    elif not any(domain and domain in child.header for child in guards):
        problems.append(f"Host 守卫应比较 {domain or '<域名>'}")
    elif not any("return 444" in child.directives for child in guards):
        problems.append("Host 守卫内应 return 444")

    proxied = [location for location in _locations(site) if location.header.startswith("location /")]
    if not proxied:
        return problems + ["缺少 location / 反向代理段"]
    proxy = proxied[0]
    upstreams = _directives(proxy, "proxy_pass")
    if not upstreams:
        problems.append("location / 缺少 proxy_pass")
    for value in upstreams:
        target = value.split(None, 1)[1] if " " in value else ""
        if not re.match(r"http://(127\.0\.0\.1|localhost|\[::1\]|web):\d+", target):
            problems.append(f"proxy_pass 应指向本机或内部服务名，收到 {target!r}")
    host = _directives(proxy, "proxy_set_header Host")
    if not host:
        problems.append("必须转交 Host，否则应用的域名白名单失效")
    elif not any("$" in value for value in host):
        problems.append("Host 不能写成固定合法域名，必须是客户端请求的变量")
    forwarded_host = _directives(proxy, "proxy_set_header X-Forwarded-Host")
    if forwarded_host and not all("$" in value for value in forwarded_host):
        problems.append("X-Forwarded-Host 同样不能固定为常量")
    forwarded_for = _directives(proxy, "proxy_set_header X-Forwarded-For")
    if not forwarded_for:
        problems.append("缺少 X-Forwarded-For 处理，日志与限流会看到代理地址")
    elif any("proxy_add_x_forwarded_for" in value for value in forwarded_for):
        problems.append("单层边缘应丢弃客户端 XFF，不能使用 proxy_add_x_forwarded_for")
    elif not any(value.endswith("$remote_addr") for value in forwarded_for):
        problems.append("X-Forwarded-For 应为 $remote_addr（单层代理）")
    if not _directives(proxy, "proxy_hide_header Server"):
        problems.append("应隐藏上游 Server 头")
    if not any(value == "proxy_intercept_errors off" for value in proxy.directives):
        problems.append("必须设置 proxy_intercept_errors off，保留应用状态码与 Retry-After")
    if not _directives(proxy, "proxy_next_upstream off"):
        problems.append("应设置 proxy_next_upstream off，避免重试非幂等提交")
    body_limits = _directives(proxy, "client_max_body_size") + _directives(site, "client_max_body_size")
    if not body_limits:
        problems.append("应限制请求体大小，与应用 512 KiB 上限一致")
    for value in body_limits:
        try:
            if _size(value.split()[1]) > APP_BODY_LIMIT:
                problems.append(f"client_max_body_size {value.split()[1]} 超过应用 512 KiB 上限")
        except (IndexError, ConfigError):
            problems.append(f"无法解析 client_max_body_size：{value!r}")

    error_pages = [d for block in [site, *proxied] for d in _directives(block, "error_page")]
    gateway = [child for child in site.children if child.header.startswith("location @")]
    if not error_pages:
        problems.append("缺少 error_page，代理自身故障会返回默认 HTML 错误页")
    else:
        targets = {value.split()[-1] for value in error_pages}
        names = {
            block.header.split()[-1]
            if block.header.split()[-1].startswith("@")
            else "@" + block.header.split()[-1]
            for block in gateway
        }
        if not targets & names:
            problems.append("error_page 指向的命名 location 不存在")
    if not gateway:
        problems.append("缺少命名 location 承载代理自身错误页")
    for block in gateway:
        if not any(value == "internal" for value in block.directives):
            problems.append(f"{block.header} 应标记 internal")
        if not _directives(block, "default_type application/json"):
            problems.append(f"{block.header} 应返回 application/json")
        if not any("no-store" in value for value in _directives(block, "add_header Cache-Control")):
            problems.append(f"{block.header} 应设置 Cache-Control no-store")
        if not any(d.startswith("return 5") for d in block.directives):
            problems.append(f"{block.header} 应返回明确的网关错误码")

    # The application owns CSP/HSTS/XFO on proxied responses. Only the proxy's
    # own error page may add headers, because it never reaches the application.
    gateway_ids = {id(block) for block in gateway}
    for block in _blocks(site):
        if id(block) in gateway_ids:
            continue
        for value in _directives(block, "add_header"):
            name = value.split()[1].lower() if len(value.split()) > 1 else ""
            if name in {
                "content-security-policy",
                "strict-transport-security",
                "x-frame-options",
                "referrer-policy",
                "permissions-policy",
                "x-content-type-options",
            }:
                problems.append(
                    f"{block.header or 'server'} 重复添加 {name}；安全头由应用统一生成"
                )
    return problems


def check_caddy(text: str) -> list[str]:
    """Return the problems found in a Caddyfile sample (empty means pass)."""
    problems: list[str] = []
    if text.count("{") != text.count("}"):
        problems.append("Caddyfile 花括号不配对")
    if not re.search(r"^\s*header\s+-Server\s*$", text, re.M):
        problems.append("缺少 header -Server，Caddy 会为自身响应补上 Server 头")
    duplicate = re.search(
        r"^\s*header\s+(?!-)"
        r"(Content-Security-Policy|Strict-Transport-Security|X-Frame-Options|Referrer-Policy|"
        r"Permissions-Policy|X-Content-Type-Options)",
        text,
        re.M | re.I,
    )
    if duplicate:
        problems.append(f"Caddyfile 重复定义 {duplicate.group(1)}；安全头由应用统一生成")
    proxy = re.search(r"^\s*reverse_proxy\s+(\S+)", text, re.M)
    if not proxy:
        problems.append("缺少 reverse_proxy 目标")
    elif not re.match(r"(https?://)?(127\.0\.0\.1|localhost|\[::1\]|web):\d+", proxy.group(1)):
        problems.append(f"reverse_proxy 应指向本机或内部服务名，收到 {proxy.group(1)!r}")
    if "http://0.0.0.0" in text or "https://0.0.0.0" in text:
        problems.append("reverse_proxy 不应指向通配地址")
    return problems


def check_compose(text: str) -> list[str]:
    """Check the shipped compose file keeps the app off public interfaces."""
    problems: list[str] = []
    mappings = []
    for line in text.splitlines():
        match = re.match(r'-\s*"?([^"\s]+)"?\s*$', line.strip())
        if match and re.fullmatch(r'(?:[^\s:]+:)?\d+:\d+', match.group(1)):
            mappings.append(match.group(1))
    if not mappings:
        problems.append("未找到发布端口映射")
    for mapping in mappings:
        parts = mapping.split(':')
        if len(parts) == 2 or parts[0] not in {'127.0.0.1', '[::1]', 'localhost'}:
            problems.append(f"端口映射 {mapping} 未绑定回环，应用会绕过反向代理")
    if re.search(r"^\s*privileged:\s*true", text, re.M):
        problems.append("容器不应使用 privileged")
    if not re.search(r"^\s*-\s*ALL\s*$", text, re.M):
        problems.append("容器应 cap_drop ALL")
    if not re.search(r"^\s*read_only:\s*true", text, re.M):
        problems.append("容器应使用只读根文件系统")
    if not re.search(r"no-new-privileges:true", text):
        problems.append("容器应启用 no-new-privileges")
    if re.search(r"^\s*network_mode:\s*host", text, re.M):
        problems.append("不要使用 host 网络，端口会绕过回环绑定")
    return problems


def check_env_example(text: str) -> list[str]:
    """Check the environment template does not invite unsafe settings."""
    problems: list[str] = []
    values = {}
    for line in text.splitlines():
        match = re.match(r"^([A-Z0-9_]+)\s*=\s*(.*)$", line.strip())
        if match:
            values[match.group(1)] = match.group(2).strip()
    if values.get("ACM_ENV") != "production":
        problems.append("示例应默认 ACM_ENV=production")
    hosts = values.get("ACM_ALLOWED_HOSTS", "")
    if not hosts:
        problems.append("示例应说明 ACM_ALLOWED_HOSTS")
    elif "*" in hosts:
        problems.append("ACM_ALLOWED_HOSTS 不能包含通配符")
    proxies = values.get("ACM_TRUSTED_PROXIES")
    if proxies is None:
        problems.append("示例应说明 ACM_TRUSTED_PROXIES，否则容器内看不到真实客户端 IP")
    elif "*" in proxies:
        problems.append("ACM_TRUSTED_PROXIES 不能为 *")
    token = values.get("ACM_ACCESS_TOKEN", "")
    if token and not token.startswith("replace-with-"):
        problems.append("示例不应包含看起来可用的访问令牌")
    if "OPENAI_API_KEY=" in text and not re.search(r"^#\s*OPENAI_API_KEY=", text, re.M):
        problems.append("示例不应把 OPENAI_API_KEY 作为有效配置项")
    return problems


CHECKS = (
    ("deploy/nginx.conf.example", "nginx", check_nginx),
    ("deploy/Caddyfile.example", "caddy", check_caddy),
    ("compose.yaml", "compose", check_compose),
    (".env.example", "env", check_env_example),
)


def run(root: Path) -> list[tuple[str, list[str]]]:
    results: list[tuple[str, list[str]]] = []
    for name, _, checker in CHECKS:
        path = root / name
        if not path.is_file():
            results.append((name, ["文件不存在"]))
            continue
        results.append((name, checker(path.read_text(encoding="utf-8"))))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="反向代理示例静态自检（不启动 Nginx/Caddy）")
    parser.add_argument("--root", default=".", help="仓库根目录")
    args = parser.parse_args(argv)
    failed = False
    for name, problems in run(Path(args.root)):
        if problems:
            failed = True
            print(f"[FAIL] {name}")
            for problem in problems:
                print(f"       - {problem}")
        else:
            print(f"[ OK ] {name}")
    print("\n以上仅检查示例配置文本；真实 nginx/Caddy/容器与 WAF 行为需部署者实测。")
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())

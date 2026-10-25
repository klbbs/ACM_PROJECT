# 2026-10-23 应用层安全复查（第二轮 · 11 项截图逐条核对）

范围：用户提供的 11 项截图发现，以及本轮新增的一键同步/代交接口在代理链上的真实行为。只在本机临时数据目录复现应用代码，未连接、部署或修改线上服务器、CDN、WAF、Nginx 或 Caddy。

本机**没有** nginx、caddy、docker、podman 可执行文件，因此代理链部分使用可重复的静态检查与依赖库行为测试，并明确标注“未实测真实代理”。这些是开发自检，不是版本验收，也不代表线上站点已通过外部扫描。

## 逐项结论

| 截图项 | 结论 | 关键证据 |
| --- | --- | --- |
| 1 登录按用户名计数定向锁定 | 已修复并复现 | 桶键为 `login-source:(对端,账号)`，10 次/15 分钟；换账号、换对端均不受影响 |
| 2 无效邀请码先 scrypt | 已修复并复现 | 无效邀请码在哈希前返回 400，`password_hash` 从未被调用 |
| 3 未认证保留管理员名枚举 | 已修复并复现 | 未知/保留/停用账号登录均为同一 401 正文；未知账号照样付出校验成本 |
| 4 全站共享限流桶误伤 | 已修复并复现 | 无 `auth-global` 计数；改为 2 槽并发门，503+`Retry-After`，释放即恢复 |
| 5 成员可读 OpenAPI | 已修复并复现 | 匿名 401、成员/管理员 404、单用户生产 404 |
| 6 版本/模式/初始化信息 | 应用侧已收敛 | 生产 health 仅 `{"status":"ok"}`；500 固定 JSON 且带安全头；`setup_required` 有意公开 |
| 7 presence 内部 ID | 已修复并复现 | 仅 `{username,self}` 与 `expires_after`，精确键集合断言 |
| 8 公开源码与版本对齐 | 有意公开，未当作漏洞 | 三处版本一致；327 个跟踪文件中无数据库/配置/凭据 |
| 9 Host 校验是否真生效 | 已生效并强化示例 | 令牌开关均安装；未知 Host 400 且带安全头；样例改用 `$host` |
| 10 两层代理重复安全头/错误页 | 已收敛示例，未实测真实代理 | 应用每头恰一份；nginx 只格式化自身 5xx；检查器含 20+ 反例 |
| 11 前置 IP 封禁误伤 | 应用侧已收敛，边缘属部署侧 | 不可信对端伪造 XFF 无效；会员不占匿名 IP 桶；compose 仅绑回环 |

## 逐项证据与真实复现

### 1 登录定向锁定

- 代码：`multiuser.login` 只用 `login-source:<peer>\0<username>`（10 次/900 秒），没有用户名级或全站级失败计数。
- 复现：同一对端对 `member` 连续 10 次错密码 → 第 11 次 429；同一对端改用 `site_owner` 仍 200；另一对端登录 `member` 仍 200。
- 测试：`test_login_throttle_is_scoped_to_peer_and_account_pair`、`test_login_flood_from_other_peer_cannot_lock_out_correct_password`。
- 取舍：换成大量 IP 猜同一账号时，唯一成本是 scrypt（n=2^17）与 2 个认证槽；若需要账号级保护，应由边缘 WAF 提供，不能再用“全站桶”实现。

### 2 邀请码在哈希前校验

- `Users.create`：先 `validate()`（邀请码有效期/未使用、保留名、人数、重名），再在认证槽内 `password_hash`，最后 `BEGIN IMMEDIATE` 内二次校验后写入。
- 复现：把 `acmweb.users.password_hash` 替换为“一旦调用即失败”，用无效邀请码注册 `site_owner/admin/kds/unusedname/site_owner` 全部 400、正文一致，且哈希函数 0 次调用。
- 测试：`test_bad_invitation_rejected_before_scrypt_and_reserved_name_disclosure`、`test_registration_does_not_reveal_reserved_names_before_the_invitation`。

### 3 保留用户名枚举

- 登录：未知账号用 `dummy_hash` 走同一验证路径，统一 `401 用户名或密码不正确`；停用账号即使密码正确也同一响应。
- 注册：无效邀请码时保留名与普通名一致返回 400；初始化：先校验设置令牌，错误令牌对任意用户名一致返回 403。
- 复现：`nobody_here`（不存在）、`site_owner`（保留）、`member`（存在但错密码）、停用后的 `member`（正确密码）四种请求状态码与正文完全一致；未知账号同样调用 `verify_password`（计数 2/2）。
- 测试：`test_login_errors_are_identical_for_unknown_reserved_and_inactive_accounts`、`test_unknown_user_still_pays_the_password_verification_cost`、`test_initial_setup_requires_secret_before_disclosing_reserved_name_or_state`。
- 功能边界：持有**有效**邀请码后，保留名返回 409“用户名不可用”，与“已被占用”同一答复，属于注册功能而非枚举口子。

### 4 全站共享限流桶

- 已删除 `auth-global` 计数窗口；昂贵认证改用 `AUTH_SLOTS`（2 个）非阻塞门：满时 503 + `Retry-After: 1`，释放后立即恢复，不会把整段时间锁死。
- 复现：占满两个槽后，登录与注册（有效邀请码）都返回 503+`Retry-After`，释放后登录立即 200。
- 会员不消耗匿名 IP 桶：即使预置 `ip:<对端>` 与旧 `auth-global` 满额，已登录成员仍 200。
- 测试：`test_auth_capacity_gate_covers_login_and_registration_without_a_global_window`、`test_authenticated_visitors_do_not_share_anonymous_ip_budget`、`test_auth_capacity_recovers_immediately_without_global_window_lockout`。
- 本轮后续已拆分 `auth-ip:<endpoint>:<对端>`：登录 120 次/15 分钟，注册与初始化各 30 次/15 分钟，互不消耗。单一对端、单一账号仍有 10 次/15 分钟的登录限制；匿名请求另有 300 次/分钟的同 IP 桶。共享 NAT 出口仍存在有限配额，不能据此承诺无任何误伤。

### 5 成员读取 OpenAPI

- 父应用 `docs_url/openapi_url=None`；子工作区 `isolated=True` → `private_schema` 关闭文档；`dispatch` 只转发白名单前缀，`api/auth|admin|shared|translations|social` 直接 404。
- 复现：`/api/openapi.json`、`/api/docs`、`/api/redoc` 匿名 401；成员 404；管理员同样 404；单用户生产 + 有效令牌 404。
- 测试：`test_api_contract_is_not_published_to_anonymous_members_or_admins`、`test_member_contract_is_not_published_and_presence_contains_no_internal_ids`、`test_host_is_checked_even_when_single_user_access_token_exists`。

### 6 版本、模式与初始化信息

- 生产 health 仅 `{"status":"ok"}`；单用户与多用户 `auth/status` 均不含版本或运行模式；未捕获异常返回固定 JSON，不含栈、路径或内部消息，并由最外层安全头包装器覆盖。
- `setup_required` 仍然公开：初始化向导必须知道站点是否已有管理员；创建管理员仍需部署令牌，且 30 次/15 分钟限制在认证路径上。
- 测试：`test_production_health_and_status_hide_version_and_mode`、`test_production_500_page_does_not_leak_internals`、`test_public_status_reveals_setup_state_but_no_identity`。
- 本轮后续已补齐：单/多用户统一使用 `SecureFastAPI`，在 Starlette 的错误层之外写入安全头；生产 500 使用固定 JSON，包含 HSTS、Permissions-Policy、CSP form-action，正常响应、认证拒绝、Host 拒绝和子工作区响应均去重。已开始发送的流式正文无法替换；代理自身错误仍由代理处理。

### 7 presence 内部 ID

- `/api/presence` 只返回 `{username, self}` 与 `expires_after`；用户 ID 只用于 `self` 比较，不进入响应。
- 复现：管理员与成员各发一次心跳，成员视图得到 `[{"username":"site_owner","self":false},{"username":"member","self":true}]`，数据库中管理员 ID 不出现在正文。
- 测试：`test_presence_payload_contains_only_display_fields`、`test_member_contract_is_not_published_and_presence_contains_no_internal_ids`。

### 8 公开源码与版本对齐

- 公开仓库与版本号是用户明确要求的产品行为，本轮**不**将其当作漏洞，也不据此声称凭据安全；结论只覆盖“公开内容与代码一致、私有内容不进入仓库”。
- 版本一致性：`pyproject.toml`、`frontend/package.json`、FastAPI 应用版本、最新公告 `RELEASES[0].id` 四处相同（测试守护）；发布公告与代码版本不一致时测试失败。
- 私有内容：已跟踪源码无 `data/`、`releases/`、`account.toml`、`.env`、`*.sqlite3`、`codex/`；`data/`、`account.toml`、`.env` 均被忽略。
- 测试：`test_shipped_version_metadata_is_aligned`；命令见文末。

### 9 Host 校验

- 单用户与多用户应用都安装 `TrustedHostMiddleware`（与访问令牌是否启用无关）；生产环境拒绝通配符配置；示例代理不再用固定合法域名覆盖 Host。
- 复现：未知 Host 400 `Invalid host header` 且仍带应用安全头；`testserver:443` 通过；`TESTserver`、`testserver.` 400（精确匹配，测试 `test_host_matching_is_exact_and_case_sensitive` 固定该行为）。
- 本轮把 nginx 示例改为 `proxy_set_header Host $host`：仍是客户端请求派生的主机（大写、端口、尾点归一化），不是常量，应用白名单继续生效，同时避免大小写/尾点导致的误拒。
- 本轮后续已在 `Settings.validate()` 对白名单配置去空白、转小写、去尾点并去重，在 `PUBLIC_ORIGIN` 检查前执行。入站 Host 仍沿用 Starlette 严格匹配，代理样例的 `$host` 负责归一化；直连大写或尾点 Host 不扩大白名单接受范围。

### 10 两层代理的安全头与错误页

- 应用侧：多用户生产每个安全头恰好一份（含 HSTS），无 `Server` 头；代理不得重复添加。
- nginx 示例：`server_tokens off`、`proxy_hide_header Server`、`proxy_intercept_errors off`（保留应用状态码与 `Retry-After`）、`proxy_next_upstream off`（不重试非幂等提交）、`error_page 502 503 504 = @gateway_error` 仅格式化 nginx 自身生成的网关错误（`internal`、JSON、`no-store`）、默认 vhost 444、`client_max_body_size 512k` 与应用 512 KiB 上限一致。
- 本轮补强：`ssl_protocols TLSv1.2 TLSv1.3`、`ssl_session_tickets off`、会话缓存、`client_header_timeout`、`large_client_header_buffers`。
- Caddy 示例：`header -Server`，不重复定义 CSP/HSTS；注释说明 `handle_errors` 会替换应用错误，需先验证再启用。
- 检查器与反例：`acmweb.deploy_check` 提供 `check_nginx/check_caddy/check_compose/check_env_example`，`tests/test_proxy_security.py` 用 12 个参数化 nginx 反例（固定 Host、`proxy_add_x_forwarded_for`、`server_tokens on`、`proxy_intercept_errors on`、超大请求体、公开上游、丢弃 Host 守卫…）、重复安全头与括号不配对 2 项、4 个 Caddy 反例、4 个 compose 反例与 2 个 env 断言，证明检查不是空转。
- **未实测**：本机没有 nginx/caddy 二进制，也没有容器运行时；没有跑 `nginx -t`、没有启动任何代理，未观察真实网关错误页。

### 11 前置 IP 封禁与客户端地址

- 启动器固定 `server_header=False`、`proxy_headers=True`、`forwarded_allow_ips=FORWARDED_ALLOW_IPS`（默认 `127.0.0.1`，从不默认 `*`）。
- 依赖库行为（`ProxyHeadersMiddleware`）实测：不可信对端自带 `X-Forwarded-For` 被忽略；可信代理链取最右侧不可信跳；`X-Forwarded-Proto` 仅在可信时生效；缺失 XFF 时保留代理地址。
- 应用侧会员走独立用户桶（240/分钟），不占匿名 IP 桶；compose 只映射 `127.0.0.1:8000`，并保持 `cap_drop ALL`、`read_only`、`no-new-privileges`（静态检查）。
- 部署注意：Docker + 宿主 nginx 时，容器看到的对端是 Docker 网关（如 `172.17.0.1`），默认 `127.0.0.1` 不会信任其 XFF → 所有客户端共用一个 IP 桶。需把网关地址写入 `ACM_TRUSTED_PROXIES`，或把 nginx 放进同一 compose 网络用服务名（已写入 nginx 示例注释）。
- 未实测：线上 WAF、CDN 回源、运营商 NAT 的封禁与真实客户端地址还原。

## 本轮修改

- 新增 `src/acmweb/deploy_check.py`：代理/容器/环境示例的静态自检模块与 `python -m acmweb.deploy_check` 命令（不启动代理）。
- 新增 `tests/test_proxy_security.py`（34 项）：示例自检、检查器反例、启动器参数、`ProxyHeadersMiddleware` 信任语义、应用安全头唯一性、Host 校验。
- 扩充 `tests/test_security_report.py`（8 → 20 项）：逐条复现上述行为，含第 1/3/4/5/6/7/8/9/11 项的新增断言。
- `deploy/nginx.conf.example`：TLS 协议/会话、请求头与请求体限制、`$host` 归一化说明、Docker 网关信任说明。
- `deploy/Caddyfile.example`：说明安全头归属、`handle_errors` 风险、`trusted_proxies` 只对已核实网段启用。
- Hermes 首轮仅改上述检查器、配置与证据；主代理与安全收尾代理随后补齐运行代码和回归，见后续处理。未改版本号或公告，未打新发布包或部署。

## 仍需部署者配置

1. `ACM_TRUSTED_PROXIES` / `FORWARDED_ALLOW_IPS`：必须等于实际连到应用的代理地址（Docker 场景见上）；不要填 `*`。
2. `ACM_ALLOWED_HOSTS`：小写、无通配、无尾点、不含协议或端口；`ACM_PUBLIC_ORIGIN` 为同一域名的 HTTPS 源。
3. 证书路径、是否监听 IPv6（示例中 IPv6 行需按主机能力开启）。
4. CDN：nginx 仅对已核实的 CDN 网段配置 `set_real_ip_from` + `real_ip_header`；Caddy 仅在全局块配置 `trusted_proxies static <网段>`。
5. 边缘限流/WAF：分布式猜密码、NAT 出口突发、匿名扫描需要部署侧策略，应用侧刻意不做“用户名级全站桶”。
6. 应用现在统一负责单/多用户安全头，代理不要重复加 HSTS/CSP；代理自身生成的错误响应另行配置并实测。

## 首轮建议的后续处理

- R1/R2 已补齐：`security_headers.py` 最外层 ASGI 安全头覆盖单/多用户正常、早退与未捕获 500 响应，生产错误正文不含内部信息。`tests/test_security_headers.py` 提供 focused 开发自检；这不代表其他业务正式验收。
- R3 已补齐：配置域名归一化，`tests/test_settings_host_normalization.py` 验证归一化先于 HTTPS 来源检查。入站 Host 保持严格守卫。
- R4 已补齐：`.env.example` 和 `compose.yaml` 注明宿主代理到容器时应填写实际 Docker 网关/代理 IP，而不是照抄回环地址。
- R5 已补齐：认证 IP 配额按端点拆分，登录调整为 120/900；保留来源与账号组合桶，注册配额耗尽不再连带封锁登录和初始化。
- R6 设计取舍：不使用全站账号失败桶，防止他人跨 IP 定向锁定账号；分布式猜密码仍需部署侧风控。边缘策略也应避免对 NAT 出口过度封禁。

以上完成应用代码与示例配置层面的处理。第 10、11 项涉及实际代理/CDN/WAF 的部分仍未实测，不将静态样例通过称为线上问题已修复。

## 可复查命令

```bash
# 安全报告、统一安全头与代理链开发自检
env -u PYTHONPATH .venv/bin/python -m pytest tests/test_security_report.py tests/test_security_headers.py tests/test_proxy_security.py -q

# 示例配置静态自检（退出码 1 表示漂移）
PYTHONPATH=src .venv/bin/python -m acmweb.deploy_check

# 仓库内不应出现的私有内容
git ls-files | grep -E '(^|/)(data|releases|daily|records|reports|work)/|(^|/)\.env$|account\.toml|\.sqlite3|codex/' || echo "none"
```

## 未实测声明

未在真实 nginx、Caddy、Docker、CDN 或 WAF 上验证；未执行外部扫描、渗透测试或线上探测；未使用真实凭据、未向真实平台交题。代理链结论来自示例文本的静态检查、依赖库（uvicorn `ProxyHeadersMiddleware`、Starlette `TrustedHostMiddleware`）的行为测试与应用响应头断言。所有涉及“线上是否生效”的结论都需要部署者按上文步骤自检后才能确认。

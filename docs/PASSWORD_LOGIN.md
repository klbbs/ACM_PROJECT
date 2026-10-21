# 平台登录窗口（开发版）

「连接与设置 → 平台连接」提供 Codeforces、AtCoder、洛谷的官方登录窗口，牛客使用微信扫码。手动 Cookie 与油猴仍保留在折叠区。

## 使用官方窗口

1. 点击平台卡片的「打开安全登录窗口」。可选填写期望的 handle / 用户名，洛谷填写数字 UID；留空则使用平台实际验证的身份。
2. 页面截图来自服务器里本次独立的 Chromium。点击截图中的输入框，在下方输入并发送文字；默认掩码，发送后清空。使用 Tab / Enter / Backspace / Control+A 控制官方页面。可放大并横向滚动。
3. 若出现验证码或浏览器验证，请本人直接在这个窗口完成。这里操作的是同一个服务器会话；在电脑另开的官网登录不会替这个窗口完成验证。ACM Lab 不自动识别验证码。
4. 平台页面显示已登录后点击「检测登录并绑定」。后台在同一个浏览器上下文的独立页面核对当前身份，保留原来的验证码/表单页；只有可靠身份匹配才保存 Cookie。失败保留原绑定，不能把跳转或 Cookie 存在当作通过。
5. 取消、空闲 180 秒、总计 5 分钟、退出 ACM Lab 或账号配置改变都会撤销流程。刷新截图/轮询不会无限延长会话。密码不存入账号配置，Cookie 不回显。

牛客点击「获取微信登录二维码」，由本人用微信扫描官方二维码并确认；扫码返回后仍要核验官方当前 UID。已在另一个浏览器登录不等于本次二维码已经扫码。

## 无桌面服务器的可选依赖

基本采集与手动 Cookie 功能不要求浏览器。官方登录窗口额外需要匹配版本的 Playwright 和 Chromium，无需安装桌面、Firefox、VNC，也不公开 CDP 端口。

```bash
.venv/bin/python -m pip install -r requirements-browser.lock
.venv/bin/python -m playwright install --with-deps --no-shell chromium
```

必须以普通系统用户运行并让系统支持 Chromium 沙箱；程序显式启用 `chromium_sandbox=True`，启动失败不自动降级到无沙箱。浏览器和操作系统依赖安装方式见 [Playwright 官方文档](https://playwright.dev/python/docs/browsers)。

Docker 使用可选 `server-browser` 构建目标和 `compose.browser.yaml`，保留非 root、no-new-privileges 与默认系统调用拒绝策略，并按 [Playwright 官方沙箱说明](https://playwright.dev/python/docs/docker)使用附带 seccomp profile。同时需要服务器 Codex 通道时，将 `ACM_BROWSER_IMAGE_TARGET=server-browser-codex`、`ACM_ENABLE_SERVER_CODEX=true` 写入环境配置。全局浏览器限额按进程计算，当前入口保持单 Uvicorn 进程；不能用多个 worker 绕过限额。由维护者审核并执行构建/部署，本次开发没有启动或部署容器：

```bash
docker compose -f compose.yaml -f compose.browser.yaml build web
```

`deploy/chromium-seccomp.json` 来自 Microsoft Playwright 官方仓库 `utils/docker/seccomp_profile.json`；获取时 SHA256 为 `cc3e61cabda6bbc1e53e54d27ba4d55a9d3be829b6dd1a596f4a7b31b1cc7849`。保留默认拒绝策略，按官方建议允许命名空间相关调用。不使用 `seccomp=unconfined`、privileged 或关闭 Chromium 沙箱来掩盖启动失败。

## 验证与实际边界

官方浏览器身份验证成功与后续 HTTP 采集链路是否被平台接纳是两件事。平台可能按服务器出口、浏览器及会话限制请求；完整 Cookie 不保证跨环境访问。当前交题仍在官方提交页由用户本人完成，并未实现服务器自动代交。

实际平台验证记录与未完成项见 [本轮验收](acceptance/2026-10-21-platform-login/VALIDATION.md)。模拟测试只检查流程和安全边界，不代表四个平台都已完成真实账号登录。旧 `POST /api/platform-login/password` 请求表单尝试仍保留兼容，前端主入口已改为官方浏览器窗口。

## 隔离与回收

每用户一条、每进程全局最多一条活跃浏览器流程。每次新建临时上下文，不复用用户浏览器 profile。所有截图、输入、状态、确认和取消请求均核对 ACM 用户及登录会话；截图与输入不缓存，不进入 AI。固定平台、HTTPS、域名白名单、逐跳检查与有限输入词汇限制浏览器用途。账号修订号与会话撤销检查防止旧流程覆盖新配置。

Linux Chromium 的 XDG 配置 / Crashpad 与浏览器 user-data-dir 不完全相同（参见 [Chromium 官方说明](https://chromium.googlesource.com/experimental/chromium/src/+/HEAD/docs/linux/debugging_minidump.md)）。每条流程为浏览器子进程分配独立0700临时配置与缓存目录，进程确认停止后清除，服务自身 HOME 不变；只读容器也无需写入用户主目录。

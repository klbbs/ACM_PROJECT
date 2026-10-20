# 平台 Cookie 助手与更新公告（0.14.0）

在网站登录后打开「连接与设置」→「下载 Cookie 插件与绑定教程」。该页同时包含账号配置、检测登录状态和 F12 手动教程，工作台的教程入口也会跳到此处。

## 安装

- Chrome 120+ / Edge：下载 Chromium ZIP 并解压，打开 `chrome://extensions` 或 `edge://extensions`，开启开发者模式，加载包含 manifest.json 的已解压文件夹，固定工具栏图标。
- Firefox 128+：下载 Firefox ZIP 并解压，打开 `about:debugging#/runtime/this-firefox`，临时载入 manifest.json。当前没有 Mozilla 签名；正常版 Firefox 不能永久安装此未签名包，重启后需重新载入。没有要求关闭浏览器签名或安全检查。
- 如需面向普通用户永久分发，维护者应将 Firefox 包送 Mozilla 签名（可不公开上架），Chrome/Edge 也可以走各自扩展商店；此版本未代为申请开发者账号、签名或发布。

Mozilla 官方说明：[临时安装](https://extensionworkshop.com/documentation/develop/temporary-installation-in-firefox/)、[签名与分发](https://extensionworkshop.com/documentation/publish/signing-and-distribution-overview/)。

## 获取与绑定

1. 在同一个浏览器登录平台，完成正常验证。支持 HTTPS：`codeforces.com`、`www.luogu.com.cn`、`www.nowcoder.com`、`ac.nowcoder.com`、`atcoder.jp`。牛客用 `ac.nowcoder.com` 算法页面。
2. 在该平台页面点击插件图标，再点击「获取 Cookie 并复制」。首次仅请求当前域名授权；没有获得授权则不读取。
3. 回到 ACM Lab 的「算法平台连接」，选择平台、填写 handle 或 UID，将剪贴板内容粘贴到 Cookie 输入框并保存；已绑定账号先点编辑。
4. 点击该账号「检测登录状态」。复制成功不代表服务器能使用 Cookie。平台可能限制服务器 IP，且某些 Cookie 绑定浏览器环境；不绕过反爬验证，不以公开页面可读作为登录证据。
5. 复制失败时弹窗提供临时文本框与重试按钮；关闭即清除弹窗内容。使用后自行覆盖系统剪贴板。

## 权限与数据流

- Manifest V3，无远程依赖、后台脚本、内容脚本、外部消息入口或网络上传，CSP `connect-src 'none'`。
- 固定允许以上五个官方域名，host 权限安装时不授予，用户主动点击时按当前域名请求；`activeTab` 只识别当前页面，`cookies` 可读取该域名权限内的 HttpOnly Cookie，`clipboardWrite` 写入剪贴板。
- 使用浏览器 Cookie API，而非 `document.cookie`，包括 HttpOnly。只读，不修改 Cookie。只从当前标签页的 Cookie store / Firefox 容器读取，获取权限过程中页面变化则终止。
- 复制当前页面 URL 实际适用的 Cookie，保留同名 Cookie 的路径顺序；不把 Domain/Path/Expires 属性混入请求值。第三方分区 Cookie 无法在独立服务器复用，排除并提示。
- 只在弹窗内存中构建标头；成功复制后不保留文本。Cookie 没有进入插件配置、日志、服务器下载包或 AI 提示词。
- 使用 `www`、子域或非默认端口的其他变体将不识别，避免把凭据从未经审查的域名导出。可使用 F12 备选流程。
- Cookie 格式检查与本站 16 KiB 上限对齐。Cookie 本身等同登录凭据，仅提供给你信任的 ACM Lab HTTPS 站点。

Chrome 官方 API：[cookies](https://developer.chrome.com/docs/extensions/reference/api/cookies)。

## 构建与下载

源文件在 `src/acmweb/browser_extension/`，由后端 `extension.py` 使用固定文件列表构建确定性 ZIP，没有用户内容或凭据。已登录用户可下载：

- `GET /api/browser-extension/chromium.zip`
- `GET /api/browser-extension/firefox.zip`

setuptools package-data 将资源放入 wheel；Docker 和源码部署均可使用，服务器无需安装浏览器、Node 或外部压缩工具来生成下载包。

## 发布公告维护

每次发布，在 `src/acmweb/announcements.py` 的 `RELEASES` 最前面添加一个新版本（版本 ID 不重复），编写用户能理解的变动，并同步 CHANGELOG 与版本号。发布后用户下次登录/刷新即可获得新公告；已读旧版本不会抑制新版本。

- 登录态 GET `/api/announcements`：历史内容和当前用户未读 ID。
- 带 CSRF POST `/api/announcements/read`：`{"ids":["0.14.0"]}`，只允许已存在版本；事务合并，避免多设备并发覆盖。
- 已读数据在用户独立 SQLite 的 `web_settings`，不依赖浏览器本地存储；不自动标记已读，只有用户点击后成功保存才消失。
- 页面「更新公告」保留完整历史，可反复查看。当前首次使用会提示收录的 0.13.0、0.13.1 与 0.14.0。

本功能不改变官方交题流程：仍需用户在官方站点提交，本站关联与同步结果。

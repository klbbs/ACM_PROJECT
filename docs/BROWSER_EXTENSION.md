# 平台 Cookie 油猴助手与更新公告（0.15.0）

在网站登录后打开「连接与设置」的 Cookie 助手与平台绑定教程。当前推荐油猴脚本，不再要求用户加载未签名 Firefox 扩展或每次重启临时安装。

## 安装与使用

1. 从 [Tampermonkey 官方网站](https://www.tampermonkey.net/) 为当前浏览器安装 Tampermonkey 5.3+。
2. 在 ACM Lab 教程中复制脚本源码；打开油猴管理面板，新建脚本，替换默认示例后保存。也可下载 `.user.js` 后在油猴中导入。主推复制源码，避免脚本管理器跨上下文获取时丢失 ACM Lab 登录态。
3. 正常登录算法平台、完成平台验证，在官方 HTTPS 页面点击 ACM Lab Cookie 助手或油猴菜单命令。支持 `codeforces.com`、`www.luogu.com.cn`、`www.nowcoder.com`、`ac.nowcoder.com`、`atcoder.jp`。
4. 复制成功后，回到「算法平台连接」选择平台、填写本人的 handle / UID，粘贴 Cookie，保存并检测。牛客建议在 `ac.nowcoder.com` 算法页面操作。
5. 若脚本提示 Cookie API 不支持或不能确认读取了 HttpOnly Cookie，请使用本页的 F12 备选流程：Network → 刷新官方页面 → Document 请求 → Request Headers → Cookie 完整值。不要用 `document.cookie` 替代完整请求 Cookie。

### HttpOnly 限制

脚本仅支持普通默认会话；Firefox 容器、隐私窗口和首方隔离请在原平台标签页用 F12 获取完整 Cookie，以免导出错误会话。

油猴脚本需要脚本管理器提供的 `GM_cookie` / `GM.cookie` API。Tampermonkey 官方文档对 HttpOnly 读取有版本和环境限制，不能保证所有稳定版浏览器及脚本管理器均支持。没有可靠能力时脚本明确失败并引导 F12，不输出看似成功的残缺 Cookie，也不要求关闭浏览器安全机制。详见 [官方 Cookie API 文档](https://www.tampermonkey.net/documentation.php#api:GM_cookie)。

脚本只在用户主动操作后读取当前固定官方域名的 Cookie，不自动上传、不持久保存、不更改 Cookie、不访问其他网站、不调用 AI。剪贴板内容仍是登录凭据，只粘贴到你信任的 ACM Lab HTTPS 站点，用完自行覆盖。

## 验证码与访问限制

遇到验证码或访问限制，平台连接卡片会给出原因、官方处理入口和下一步。先在当前浏览器进入官方页面，完成登录或验证，重新获取 Cookie 并保存，再执行「重新检测」。主动重新检测不会把 60 秒内的旧结果冒充新结果，仍受防刷间隔限制。

官方浏览器通过验证不保证服务器 IP 也能访问。洛谷 HTTP 418 等限制不能直接判为 Cookie 过期。本站不把第三方挑战页原始 HTML、验证码会话或 CSRF 放到自己域名执行，也不声称能将服务器验证码原样搬到用户浏览器完成。持续受限时，应在官方页面完成读题/交题，本站可保留公开资料采集和手动导入题面流程。详见 [检测说明](CREDENTIAL_CHECK.md)。

## 分发与构建

- `GET /api/browser-userscript`：认证 JSON，包括脚本源码、版本和文件名，前端通过已有会话读取并复制。
- `GET /api/browser-userscript/acm-lab-cookie-helper.user.js`：认证脚本下载，不包含用户凭据，不将会话 token 嵌入脚本。
- 源文件和 package-data 随源码、wheel、Docker 及发布包一起提供；服务器无需浏览器来分发。
- 原 `/api/browser-extension/{chromium|firefox}.zip` 仅保留旧客户端兼容，网页不再主推或要求安装未签名扩展。

## 发布公告维护

每次发布，在 `src/acmweb/announcements.py` 的 `RELEASES` 最前面添加新版本（版本 ID 不重复），同步 CHANGELOG 与版本号。用户下次登录/刷新可看到新公告；已读状态保存在每用户数据库，旧公告继续保留。

登录态 GET `/api/announcements` 获取历史与未读 ID；带 CSRF POST `/api/announcements/read` 合并已读。只有用户点击并保存成功才标记已读。本功能不改变交题方式：仍由用户前往官方提交，本站关联与同步结果。

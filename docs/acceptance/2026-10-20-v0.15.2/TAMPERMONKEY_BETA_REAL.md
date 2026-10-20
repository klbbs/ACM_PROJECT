# 官方 Tampermonkey Beta 实机验收

日期：2026-10-20。本次只做本地开发验收，不部署、不真实交题。

## 已实际运行的环境

- 系统 Firefox 157.0.1，geckodriver 0.37.1，headless。
- 官方 `https://www.tampermonkey.net/xpi/firefox-current-beta.xpi`：Tampermonkey Beta 5.6.6239，扩展 ID `firefoxbeta@tampermonkey.net`。
- 隔离临时 Firefox profile；不打开、不修改、不重启用户日常 Firefox。
- 通过真实 Tampermonkey 管理面板「Create a new script...」导入项目原样助手 1.0.1，通过编辑器 Save 保存。未替换或模拟任何 GM API。
- 权限路径为管理面板 **Settings → Config mode: Advanced → Security → Allow scripts to access cookies: All**。该临时 Beta 环境读到的默认值就是 **All**；另两个选项为 **All but protected cookies (HttpOnly)** 和 **Disabled**。此结果不保证稳定版或其他浏览器同样支持。

## 实际验收结果

在真实 AtCoder 公开首页进行匿名访问，向临时浏览器会话加入一个仅供实验的 synthetic HttpOnly Cookie。它不是平台登录凭据，不能登录任何账号。

1. 浏览器 `document.cookie` 无法读到这个 synthetic HttpOnly Cookie。
2. 用户动作对应的真实助手按钮通过 WebDriver 点击；脚本由真实 Tampermonkey Beta 运行。
3. 助手显示「已复制 6 项（包含 HttpOnly；来源：油猴 API）」，诊断显示真实管理器 5.6.6239，返回 6 项、适用 6 项、各过滤计数 0。
4. 使用隔离 Firefox 的真实剪贴板服务只检查布尔结果：synthetic HttpOnly 键值确实存在于复制结果；不输出任何 Cookie 值。
5. 清空实验剪贴板，退出隔离浏览器并清理临时 profile。

此结果证明真实管理器的 Cookie API → 助手过滤与序列化 → 真实剪贴板整条通路可工作。它不是 CF、AtCoder 或其他平台的登录成功证明。

CF 的真实用户会话由另一 agent 只读检测：目前拿到的各浏览器 store 对应请求均重定向到登录页，没有当前账号身份确认。本实验没有再次打开 CF 验证页面、没有自动处理验证码、没有真实提交。

## 可复现的显式手工命令

```bash
.venv/bin/python scripts/verify_tampermonkey_beta.py \
  --run-real-browser \
  --output docs/acceptance/2026-10-20-v0.15.2
```

上述完整命令已在本机实际重新执行并以 exit 0 结束，输出 `synthetic_httponly_in_real_clipboard=true`；对应脱敏结果为本目录 `tampermonkey-beta-real.json`，截图为 `tampermonkey-beta-real-httponly.png`。官方入口实际重定向到同属官方的精确域名 `firefox.tampermonkey.net`；脚本只允许这两个已核实的官方域名，其他来源拒绝。

该完整命令已实际执行并exit0，脱敏结果保存在同目录`tampermonkey-beta-real.json`。

脚本默认不执行；必须显式传 `--run-real-browser`。它下载官方 Beta，启动新建临时 profile，真实安装并操作管理器，在真实匿名 AtCoder 页加入 synthetic HttpOnly Cookie并验收，只输出版本、计数和布尔结果。不会读取 account.toml、用户日常 profile、或真实 Cookie，不会交题。结束时关闭自建浏览器、清空实验剪贴板和清理临时目录。它不是普通 CI 测试，依赖本机 Firefox/geckodriver、上游 Beta UI、官网可达性。

官方说明：[Cookie API](https://www.tampermonkey.net/documentation.php?q=GM_cookie)、[Beta 下载](https://www.tampermonkey.net/xpi/firefox-current-beta.xpi)、[Firefox 变更](https://www.tampermonkey.net/changelog.php?locale=en&more=true&show=firb)。

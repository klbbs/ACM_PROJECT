# 牛客真实油猴 Beta 与已授权会话验收（2026-10-21）

本次验收使用主代理已确认登录的本人牛客会话，迁入全新临时 Firefox profile，真实安装官方 Tampermonkey Beta 和仓库 helper 1.0.2，真实点击助手复制按钮，再由服务端 `CredentialChecks` 核对官方当前身份。没有申请二维码、没有新微信扫码、没有提交代码；不能将本结果写成牛客扫码登录流程已验收。

## 环境与结果

| 项目 | 实测 |
| --- | --- |
| Firefox | 157.0.1，独立临时 profile |
| Tampermonkey Beta | 5.6.6239 |
| 助手 | 1.0.2，仓库原始源码安装进真实管理器 |
| Beta 来源 | 官方 `https://www.tampermonkey.net/xpi/firefox-current-beta.xpi`，最终来源仍为官方 HTTPS 域名；XPI 包含签名文件，实际安装的扩展 ID 与官方 Beta 一致 |
| Cookie 权限 | 仅切换高级配置视图读取；默认值已是 All，没有修改此权限 |
| 导入既有会话 | 15 项，其中 1 项 HttpOnly |
| 点击前、点击后当前页面适用集合 | 均为 15 项，集合稳定；其中均为 1 项 HttpOnly |
| 助手真实复制结果 | 15 项，来源为油猴 API，无“可能不完整”提示 |
| 剪贴板与当前完整适用集合 | 名称与值的多重集合完全相等；全部 HttpOnly 项均包含，仅在内存比较，无明细输出 |
| 服务端实际检查 | HTTP200，`status=authenticated`，`login_verified=true`，官方当前 UID 与预期本人 UID 匹配 |
| 新扫码 / 交题 | 均未进行 |

当前页面为牛客 ACM 官方 HTTPS 根页面。完整适用集合来自真实 Firefox WebDriver 的当前页面 Cookie 接口，与助手实际写入系统剪贴板的内容比较；没有用合成 Cookie 代替真实会话，也没有用公开个人主页存在或公开数据可采集来推断登录。服务端检查使用本次复制结果，在内存构造隔离账号对象，调用产品 `CredentialChecks.check(..., force_refresh=True)`，读取固定官方地址并检查当前身份。

## 隔离与清理

授权源只读取 Codex 浏览器 Cookie 数据库中精确限定的三种牛客域元数据，不读取其他站点的 Cookie。数据库及 WAL 先复制至权限 0700 的独立临时目录，复制文件权限 0600；仅查询 `application=chromium` 精确匹配的密钥环项，按现有加密格式解密并校验每项域名摘要。原浏览器数据库、日常 Firefox profile 和产品账号配置均未修改。

临时目录、Firefox profile 及脚本私密文件权限由 077 umask 限定；浏览器与 geckodriver 使用本次独立进程。测试结束已清空实验剪贴板、结束本次创建的浏览器/驱动、删除临时 profile、数据库副本、WAL 与临时脚本。没有保存 Cookie 名称、值、原始官方 HTML、密码或密钥。

仅保留无秘密的[计数与布尔结果 JSON](nowcoder-beta-existing-session.json)，以及只裁取助手卡片的[真实截图](nowcoder-beta-existing-session-helper.png)。截图已目视检查，不含个人凭据或当前账户信息。

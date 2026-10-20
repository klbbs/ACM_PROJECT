# CF 真实会话只读检查（2026-10-20）

用户明确授权读取本机 Firefox CF Cookie 并测试，不在聊天中传输凭据。

- 用运行中 Firefox 进程持有的 Cookie 数据库文件路径核对实际 Snap profile，未重启或修改日常浏览器。
- 数据库含 WAL，复制到权限受限临时目录后只读查询 CF 域，临时目录自动清理；未记录 Cookie 名称列表、值、配置原文或其他网站会话。
- 普通默认 store 导出六枚适用于 CF 设置页的 Cookie；非默认 store 单独导出一枚。不同 store 未混合。
- 两组实际只读请求均使用项目 CredentialChecks，固定 HTTPS 设置页、不跟随重定向。CF 返回 HTTP302 登录跳转，状态 expired/not_logged_in，login_verified=false。
- 所有存储均未发现非空的常见 JSESSIONID 字段；该事实不能代替身份检测，也不能证明浏览器当前已退出。
- 未得到匹配的官方当前身份，未将私有 account.toml 覆盖成未经验证的新配置，未提交真实代码，未宣称真实 CF 登录已通过。
- 已有配置测试与本次 Firefox 导出测试均失败；需要当前实际已登录会话才能继续真实身份验收，不能用合成响应冒充成功。

本记录不包含账号、凭据或浏览器页面内容。未部署、未连接服务器。

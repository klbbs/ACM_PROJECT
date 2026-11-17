# QQ / ACM 客户端协作

- 此目录是 `ACM_PROJECT` 共同仓库的一部分；后续直接在这里维护，不回写旧独立 QQ 仓库形成第二份活跃源码。
- 遵守根目录 `AGENTS.md` 和 `docs/QQ_INTEGRATION.md`。服务器由负责部署的 agent 管理，客户端不要并发改服务器部署状态。
- 保留账号占位符和本机私有数据边界，不提交 `.local-settings.json`、server/session 配置、密码、Cookie、Key、聊天/作答数据库、SSH 私钥。
- ACM 客户端配置只从 `src/acm/acm_connection.py` 维护，由部署工具安装到两个运行目录。旧站不得作为隐式备用线路，Cookie 必须与新站 HTTPS origin 一致。
- 修改 QQ 后运行本目录的 `scripts/manage.py check`，只做本次影响范围的开发检查；没有用户验收指令时不标新版本。
- 改收费抽题与回执前阅读根目录交付协议。结果不明时恢复同一请求，不自动新建收费请求；旧站请求不能在新站重放。

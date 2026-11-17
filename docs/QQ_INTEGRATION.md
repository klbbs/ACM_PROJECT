# QQ 与 ACM Lab 共同维护

用户于 2026-11-17 要求将原 QQ 项目与 ACM Lab 合并。共同仓库为 `klbbs/ACM_PROJECT`，QQ 源码位于 `integrations/qq_bot/`。原 `klbbs/qq_bot` 的提交历史通过 Git subtree 合并保留，不删除旧仓库，不维护两份独立的功能分支。

## 分工和更新

- ACM 服务、接口与网站仍在根目录维护；QQ/Hermes 客户端在 `integrations/qq_bot/` 维护。
- 修改交付协议时同时检查 `docs/HERMES_DELIVERY_PROTOCOL.md` 和客户端接收/去重/ACK 逻辑，不能仅修改一边并宣称联调成功。
- QQ 操作仍受管理员账号约束，普通私聊/群聊不会因为仓库合并获得电脑控制权限。
- 本机 QQ 的运行数据、登录态和作答记录继续保留在原私有目录。目录合并不搬迁或清空用户数据库。
- 跨 agent 开发使用独立分支/worktree；服务器部署与客户端改动分开负责，不回退其他 agent 的提交。

本机更新 QQ：

```bash
cd integrations/qq_bot
~/.local/share/qq-hermes/.venv/bin/python scripts/manage.py check
~/.local/share/qq-hermes/.venv/bin/python scripts/manage.py deploy --apply
```

## 服务器迁移

旧服务器已过期，不再作为默认地址或备用地址。所有 QQ 刷题与 `hermes acm` 查询统一读取本机 `~/.local/share/hermes-acm/server.json`。没有配置、状态为 pending、配置无效时均不发网络请求；QQ 的其他聊天功能继续运行。

新站部署 agent 将部署结果写入本机 `docs/QQ_SERVER_HANDOFF.local.json`（已加入 Git 本地排除项），提供非敏感的 origin、ready、tls_ready 和说明。该文件只是交接信息，不是认证凭据，也不能单凭其声明绕过真实健康检查。

客户端切换步骤：

```bash
# 指定新站实际已配置的 HTTPS origin；先停用旧站，不自动联网
~/.local/share/qq-hermes/.venv/bin/python scripts/server.py prepare --origin https://new-acm.example.com

# 待服务器 agent 确认部署完成后：验证 HTTPS、/api/health 和 /api/auth/status
# 检查只读，不发送 Cookie、不抽题、不产生 AI 费用
~/.local/share/qq-hermes/.venv/bin/python scripts/server.py activate

# 新站重新登录，密码只在本机终端输入
hermes acm login
```

`activate` 不允许 HTTP、忽略证书错误或跟随重定向。新站只有 HTTP 时应先由部署 agent 完成 TLS 或受信任的 HTTPS 入口，再激活客户端。激活后仍需建立新站登录会话；旧 Cookie 没有绑定 origin 或属于旧站时不会发送到新站。

历史题目、回执收件箱、历史请求原样保留并按 origin 隔离。旧站已存在的付费抽题编号不能在新站自动重放；必要时用户明确结束旧轮次后新开 `/roll`。旧任务不能通过修改地址伪装成新站任务已恢复。历史去重数据的跨站迁移应单独核对工作区身份和服务器备份，不能把其他账号的数据混进来。

QQ 发送保护继续生效，服务器回执的含义仍是本机持久化成功，不代表 QQ 用户已阅读。开发检查与部署健康检查不等于用户的版本验收，本次不标新版本或发布标签。

# QQ 与 ACM Lab 共同维护

用户于 2026-11-17 要求将原 QQ 项目与 ACM Lab 合并。共同仓库为 `klbbs/ACM_PROJECT`，QQ 源码位于 `integrations/qq_bot/`。原 `klbbs/qq_bot` 的提交历史通过 Git subtree 合并保留，不删除旧仓库，不维护两份独立的功能分支。

## 分工和更新

- ACM 服务、接口与网站仍在根目录维护；QQ/Hermes 客户端在 `integrations/qq_bot/` 维护。
- 修改交付协议时同时检查 `docs/HERMES_DELIVERY_PROTOCOL.md` 和客户端接收/去重/ACK 逻辑，不能仅修改一边并宣称联调成功。
- QQ 操作仍受管理员账号约束，普通私聊/群聊不会因为仓库合并获得电脑控制权限。
- 生产 BOT 在服务器的独立服务账户中运行。电脑上的原运行目录保留作回退，本机服务及自启保持关闭。
- 跨 agent 开发使用独立分支/worktree；服务器部署与客户端改动分开负责，不回退其他 agent 的提交。

本机检查准备推送的 QQ 源码：

```bash
cd integrations/qq_bot
~/.local/share/qq-hermes/.venv/bin/python scripts/manage.py check
```


## 服务器运行与备份

生产实例使用 `/srv/hermesbot`，由 `hermesbot-gateway`、`hermesbot-nonebot` 和 `hermesbot-napcat` 三个系统服务开机启动。开发在本机共同仓库完成，检查通过后推送 `main`。服务器保留对应源码版本；正在运行的 BOT 更新须检查当前任务，并通过 SSH 只部署需要变动的组件。`manage.py deploy --apply` 是旧的本机模式，不能用于生产实例。

BOT 管理端口 6099、8642、18800 仅监听服务器本地地址。NapCat 管理可通过 SSH 将本机 16099 转发到服务器的 127.0.0.1:6099。所有 BOT 服务直连，VPN 仅供 ACM 的官方 ChatGPT/Codex 子进程使用。

把备份脚本从已审查的仓库版本安装到服务器 `/usr/local/lib/hermesbot/backup.py`，以 BOT 账户执行：

```bash
sudo -u hermesbot -H python3 /usr/local/lib/hermesbot/backup.py pack config
sudo -u hermesbot -H python3 /usr/local/lib/hermesbot/backup.py pack memory
sudo -u hermesbot -H python3 /usr/local/lib/hermesbot/backup.py pack all
```

`config` 包含配置、插件、人设相关设置和现有通道凭据。`memory` 包含 Hermes 会话、记忆、角色卡、QQ 会话映射和消息、图片缓存、ACM 作答记录和结果回执。`all` 合并两部分。程序通过 SQLite 在线备份接口保存每个数据库的完整快照。

打包后会校验文件清单和 SHA-256，私有压缩包位于 `/srv/hermesbot/.local/state/hermesbot/backups/`。其中可能包含密钥、Cookie 和聊天内容，请通过 SSH 私下转存。GitHub 只保存源码和配置示例。用 `backup.py verify /path/to/archive.tar.gz` 校验备份。恢复时先解包到私有暂存目录，核对目标和账号，再选择合并或替换；在替换在线数据库前停止对应服务并保留现有数据。QQ 客户端原生登录缓存不在包内，换机后可能需要扫码。

## 更换 ACM 服务器

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

## 管理员服务器操作

管理员 QQ 私聊可用 `/server` 查看指令。状态、内存、磁盘和身份查询以
`kendas` 执行，也可以直接用自然语言查询。需要修改服务状态时，在管理员私聊
输入 `/server restart-gateway` 或 `/server reload-nginx`，检查 Nginx 配置用
`/server check-nginx`。这些固定动作由服务器的 `hermesbot-admin.service`
执行，日志记录操作和结果。网关或桥接进程均保留原账户和隔离设置。
管理服务通过本机 Unix 套接字识别实际调用进程，不接收任意命令或参数。

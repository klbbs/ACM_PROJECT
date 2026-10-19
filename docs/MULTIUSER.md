# 多人版部署、权限与运维

## 首次部署

本版本面向受邀的小型训练团队，默认最多 100 名用户、单个后端进程。Docker Compose 默认启用 `ACM_MULTI_USER=true`。本机未启用多人模式时，原有个人 CLI 和网页仍可使用；请勿将旧版共享访问令牌模式当作多人隔离。

```bash
python3 scripts/bootstrap.py
```

编辑服务器 `.env`，填入自己的站点域名：

```dotenv
ACM_MULTI_USER=true
ACM_ALLOWED_HOSTS=acm.example.com,localhost,127.0.0.1,[::1]
ACM_PUBLIC_ORIGIN=https://acm.example.com
ACM_AI_ALLOWED_HOSTS=api.openai.com
```

保留 bootstrap 生成的随机 `ACM_ACCESS_TOKEN`，不要使用 `.env.example` 中的占位符。使用 Caddy 示例配置 HTTPS，然后启动：

```bash
docker compose up -d --build
```

首次打开网页，使用部署令牌创建首位管理员；初始化页面预填用户名和密码 `kds`。该初始密码较弱，首次登录后请立即在账号设置中修改。密码使用 scrypt 哈希保存。初始化仅能成功一次；部署令牌此后不能登录任何用户账号。管理员进入「管理后台」生成一次性邀请码、审核题面译文并管理成员。邀请 24 小时有效、注册成功即消耗；不开放匿名自由注册。

## 管理后台

管理员登录后，左侧导航会显示「管理后台」。后台分为译文审核、成员管理和安全审计：译文审核可按待审核、已通过、已拒绝、已撤下筛选，并同时查看原题与译文；管理员可批准、拒绝或填写原因撤下已共享译文；成员管理支持生成邀请码、启用或停用成员和调整角色；审计区显示最近的登录、权限、译文审核和共享内容操作。接口仍由服务端实时校验管理员角色，隐藏导航不构成权限控制。

在实际站点域名下使用 HTTPS。`ACM_PUBLIC_ORIGIN` 固定浏览器请求的站点来源，避免 HTTPS 反向代理到内部 HTTP 时误判。`ACM_ALLOWED_HOSTS` 只写主机名，不含协议或端口。`ACM_TRUSTED_PROXIES` 仅加入实际反向代理的 IP，用于获取客户端 IP、按来源限流，不能填 `*`；Caddy 在容器网络或 Docker 网关后时需按实际网络调整。未经信任的 X-Forwarded-For 不应作为客户端身份。

原生部署可通过 systemd 加载 `.env`，或为服务进程设置相同环境变量后运行 `acm-web --multi-user`。服务必须具备可写的 `ACM_DATA_DIR` 和 `/tmp`；容器根文件系统为只读。建议至少 2 GiB 内存，密码哈希和官方账号进程会占用内存。

## 数据与权限

| 数据 / 操作 | 自己 | 其他成员 | 管理员 |
| --- | --- | --- | --- |
| 平台 Cookie、API Key、官方授权 | 设置和使用，不回传秘密 | 无权 | 无跨用户网页接口 |
| 提交、分析报告、题库筛选、训练偏好和定时任务 | 读写 | 无权 | 无跨用户网页接口 |
| 私人资料草稿 | 读写、移除和恢复 | 不可见 | 他人的私人草稿不可见 |
| 发布的共享资料 | 作者可编辑、移除和恢复 | 可读 | 可读、移除和恢复 |
| 用户列表、角色、停用、邀请和审计 | 普通成员不可用 | 不可用 | 可管理；不能停用自己或修改自己角色 |

共享范围为本站全部已登录成员，并非公开互联网，也没有团队子分组。共享资料按纯文本呈现，不执行 HTML。报告分享只发布用户主动选择的标题和摘要快照，不共享原始提交、Cookie、训练数据库或 AI 凭据。后续报告修改不会自动修改既有快照；需要时作者可撤回共享。编辑需要当前版本号，冲突返回 409；移除采用回收站，作者和具有相应共享管理权限的管理员可恢复。

站点管理员通过网页不能读取他人私人数据；拥有宿主机磁盘或 root 权限的运维人员仍能访问服务器文件。此版本没有承诺磁盘加密、端到端加密或独立容器级用户隔离。

目录结构：

```text
data/
  users.sqlite3              用户、哈希密码、哈希会话、邀请、共享资料及审计
  users/<服务生成的用户ID>/
    accounts.sqlite3         此用户的采集与训练数据库
    accounts.web.toml        此用户的账号与 Cookie（600）
    ai-secrets.json          此用户的 API Key（600）
    codex/                   此用户的官方授权（目录 700）
    runs/                    此用户的采集导出
```

用户 ID 由服务器生成，客户端不能指定数据目录。成员不会继承根目录 `account.toml`、旧数据库、宿主机 Codex 登录或 `OPENAI_API_KEY`。AI 提供方须先由运维加入 `ACM_AI_ALLOWED_HOSTS`；成员不能通过自定义 API URL 访问任意内网或元数据地址。

## 官方 ChatGPT 登录

保留 API Key 和设备码两种方式。需要官方登录时，在 `.env` 设置：

```dotenv
ACM_ENABLE_SERVER_CODEX=true
ACM_IMAGE_TARGET=server-codex
```

重建容器。每位成员在自己的设置页申请设备码，在自己的浏览器完成官方授权，模型调用只使用该成员的凭据。未登录不会借用管理员的官方账号。本站最多同时保持 4 个官方客户端进程，空闲 5 分钟后关闭进程但保留官方客户端保存的授权。模型与额度以实际官方调用结果为准。

## 身份和网站防护

- 密码使用随机盐和 scrypt（N=131072、r=8、p=1），数据库不保存明文密码。密码哈希并发最多 2 个。
- 每次登录生成新的随机会话，数据库仅存 SHA-256 摘要；最多保留 5 个设备会话。绝对有效期 24 小时，连续空闲 2 小时失效。
- Cookie 为 HttpOnly、SameSite=Strict，生产环境增加 Secure；不将登录令牌写入 localStorage。
- 修改密码会撤销全部会话，用户可退出全部设备；停用或修改角色会撤销该用户会话。密码找回通过下方离线命令处理，不提供无验证的网页重置。
- 写操作校验会话 CSRF Token、自定义请求头、Origin 和 Fetch Metadata；不开放通配 CORS。
- 认证按客户端 IP、用户名与全站限流，普通接口和高成本任务单独限流。限流记录保存在数据库，重启不会清空当前窗口。
- JSON 请求最大 512 KiB，读取请求体最多 15 秒；限制服务连接数、每用户任务数（2）和全站任务数（4）。
- 限定 Host，设置 CSP、反嵌入、禁止类型嗅探、来源策略、权限策略；生产启用 HSTS。敏感接口不缓存，错误不回显提交的密码或 Cookie。
- 关键用户管理、登录、资料写操作有审计。部署包与 Git 排除数据库、授权和私人文件。

这些措施不是外部渗透测试或绝对安全保证。公网部署仍需补丁升级、HTTPS、主机防火墙、备份及边缘 DDoS/WAF 防护。当前无邮箱验证、MFA、SSO、组织子分组或分布式任务队列；不能通过增加 Uvicorn worker 扩容，同一目录有服务进程锁。

设计参考：[OWASP 密码存储](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)、[OWASP 会话管理](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)。

## 旧个人数据迁移

升级后旧文件保持原位，不会自动分给新用户或发布为共享资料。先创建管理员，再停止服务，备份完整数据目录，然后显式导入管理员自己的空空间：

```bash
docker compose stop web
docker compose run --rm --no-deps web python scripts/manage_users.py import-legacy YOUR_ADMIN
docker compose up -d
```

旧布局 `ACM_DATA_SUBDIR=account-history` 同样适用。原生部署停止服务后执行：

```bash
.venv/bin/python scripts/manage_users.py --data-dir data --config account.toml import-legacy YOUR_ADMIN
```

使用 SQLite backup 快照导入；原始数据库和配置不变，目标已有数据或授权时拒绝覆盖。旧定时任务在目标空间默认关闭，防止重复采集；官方授权不迁移，需要管理员重新登录。不得将一个人的历史批量导给其他成员。

## 密码恢复与备份

运维人员停止服务后，通过交互提示设置新密码，密码不放在命令参数或日志中：

```bash
docker compose stop web
docker compose run --rm --no-deps web python scripts/manage_users.py reset-password USERNAME
docker compose up -d
```

`list` 子命令显示用户名、角色和启用状态，不显示哈希或秘密。恢复密码会撤销该用户全部会话，记录离线审计，不会重新启用被停用的用户。

最简单的一致备份是停止服务，备份 `.env`、`account.toml` 及完整 `data` 挂载（含 users.sqlite3、WAL 和各用户目录）。备份包含密钥和私人信息，应加密保存、限制读取权限，并在隔离环境验证恢复。不要只复制运行中的 SQLite 主文件而遗漏 WAL，也不要将用户目录作为静态网站发布。

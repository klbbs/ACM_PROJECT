# 部署与运维

多人服务器的新部署、升级与密码恢复请优先阅读 [多人版说明](MULTIUSER.md)。下文同时包含保留的个人模式部署选项。

## 从发布包启动

解压 ZIP/TAR.GZ，进入目录：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -c requirements.lock .
python3 scripts/bootstrap.py
```

运行后可在网页添加平台账号，也可手动编辑 `account.toml`。运行 `.venv/bin/acm-web` 本机访问。发布包有 `frontend/dist`，无需 Node.js。

## 原生服务器

1. 将项目放入 `/opt/acm-lab`，创建专用运行用户。
2. 安装依赖并初始化，填写账号配置。
3. `.env`、`account.toml` 保持 `600`、由运行用户持有；`data` 可写。
4. 修改 `deploy/acm-lab.service.example` 的用户和路径，再安装到 systemd。
5. 使用 Caddy/Nginx 提供 HTTPS，保留 Host/协议头，代理到 `127.0.0.1:8000`。

当前使用单进程。多个 worker 会使内存任务队列和调度器不一致，服务进程锁会阻止重复实例；多实例扩容需另行引入共享任务队列。

对外绑定时，CLI 启用生产校验，要求至少 24 字符访问令牌；官方账号通道默认关闭，可显式启用服务器设备码模式。公共域名使用 Secure、HttpOnly、SameSite=Strict 会话 Cookie；外部访问须配置 HTTPS。回环地址可使用本机 HTTP。

## 环境变量

| 名称 | 含义 |
| --- | --- |
| `ACM_MULTI_USER` | Compose 默认 true，启用邀请注册与独立用户空间 |
| `ACM_ALLOWED_HOSTS` | 允许的网站域名，逗号分隔 |
| `ACM_PUBLIC_ORIGIN` | 浏览器访问的完整 HTTPS 站点来源，不含路径 |
| `ACM_AI_ALLOWED_HOSTS` | 成员允许连接的 AI API 域名；默认预设官方域名，显式配置优先，见 [AI 接入](AI_PROVIDERS.md#密钥与服务器) |
| `ACM_ENV` | 默认 `local`，服务器设 `production` |
| `ACM_ACCESS_TOKEN` | 工作台访问令牌，生产必填 |
| `ACM_ENABLE_CODEX` | 本机默认 true；生产使用下面的独立开关 |
| `ACM_ENABLE_SERVER_CODEX` | 生产默认 false；true 启用独立设备码授权 |
| `ACM_IMAGE_TARGET` | Compose 构建目标：runtime 或含官方 CLI 的 server-codex |
| `ACM_CODEX_BINARY` | 默认 codex，官方客户端路径 |
| `ACM_DATA_DIR` | 默认 data，使用其中的 accounts.sqlite3 |
| `ACM_DATA_SUBDIR` | Compose 挂载数据目录下的子目录，旧布局为 account-history |
| `ACM_ACCOUNTS_CONFIG` | 默认 account.toml |
| `ACM_STATIC_DIR` | 默认 frontend/dist |
| `OPENAI_API_KEY` | 仅个人模式使用；多人模式禁止继承服务端共用 Key |
| `ACM_UID` / `ACM_GID` | Compose 的宿主机运行用户 |

本机 CLI 不自动读取 `.env`；Compose/systemd 示例会加载它。

## 迁移与备份

从开发目录升级已有桌面采集项目时，先停止目标网页服务和采集任务，完成前端构建与打包，然后在开发目录执行：

```bash
.venv/bin/python scripts/upgrade_workspace.py /home/kendas/桌面/acm
cd /home/kendas/桌面/acm
.venv/bin/python -m pip install -c requirements.lock -e '.[dev]'
.venv/bin/acm-web
```

脚本要求目标已有 `account.toml` 和 `data/account-history/accounts.sqlite3`，源目录有 `data/accounts.sqlite3`。它复制 Git 跟踪文件及构建产物，将目标数据库和配置备份至 `data/account-history/backups/`，只导入缺失的网页数据，不导入源提交历史，也不覆盖已有设置。账号配置仅修改数据库与导出目录，Cookie 保持原值；学习资料保持原位。重复运行安全，但仍须先停止任务。

若本机安装 ROS，pytest 可能自动加载全局 ROS 插件导致缺少 yaml 等无关依赖，使用 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q` 可隔离这些插件。

数据库与采集 CLI 兼容。本机发现旧数据库 `data/account-history/accounts.sqlite3` 时会自动沿用该目录，也可将 `ACM_DATA_DIR` 显式设为 `data/account-history`，不要混入 `data/smoke`。`ACM_ACCOUNTS_CONFIG` 可指向旧账号配置。

运行中的 SQLite 应通过备份 API 备份，避免只复制主文件而漏掉 WAL：

```bash
.venv/bin/python - <<'PY'
import sqlite3
source = sqlite3.connect('data/accounts.sqlite3')
target = sqlite3.connect('backup.sqlite3')
source.backup(target)
target.close()
source.close()
PY
```

另行备份 `account.toml`、`.env`、`data/accounts.web.toml`、`data/ai-secrets.json` 和 `data/codex/`，这些不属于公开部署包，也不要加入 Git。

升级前备份数据库与本机配置、记录 Git tag。升级后保留原数据挂载；当前采用兼容式建表，不删除已有采集表。回滚时使用对应版本和升级前备份。发布包的 `BUILD_INFO.json` 记录源码提交，`MANIFEST.sha256` 可校验文件。

## 状态与定时任务

`GET /api/health` 返回版本。任务状态包含 queued/running/completed/partial/failed/interrupted。服务重启将未完成任务标记中断，用户可重试。

工作流与其他聊天自动化、系统 cron 独立，不会修改已有计划。避免同时开启两个重复采集/分析计划。

## 验证边界

已在本机执行后端测试、前端构建和浏览器交互。开发机未安装 Docker，不能宣称本机镜像构建通过；仓库 CI 配置了 docker build。真实 API Key 提供方仍需用自己的 Key 确认模型和计费权限。

## 无桌面服务器的账号配置

首次启动只需运行 bootstrap 生成访问令牌；用令牌解锁网页后添加账号，支持 CF / AtCoder 用户名、洛谷 / 牛客数字 UID，以及四平台个人主页 URL。服务仅解析已知平台 URL，不会访问用户输入的任意地址。Cookie 是可选项，公开数据可先不填；需要登录时，从自己的浏览器对应平台请求中复制 Cookie 值到密码输入框。

浏览器安全机制不允许本项目直接读取其他网站的 Cookie，平台也未统一提供第三方授权登录。无需在服务器安装 Firefox，也无需上传整个浏览器用户目录。Cookie 留空保留原会话，勾选清除可切回公开接口。移除账号配置不会删除历史；更换账号标识后，原标识的 Cookie 不会自动转移。

配置按 `ACM_DATA_DIR/accounts.web.toml` → `ACM_ACCOUNTS_CONFIG` 的顺序加载，网页文件权限为 600，原始 TOML 可保持只读挂载。网页配置包含完整账号列表，删除最后一个账号后也不会恢复原配置。采集运行期间禁止修改配置。保存成功只表示格式校验通过，实际会话有效性以同步任务结果为准。

## 官方 ChatGPT 设备码授权

Docker 部署在 `.env` 添加：

```dotenv
ACM_ENABLE_SERVER_CODEX=true
ACM_IMAGE_TARGET=server-codex
```

执行 `docker compose up -d --build`。基础 runtime 镜像保持 API Key 模式；server-codex 目标额外安装 Node.js 和官方 `@openai/codex`。可在构建时使用 `--build-arg CODEX_VERSION=<版本>` 固定经过验证的官方 CLI 版本，默认安装 npm 的 latest。

原生部署在受控环境安装官方 Codex CLI，设置 `ACM_ENABLE_SERVER_CODEX=true`，并为 systemd 的 `ACM_CODEX_BINARY` 提供绝对路径。之后在网页申请设备码，在自己的浏览器完成授权即可，服务器不需要桌面、不需要暴露 localhost OAuth 回调端口。设备码登录若未开启，请先进入 ChatGPT 安全设置 / 工作区权限开启。

授权存入 `ACM_DATA_DIR/users/<用户ID>/codex/`（多人模式）或 `ACM_DATA_DIR/codex/`（个人模式），目录权限 700，不借用宿主机官方登录。模型由官方目录读取；官方账号登录失败、过期或额度不足时，任务会报告失败，可重新登录或改用 API Key。设备码与验证链接只通过已解锁工作台返回，不写入任务日志。多人模式为每位成员分别保存登录；个人模式仍共享一个本机身份，不应用于多人服务器。

文档依据：[官方认证](https://learn.chatgpt.com/docs/auth)、[官方 App Server 协议](https://learn.chatgpt.com/docs/app-server)。回归测试验证登录协议与隔离目录，真实官方授权需由各成员在官方页面完成。Docker 镜像在仓库 CI 构建验证。

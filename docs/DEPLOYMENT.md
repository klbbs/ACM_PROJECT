# 部署与运维

## 从发布包启动

解压 ZIP/TAR.GZ，进入目录：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -c requirements.lock .
python3 scripts/bootstrap.py
```

编辑 `account.toml`，运行 `.venv/bin/acm-web` 本机访问。发布包有 `frontend/dist`，无需 Node.js。

## 原生服务器

1. 将项目放入 `/opt/acm-lab`，创建专用运行用户。
2. 安装依赖并初始化，填写账号配置。
3. `.env`、`account.toml` 保持 `600`、由运行用户持有；`data` 可写。
4. 修改 `deploy/acm-lab.service.example` 的用户和路径，再安装到 systemd。
5. 使用 Caddy/Nginx 提供 HTTPS，保留 Host/协议头，代理到 `127.0.0.1:8000`。

当前使用单进程。多个 worker 会使内存任务队列、登录和调度器不一致，不适用于当前个人工作台。多租户/多实例需另行引入独立任务队列、共享会话和账号隔离。

对外绑定时，CLI 启用生产校验，要求至少 24 字符访问令牌并关闭本机 Codex。公共域名使用 Secure、HttpOnly、SameSite=Strict 会话 Cookie；外部访问须配置 HTTPS。回环地址可使用本机 HTTP。

## 环境变量

| 名称 | 含义 |
| --- | --- |
| `ACM_ENV` | 默认 `local`，服务器设 `production` |
| `ACM_ACCESS_TOKEN` | 工作台访问令牌，生产必填 |
| `ACM_ENABLE_CODEX` | 本机默认 true，生产始终 false |
| `ACM_CODEX_BINARY` | 默认 codex，官方客户端路径 |
| `ACM_DATA_DIR` | 默认 data，使用其中的 accounts.sqlite3 |
| `ACM_DATA_SUBDIR` | Compose 挂载数据目录下的子目录，旧布局为 account-history |
| `ACM_ACCOUNTS_CONFIG` | 默认 account.toml |
| `ACM_STATIC_DIR` | 默认 frontend/dist |
| `OPENAI_API_KEY` | 可选服务端 Key，设置页保存的 Key 优先 |
| `ACM_UID` / `ACM_GID` | Compose 的宿主机运行用户 |

本机 CLI 不自动读取 `.env`；Compose/systemd 示例会加载它。

## 迁移与备份

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

另行备份 `account.toml`、`.env`、`data/ai-secrets.json`，这些不属于公开部署包，也不要加入 Git。

升级前备份数据库与本机配置、记录 Git tag。升级后保留原数据挂载；当前采用兼容式建表，不删除已有采集表。回滚时使用对应版本和升级前备份。发布包的 `BUILD_INFO.json` 记录源码提交，`MANIFEST.sha256` 可校验文件。

## 状态与定时任务

`GET /api/health` 返回版本。任务状态包含 queued/running/completed/partial/failed/interrupted。服务重启将未完成任务标记中断，用户可重试。

工作流与其他聊天自动化、系统 cron 独立，不会修改已有计划。避免同时开启两个重复采集/分析计划。

## 验证边界

已在本机执行后端测试、前端构建和浏览器交互。开发机未安装 Docker，不能宣称本机镜像构建通过；仓库 CI 配置了 docker build。真实 API Key 提供方仍需用自己的 Key 确认模型和计费权限。

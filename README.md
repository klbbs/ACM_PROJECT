# ACM Lab

支持个人训练与受邀成员协作的算法工作台：连接 Codeforces、洛谷、牛客和 AtCoder，聚合提交记录，调用 AI 生成能力分析与训练计划，再从真实题库中选择合适的练习。

**React + TypeScript + FastAPI + SQLite**。采集 CLI 可独立使用；前端构建后由后端提供，无需部署第二个前端服务。

## 功能

- 多人协作：邀请注册、管理员管理、独立训练空间、共享资料库、私人草稿及可恢复回收站。
- 网站防护：密码哈希、可撤销会话、CSRF、限流、域名校验、请求与后台任务配额、安全审计。

- 训练总览：7/30/90 天或全部历史，去重通过题数、提交通过率、活跃天数、训练趋势和知识点分布。
- 提交记录：按平台、时间、结果和题目筛选，分页查看、跳转原提交、导出筛选结果前 100 条。
- AI 教练：API Key 或官方 Codex 设备码登录；动态模型目录；有依据的优势、薄弱点和 7 天训练计划；历史报告。
- 智能选题：缓存 CF 官方题库，按难度、标签、比赛 ID 筛选；排除历史提交和练习反馈；AI 只能从真实候选中选择。
- 训练偏好：目标 Rating、训练区间、新题优先、强化与避开标签；记录完成/跳过状态。
- 每日工作流：北京时间定时采集，成功后可自动生成 AI 报告；任务进度、错误和中断状态可查。
- 部署：Docker Compose、原生 Python、systemd/Caddy 示例、依赖锁、ZIP/TAR 部署包和 GitHub Actions。

当前推荐题库覆盖 **Codeforces**；其他三个平台的数据参与统计与 AI 分析。牛客提交仅覆盖公开练习页，AtCoder Problems 可能存在延迟。

## 多人服务器部署

Compose 默认启用多人模式。先运行 `python3 scripts/bootstrap.py`，在 `.env` 填写 `ACM_ALLOWED_HOSTS` 和 `ACM_PUBLIC_ORIGIN` 为你的 HTTPS 站点，然后 `docker compose up -d --build`。首次访问使用部署令牌创建管理员，之后通过用户名和密码登录；在「共享资料与成员」生成邀请码。

每位用户的平台账号、Cookie、训练数据、API Key 与官方登录独立；仅主动发布的资料或报告摘要共享。旧数据保留，需显式导入管理员的空空间。

完整部署、权限、旧数据迁移和密码恢复见 [多人版运维说明](docs/MULTIUSER.md)。

## 本机启动

需要 Python 3.10+、Node.js 22+（仅构建前端时需要）。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -c requirements.lock -e '.[dev]'
python3 scripts/bootstrap.py
```

启动后进入「连接与设置 → 算法平台连接」，直接添加平台用户名、数字 UID 或个人主页链接，可选填 Cookie。保存后立即生效，无需编辑服务器文件。也支持参考 `accounts.example.toml` 手动编辑 `account.toml`，使用 `cookie_env` 或单行 `cookie`，二选一。网页配置优先于该文件，真实配置均不提交 Git。

```bash
cd frontend
npm ci
npm run build
cd ..
.venv/bin/acm-web
```

打开 **http://127.0.0.1:8000**。本机默认仅监听回环地址；本机个人模式不强制访问令牌。`bootstrap.py` 生成的 `.env` 用于 Docker/systemd，不会由本机 CLI 自动加载。

后续也可从任意目录执行 `bash /项目路径/scripts/start_local.sh` 启动；已有桌面采集项目的保留数据升级步骤见 [迁移与备份](docs/DEPLOYMENT.md#迁移与备份)。

发布包自带 `frontend/dist`，可以跳过 Node.js 构建。开发时分别启动后端和 `cd frontend && npm run dev`，Vite 会将 `/api` 代理到后端 8000 端口。

## AI 接入

### API Key：可用于服务器部署

在「连接与设置 → AI API 接入」配置：

- 选择 OpenAI、DeepSeek、Claude、Gemini、Qwen（中国/新加坡）、Kimi、硅基流动、智谱或自定义网关预设。
- 填入自己的 Key 和默认模型 ID，可「保存并读取模型列表」；不提供目录的服务可手动填写 ID。
- 按模型支持情况调整结构化输出、思考/推理强度、输出上限与超时，默认不发送 temperature。
- 保存后可主动执行会计费的连接测试，在分析/选题/工作台使用 API Key 通道。

密钥按用户及 API 地址隔离，换地址不会发送上一家的 Key。旧版个人模式的 `OPENAI_API_KEY` 仅用于官方 OpenAI 默认地址；多人模式不继承此变量。失败不自动重试或换模型，不生成假报告。适配器用模拟响应验证，真实模型权限需用户接入后测试。详细填写步骤、各家地址、参数和排错见 [AI 提供方配置](docs/AI_PROVIDERS.md)。

### 官方 ChatGPT 登录（支持无桌面服务器）

使用官方 Codex App Server 的设备码流程：在设置页点击「申请官方登录设备码」，在自己的电脑或手机打开官方验证链接并输入代码；网页自动检测授权完成，之后可读取模型目录、分析数据和选题。可以取消申请或退出官方账号。需先在 ChatGPT 安全设置 / 工作区权限中开启设备码登录；可用性取决于官方账号权限。

本机安装官方 Codex CLI 后可直接使用。无桌面服务器按下方配置启用：

```dotenv
ACM_ENABLE_SERVER_CODEX=true
ACM_IMAGE_TARGET=server-codex
```

将这两行加入服务器 `.env`，再执行 `docker compose up -d --build`。该目标镜像自带官方 CLI，无需服务器安装浏览器。未启用时仍可使用 API Key。原生 Python 部署需安装官方 CLI、将以上第一项交给服务进程，并配置 `ACM_CODEX_BINARY` 为实际路径。

多人模式使用 `data/users/<用户ID>/codex/` 保存各自的官方授权，不继承其他成员或宿主机登录。旧版个人模式仍使用 `data/codex/`。挂载数据目录后重建容器保留授权，对外访问必须配置 HTTPS。

模型列表可能包含缓存，实际调用成功才代表有权限。ChatGPT 登录与 API Key 的额度及计费独立；API Key 仍可用于每日自动分析。服务器首次授权必须由你在官方网页完成，本项目不代填密码或抓取 ChatGPT Cookie。

参考：[官方无桌面登录](https://learn.chatgpt.com/docs/auth)、[官方 App Server 登录协议](https://learn.chatgpt.com/docs/app-server)。

## Docker 部署

```bash
git clone git@github.com:klbbs/ACM_PROJECT.git
cd ACM_PROJECT
python3 scripts/bootstrap.py
# 访问令牌在 .env 中；平台账号可在启动后的网页设置中添加
docker compose up -d --build
```

默认绑定宿主机 `127.0.0.1:8000`。多人模式首次访问使用 `.env` 中的 `ACM_ACCESS_TOKEN` 创建管理员，此后使用账号密码登录。对外部署时配置 HTTPS 反向代理，可参考 [Caddy 示例](deploy/Caddyfile.example)。不要将账号数据目录作为静态目录发布。

Compose 挂载 `./data` 和只读的 `account.toml`，升级镜像不会删除数据。容器根目录只读，使用非 root 用户并限制进程数与内存；初始化脚本会按本机 UID/GID 写入配置。换服务器时确认目录权限与运行用户一致。

原生部署、环境变量和备份见 [部署说明](docs/DEPLOYMENT.md)。

## 独立采集 CLI

```bash
.venv/bin/ojcrawl check --config account.toml
.venv/bin/ojcrawl crawl --config account.toml
.venv/bin/ojcrawl crawl --config account.toml --full
.venv/bin/ojcrawl export --database data/accounts.sqlite3 --output data/submissions.jsonl
```

如果已在网页配置账号，CLI 使用 `--config data/accounts.web.toml`（旧布局为 `data/account-history/accounts.web.toml`）。

默认重抓检查点之前 7 天以更新近期判题结果。达到分页上限或账号失败时，不推进其检查点，其他账号继续；更早的重新判题需要 `--full`。Web 采集统一写入 `ACM_DATA_DIR` 指向的数据库。

每日工作流默认关闭，可在设置页启用。服务必须持续运行；关闭网页不影响后台任务。同一数据库只启动一个服务进程，避免与已有的外部 cron/聊天自动化重复执行。

## 打包与版本控制

```bash
python3 scripts/package.py
python3 scripts/verify_release.py releases/acm-lab-0.8.0.zip
```

生成 ZIP 与 TAR.GZ，包含前端构建、Python 源码、依赖锁、部署示例、校验清单和构建提交号。文件白名单确保发布包不含 `account.toml`、`.env`、API Key、数据库、原始快照、学习资料或虚拟环境。

仓库：[klbbs/ACM_PROJECT](https://github.com/klbbs/ACM_PROJECT)。稳定分支为 `main`，版本标签为 `vMAJOR.MINOR.PATCH`；建议用功能分支和 PR 开发。每次 push/PR 运行测试、前端构建和部署包检查，并执行容器构建校验。

## 项目结构

```text
frontend/             React + TypeScript 界面
src/acmweb/           Web API、统计、AI、任务与题库
src/ojcrawler/        四个平台采集器与原 CLI
tests/               采集、API、任务、安全边界及 AI 校验测试
scripts/             初始化、打包和包校验
deploy/              systemd / Caddy 示例
docs/                架构和部署文档
data/                本机数据与密钥（忽略，不发布）
```

验证命令：

```bash
.venv/bin/python -m pytest -q
cd frontend && npm run build
```

数据发送范围和接口见 [架构说明](docs/ARCHITECTURE.md)。

### 刷题工作台（第一阶段）

侧栏新增「刷题工作台」：CF 推荐题、题面和样例、自动保存代码、提交快照、判题核验与 AI 分步提示/错因复盘。
当前使用官方页面手动提交后关联提交号，自动代提交将在后续接入；服务器不会执行用户代码。
完整流程、隐私边界和后续计划见 [工作台说明](docs/WORKBENCH.md)。

### 增强代码编辑器（0.6.2）

CodeMirror 6 提供四种语言标签的高亮与本地补全、普通/Vim 切换、字号与换行偏好、全屏、搜索替换及安全降级。补全不调用 AI，不发送源码；Vim `:w` 沿用现有草稿保存流程。见 [编辑器说明](docs/EDITOR.md)。

鼠标操作工作台验收、子 agent 教练反馈和修复记录见 [2026-10-19 验收报告](docs/USER_ACCEPTANCE_2026-10-19.md)。判题为隔离模拟，未向真实账号交题。

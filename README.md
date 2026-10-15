# ACM Lab

用于个人算法训练的完整工作台：连接 Codeforces、洛谷、牛客和 AtCoder，聚合提交记录，调用 AI 生成能力分析与训练计划，再从真实题库中选择合适的练习。

**React + TypeScript + FastAPI + SQLite**。采集 CLI 可独立使用；前端构建后由后端提供，无需部署第二个前端服务。

## 功能

- 训练总览：7/30/90 天或全部历史，去重通过题数、提交通过率、活跃天数、训练趋势和知识点分布。
- 提交记录：按平台、时间、结果和题目筛选，分页查看、跳转原提交、导出筛选结果前 100 条。
- AI 教练：API Key 或本机 Codex 登录；动态模型目录；有依据的优势、薄弱点和 7 天训练计划；历史报告。
- 智能选题：缓存 CF 官方题库，按难度、标签、比赛 ID 筛选；排除历史提交和练习反馈；AI 只能从真实候选中选择。
- 训练偏好：目标 Rating、训练区间、新题优先、强化与避开标签；记录完成/跳过状态。
- 每日工作流：北京时间定时采集，成功后可自动生成 AI 报告；任务进度、错误和中断状态可查。
- 部署：Docker Compose、原生 Python、systemd/Caddy 示例、依赖锁、ZIP/TAR 部署包和 GitHub Actions。

当前推荐题库覆盖 **Codeforces**；其他三个平台的数据参与统计与 AI 分析。牛客提交仅覆盖公开练习页，AtCoder Problems 可能存在延迟。

## 本机启动

需要 Python 3.10+、Node.js 22+（仅构建前端时需要）。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -c requirements.lock -e '.[dev]'
python3 scripts/bootstrap.py
```

编辑 `account.toml` 为自己的账号。洛谷、牛客填写数字 UID。Cookie 可使用 `cookie_env` 指定环境变量名，或使用单行 `cookie` 字段，二选一；真实配置文件不提交到 Git。

```bash
cd frontend
npm ci
npm run build
cd ..
.venv/bin/acm-web
```

打开 **http://127.0.0.1:8000**。本机默认仅监听回环地址；本机个人模式不强制访问令牌。`bootstrap.py` 生成的 `.env` 用于 Docker/systemd，不会由本机 CLI 自动加载。

发布包自带 `frontend/dist`，可以跳过 Node.js 构建。开发时分别启动后端和 `cd frontend && npm run dev`，Vite 会将 `/api` 代理到后端 8000 端口。

## AI 接入

### API Key：可用于服务器部署

在「连接与设置 → AI API 接入」配置：

- OpenAI 官方地址 `https://api.openai.com/v1`，协议选择 **Responses API**。
- 兼容提供方填写其 HTTPS API 地址，协议选择 **Chat Completions**。
- 保存 Key 后，在分析或选题页选择 **API Key 通道**，刷新模型目录，再选择支持文本及 JSON 输出的模型。

也可通过服务端 `OPENAI_API_KEY` 环境变量提供 Key。模型调用失败、额度不足、输出被截断或格式不符时，任务明确报错，不生成假报告。API Key 通道没有使用开发机上的真实收费 Key 测试；协议、异常和结果校验有模拟测试。

### 本机 ChatGPT / Codex

安装官方 Codex CLI 后，设置页可以读取现有登录，也可使用 **Continue with ChatGPT** 或设备码登录。认证由官方客户端管理，本项目不抓取 ChatGPT Cookie、不读取或展示其 token。

分析页选择 **本机 ChatGPT / Codex**，刷新模型目录。目录可能来自客户端缓存，实际调用完成才代表当前账号可用。使用前需确认本机官方 CLI 的登录与模型调用可用。

本机通道仅用于个人应用，生产部署强制禁用。ChatGPT 网页会员与 API Key 的计费/权限独立。托管服务的官方网页登录需要按 OpenAI 的 Sign in with ChatGPT 注册与接入条件单独实现；当前服务器部署使用 API Key。

参考：[官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)、[网站登录接入条件](https://developers.openai.com/siwc/website)。

## Docker 部署

```bash
git clone git@github.com:klbbs/ACM_PROJECT.git
cd ACM_PROJECT
python3 scripts/bootstrap.py
# 编辑 account.toml；访问令牌在 .env 中，脚本不会打印令牌
docker compose up -d --build
```

默认绑定宿主机 `127.0.0.1:8000`。本机访问后输入 `.env` 中的 `ACM_ACCESS_TOKEN` 解锁。对外部署时配置 HTTPS 反向代理，可参考 [Caddy 示例](deploy/Caddyfile.example)。不要将账号数据目录作为静态目录发布。

Compose 挂载 `./data` 和只读的 `account.toml`，升级镜像不会删除数据。进程使用非 root 用户；初始化脚本会按本机 UID/GID 写入配置。换服务器时确认目录权限与运行用户一致。

原生部署、环境变量和备份见 [部署说明](docs/DEPLOYMENT.md)。

## 独立采集 CLI

```bash
.venv/bin/ojcrawl check --config account.toml
.venv/bin/ojcrawl crawl --config account.toml
.venv/bin/ojcrawl crawl --config account.toml --full
.venv/bin/ojcrawl export --database data/accounts.sqlite3 --output data/submissions.jsonl
```

默认重抓检查点之前 7 天以更新近期判题结果。达到分页上限或账号失败时，不推进其检查点，其他账号继续；更早的重新判题需要 `--full`。Web 采集统一写入 `ACM_DATA_DIR` 指向的数据库。

每日工作流默认关闭，可在设置页启用。服务必须持续运行；关闭网页不影响后台任务。同一数据库只启动一个服务进程，避免与已有的外部 cron/聊天自动化重复执行。

## 打包与版本控制

```bash
python3 scripts/package.py
python3 scripts/verify_release.py releases/acm-lab-0.2.0.zip
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

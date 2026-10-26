# ACM Lab

当前版本为 0.21.1。修复排行榜同步、专项选题、题面不等号和编辑器缩进，统一默认 AI 配置，新增逐题阅读报告、对战日期限制及专项难度范围；首次部署由部署者自设管理员。只做本地验收与仓库发布，不部署服务器。真实平台可用性与模型判断准确性仍需结合实际使用确认。

支持个人训练与受邀成员协作的算法工作台：连接 Codeforces、洛谷、牛客和 AtCoder，聚合提交记录，调用 AI 生成能力分析与训练计划，再从真实题库中选择合适的练习。

**React + TypeScript + FastAPI + SQLite**。采集 CLI 可独立使用；前端构建后由后端提供，无需部署第二个前端服务。

牛客可在「连接与设置」直接使用[官方微信扫码登录](docs/NOWCODER_QR.md)：扫码确认后自动核对 UID 并绑定，无需填写牛客密码。

## 功能

- 主要目录：首页按开始训练、记录与社交、连接与管理展示所有可用功能，支持搜索和首次使用路线；侧栏同步分组。

- 多人协作：邀请注册、管理员管理、独立训练空间、共享资料库、私人草稿及可恢复回收站。
- 网站防护：密码哈希、可撤销会话、CSRF、限流、域名校验、请求与后台任务配额、安全审计。
- Cookie 助手：在「连接与设置」复制或下载油猴脚本，在官方平台主动获取 Cookie 后回本站粘贴、保存与检测；不再要求未签名 Firefox 扩展。无 HttpOnly 时允许明确确认后复制可读结果（可能不完整），API 空结果可尝试本页可读部分，完整 Cookie 可用 F12 获取；验证码、访问限制和重试有明确指引。[安装说明](docs/BROWSER_EXTENSION.md)
- 更新公告：登录后提示未读版本，按账号保存已读状态；「更新公告」可回看历史。平台绑定教程已合并到「连接与设置」。

- 训练总览：7/30/90 天或全部历史，去重通过题数、提交通过率、活跃天数、训练趋势和知识点分布。
- 提交记录：按平台、时间、结果和题目筛选，分页查看、跳转原提交、导出筛选结果前 100 条。
- 收藏与备注：题面旁收藏好题、保存私人笔记，列表可搜索并回到工作台；取消收藏不删除备注，保存在每位用户独立数据库。
- 私人代码模板：工作台可创建多个模板，设置中文或英文触发词及适用语言，输入后按 Tab 补全；Enter 仍用于换行。[代码模板说明](docs/CODE_TEMPLATES.md)
- 私人代码模板：编辑器下方管理多个模板，自定义中文或英文触发词及适用语言，输入触发词后按 Tab 补全；支持占位符跳转。见 [代码模板说明](docs/CODE_TEMPLATES.md)。
- AI 教练：API Key 或官方 Codex 设备码登录；动态模型目录；有依据的优势、薄弱点和 7 天训练计划；历史报告。
- 智能选题：从所有已验证登录平台的官方缓存混合随机抽样，严格遵守发布日期下限、已做题与对战排除；AI 只能从真实候选中选择。
- 训练偏好：目标 Rating、训练区间、新题优先、强化与避开标签；记录完成/跳过状态。
- 每日任务：自行设置题量与周期，AI 生成整张题单；每日 k 题有 k 次指定单题换题机会，完成后自行记录实际用时；官方 AC 核验普通签到/K 签，统计月度和累计记录。
- 部署：Docker Compose、原生 Python、systemd/Caddy 示例、依赖锁、ZIP/TAR 部署包和 GitHub Actions。

已有 Docker Compose 或 systemd 服务可先运行 `./update.sh --check` 检查，再运行 `./update.sh` 更新；支持候选构建、数据库与配置备份、健康检查及失败回滚。完整支持边界见[一键更新说明](docs/UPDATE.md)。此功能未在真实服务器运行。

推荐题库包含 CF 官方带难度题、AtCoder 近期比赛和公开索引、洛谷专题分页及牛客标签/关键词有限列表；缺少可靠官方日期时不进入有日期限制的推荐。专项支持查看候选覆盖、补抓本专题题库，以及主动用 AI 阅读官方题面核查缺少精确标签的细类方法；AI 证据不冒充官方标签或模板资格。平台会话必须通过当前身份检测，不能以公开资料可读代替。详见 [推荐范围](docs/RECOMMENDATIONS.md)及[专项训练说明](docs/SPECIALIST_TRAINING.md)。

## 多人服务器部署

Compose 默认启用多人模式。先运行 `python3 scripts/bootstrap.py`，在 `.env` 填写 `ACM_ALLOWED_HOSTS` 和 `ACM_PUBLIC_ORIGIN` 为你的 HTTPS 站点，然后 `docker compose up -d --build`。首次访问使用部署令牌创建管理员，自行填写用户名、可选昵称和密码，没有内置默认账号或密码；之后通过用户名和密码登录；在「共享资料与成员」生成邀请码。

每位用户的平台账号、Cookie、训练数据、API Key 与官方登录独立；仅主动发布的资料或报告摘要共享。旧数据保留，需显式导入管理员的空空间。管理员用户名、昵称与密码均由首次部署者自行决定。登录后管理员可在「管理后台」审核或撤下题面译文、管理成员、批准 AI 中转站域名、查看安全审计和后台运行日志。

首次初始化仅创建部署者指定的一位管理员；升级保留既有管理员及所有成员，不自动改名、删除或降权。其他成员可在「共享资料与成员」申请管理员权限，须由现有管理员在后台明确同意；通过后重新登录生效，不能直接在成员编辑表单提权。

已有服务器可使用 `./update.sh --check` 先查看更新计划，再由运维人员执行 `./update.sh`。工具为 Docker Compose / systemd 提供候选构建、停服备份、健康检查及失败回滚；使用条件和数据边界见 [一键更新说明](docs/UPDATE.md)。本次开发没有执行服务器更新。

完整部署、权限、旧数据迁移和密码恢复见 [多人版运维说明](docs/MULTIUSER.md)；源码及私有配置边界见 [仓库隐私说明](docs/REPOSITORY_PRIVACY.md)。

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

服务启动时会在控制台输出配置巡检，指出未绑定的平台、AI/代码自测/翻译服务缺项、前端资源缺失等可能不可用的功能。巡检只读本地配置，不会访问平台或 AI 服务；Cookie 只检查格式，不代表登录有效，AI 与外部服务是否连通仍需在设置页手动检测。关键运行事件会保存到数据目录下的 `logs/runtime.jsonl`，单文件最多 1 MiB，自动保留 4 份轮转文件；仅管理员可在「管理后台 → 后台运行日志」筛选查看。日志记录固定事件和脱敏诊断字段，不保存账号名、Cookie、密码、API Key、请求正文、提交源码或原始异常文本。

后续也可从任意目录执行 `bash /项目路径/scripts/start_local.sh` 启动；已有桌面采集项目的保留数据升级步骤见 [迁移与备份](docs/DEPLOYMENT.md#迁移与备份)。

发布包自带 `frontend/dist`，可以跳过 Node.js 构建。开发时分别启动后端和 `cd frontend && npm run dev`，Vite 会将 `/api` 代理到后端 8000 端口。

## AI 接入

### API Key：可用于服务器部署

在「连接与设置 → AI API 接入」配置：

- 选择 OpenAI、DeepSeek、Claude、Gemini、Qwen（中国/新加坡）、Kimi、硅基流动、智谱或自定义网关预设。
- 填入自己的 Key 后保存并读取模型目录，再从下拉栏选择默认模型；不提供目录的服务会提示手动输入 ID。
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

模型列表可能包含缓存，实际调用成功才代表有权限。ChatGPT 登录与 API Key 的额度及计费独立；API Key 可用于每日任务选题和训练分析。服务器首次授权必须由你在官方网页完成，本项目不代填密码或抓取 ChatGPT Cookie。

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

## 推荐、好友与对战

AI 从已验证登录平台混合选题。请在训练偏好保存最早发布日期；目录未提供可靠时间的题会被排除，不以题号推测年份。详见 [推荐说明](docs/RECOMMENDATIONS.md)。

多人模式提供“排行榜与好友”：可选择公开主页和每日榜单、检索并添加好友、约定 CF 精确难度对战。设置页检测平台身份成功后自动安全关联榜单；每个平台身份仍只能属于一个用户，主动解除后可在个人主页重新认领。同步题库同时采集已配置账号今日提交；榜单点击“查看当日题目”查看题目链接、难度、分数与标签。对战按官方 AC 证据判胜，计普通签到但不抵扣每日任务。详见 [社交指南](docs/SOCIAL.md)。

部分 CF Gym/Mashup 不出现在普通用户历史中，可在排行榜展开“补充 Gym / Mashup 虚拟参赛记录”，填入官方比赛或提交链接后同步。比赛范围会保存，选择往日日期可补同步；遇到权限或安全验证限制会明确标为不完整，不能保证服务器读到全部私有比赛记录。

AI 选题先根据候选元信息初选，再逐题读取官方题面、交给所选模型确认并生成私人报告。每日随机、每日专项、Roll、独立专项和普通推荐的题目卡均提供“查看 AI 报告”；打开前提示“此报告为ai生成的代码解析报告，你确定要现在看吗？”，取消不会读取报告正文。报告可能包含解题思路和实现注意点，未运行代码，不是官方题解。题面获取或阅读确认失败不保存新题单，换题失败不扣 Roll；旧题单没有报告时明确说明。阅读会增加生成耗时与模型调用费用，读取已保存报告不会再次调用 AI。

工作台自动错误提示默认关闭，需同意将当次代码发送至所选 AI。CF/AtCoder 接入工作台代交与自动判题，登录及平台验证失败时停止发送；洛谷/牛客仍在官方页面提交。外部交题后可一键同步本题记录，无需先粘贴源码或手填提交号。源码可读时再复盘。真实平台交题验收边界见 [安全与验证说明](docs/SECURITY_REVIEW.md)。本次开发未执行服务器部署。

## 独立采集 CLI

```bash
.venv/bin/ojcrawl check --config account.toml
.venv/bin/ojcrawl crawl --config account.toml
.venv/bin/ojcrawl crawl --config account.toml --full
.venv/bin/ojcrawl export --database data/accounts.sqlite3 --output data/submissions.jsonl
```

如果已在网页配置账号，CLI 使用 `--config data/accounts.web.toml`（旧布局为 `data/account-history/accounts.web.toml`）。

默认重抓检查点之前 7 天以更新近期判题结果。达到分页上限或账号失败时，不推进其检查点，其他账号继续；更早的重新判题需要 `--full`。Web 采集统一写入 `ACM_DATA_DIR` 指向的数据库。

“训练”统一提供“每日训练”和“专项训练”。每日训练可选 AI 根据近期情况随机出题，或按用户指定专题训练；题量自由设置 1～50，目标 k 道时每天有 k 次指定单题换题机会。专题目录使用洛谷完整算法分类的 22 个根分类、262 个节点，可搜索并选择新学、巩固、变式、加强四阶段，仅新学强制模板首题。生成、评估和追问直接通过按钮发起，不设静态同意复选框。官方 AC 核验后记录签到，完成当日题单记录 K 签；独立专项计划不自动抵扣每日题单。详见 [专项训练说明](docs/SPECIALIST_TRAINING.md)。已移除定时抓取任务；同一数据库只启动一个服务进程。

## 打包与版本控制

```bash
python3 scripts/package.py
python3 scripts/verify_release.py releases/acm-lab-0.21.1.zip
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
当前使用官方页面手动提交后关联提交号，自动代提交将在后续接入；网站通过专用 Judge0 隔离服务提供代码自测，未配置时禁用。
完整流程、隐私边界和后续计划见 [工作台说明](docs/WORKBENCH.md)。

### 增强代码编辑器（0.6.2）

CodeMirror 6 提供四种语言标签的高亮与本地补全、普通/Vim 切换、字号与换行偏好、全屏、搜索替换及安全降级。补全不调用 AI，不发送源码；Vim `:w` 沿用现有草稿保存流程。见 [编辑器说明](docs/EDITOR.md)。

鼠标操作工作台验收、子 agent 教练反馈和修复记录见 [2026-10-19 验收报告](docs/USER_ACCEPTANCE_2026-10-19.md)。判题为隔离模拟，未向真实账号交题。


平台绑定的保存/编辑规则见[平台教程](docs/PLATFORM_BINDING.md)：CF、AtCoder 在本人浏览器登录后使用[油猴 Cookie 助手](docs/BROWSER_EXTENSION.md)，回本站保存并检测；两平台不再提供服务器密码或远程浏览器登录。洛谷保留同会话[官方登录窗口](docs/PASSWORD_LOGIN.md)，牛客使用微信扫码。可靠验证身份后才绑定；CF/AtCoder 支持工作台提交，洛谷/牛客仍使用官方页面。牛客扫码与导入 Cookie 二选一。

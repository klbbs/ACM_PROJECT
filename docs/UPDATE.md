# 一键更新（`./update.sh`）

面向**已经存在的**服务器部署：服务器管理员进入项目目录执行一条命令，拉取上游、准备候选
版本、停服备份、切换、启动并做健康检查；失败时自动恢复更新前的代码、运行环境与数据。

更新器是本地 CLI，只做部署升级：

- 不提供网页端远程执行入口，不接受来自浏览器的更新请求；
- 不新建部署（没有现成容器或 systemd 单元时会拒绝，并提示显式指定 `--mode`）；
- 不做合并/变基，只允许快进（fast-forward）；
- 不使用 `git reset --hard`、`git clean`，不删除未跟踪资料；
- 不修改 systemd 单元文件。

## 支持的两种部署

| 方式 | 识别依据 | 更新动作 |
| --- | --- | --- |
| `docker` | 项目内有 `compose.yaml`（或 `compose.yml`、`docker-compose.yaml/.yml`），Compose v2 可用，服务已有容器，且该服务显式写了 `image:` | 在候选源码上构建新镜像（独立 tag，不覆盖旧 tag），停服备份后把旧镜像保留到回滚 tag，再让 Compose 使用新镜像 |
| `systemd` | 存在运行中、`WorkingDirectory` 指向项目目录，且 `ExecStart` 明确执行项目 `.venv/bin` 的单元 | 在隔离目录构建前端与全新 venv，停服备份后切换受跟踪文件、前端产物和 `.venv` 符号链接 |

`--mode auto`（默认）只在能唯一识别时继续；同时发现两种部署、或完全无法识别时，会报错并要求
用 `--mode systemd` / `--mode docker` 明确指定，不会猜测。

### systemd 单元的接受条件

因为更新通过切换项目 `.venv` 生效，只接受以下 `ExecStart`（`WorkingDirectory` 必须是项目目录）：

- `<项目>/.venv/bin/acm-web`
- `<项目>/.venv/bin/python -m acmweb.main`（`python3` 同理）

若单元实际执行 `/opt/other-runtime/bin/acm-web` 之类的其他运行时，更新会**拒绝**并说明原因，
因为替换项目 `.venv` 并不能升级该服务。更新器不会改写你的单元文件；请自行把 `ExecStart`
改到项目 `.venv` 后再运行。

## 前置条件

- 项目目录是 Git 仓库根目录，当前在跟踪上游的分支上（不是游离 HEAD）；
- 已跟踪文件与索引干净（未跟踪的私人文件允许存在，更新不会删除它们）；
- systemd 方式需要 root（停止/启动服务、备份数据、创建候选 venv），以及 Python 3.10+；
  前端构建需要已安装 Node.js 22+ 与 npm；每次更新都会在候选目录执行 `npm ci` 自动下载锁定的前端依赖，
  不依赖或修改现有 `frontend/node_modules`；
- Docker 方式需要可用的 `docker` 与 Compose v2；`.env` 需能解析 `compose.yaml`；旧镜像必须还在
  （否则没有可回滚镜像，会拒绝）；
- 需要网络：`git fetch`、原生方式的 pip/npm 下载；
- 预留磁盘空间：每次更新会保留候选源码、候选 venv、后端备份和旧镜像回滚 tag。

## 用法

```bash
./update.sh --check          # 无副作用预检（推荐每次先跑）
./update.sh                  # 更新到 origin/main，失败自动回滚
./update.sh --mode systemd --service acm-lab.service
./update.sh --mode docker --compose-file compose.yaml --compose-file compose.browser.yaml
```

`./update.sh --check` 会读取本地提交并查询上游 `main` 的最新提交，显示是否有更新且不写入项目；
`./update.sh` 会再次检查版本，自动下载最新提交、安装候选版 Python 与前端依赖，再构建、备份、切换和健康检查。
其中 Python 包由候选虚拟环境的 `pip` 按 `requirements.lock` 安装，前端包由 `npm ci` 按
`frontend/package-lock.json` 安装；Docker 部署由候选 Dockerfile 在镜像构建阶段自动下载这些依赖。
更新器不会替操作系统安装 Python、Node.js、Docker 或 systemd，也不会自行创建首次部署。

`acm-update`（安装包提供的命令）与 `scripts/update.py` 等价；但 `./update.sh` 额外设置了
`PYTHONDONTWRITEBYTECODE=1`，适合干净部署的首次预检。直接使用 `acm-update` 时如需完全避免
写入字节码缓存，请自行设置 `PYTHONDONTWRITEBYTECODE=1`。

### 参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--check` / `--dry-run` | 关闭 | 只预检：不 fetch、不加锁、不备份、不停服、不创建 `.acm-update` |
| `--mode auto\|docker\|systemd` | `auto` | 部署方式；无法唯一识别时必须显式指定 |
| `--ref` | `origin/main` | 上游引用（远程跟踪分支最可靠；本地引用无法在预检中比较上游） |
| `--service` | 自动识别 | systemd 单元名（自动补 `.service`） |
| `--compose-service` | `web` | Compose 服务名 |
| `--compose-file` | 自动 | 可重复；应与部署时使用的 compose 文件一致 |
| `--health-url` | `http://127.0.0.1:8000/api/health` | 健康检查地址 |
| `--public-origin` | 读 `.env` 的 `ACM_PUBLIC_ORIGIN` | 健康检查使用的 `Host` 头来源（HTTPS 站点） |
| `--health-timeout` | `120` 秒 | 启动后等待健康的总时长 |
| `--data-dir` | systemd 读 `.env` 的 `ACM_DATA_DIR`，否则 `data`（旧布局 `data/account-history`） | 备份的数据目录 |
| `--python` | `python3` | 创建候选 venv 的解释器 |
| `--skip-frontend-build` | 关闭 | 沿用现有 `frontend/dist`；仅在确认产物与目标版本匹配时使用 |
| `--verbose` | 关闭 | 更详细日志 |

## 预检（`--check`）的保证与边界

预检承诺不改变部署状态：

- 不执行 `git fetch`（用 `git ls-remote` 只读解析上游 SHA）、不加锁、不创建 `.acm-update`、
  不构建、不备份、不停止服务、不修改项目文件；
- Git 只读命令带 `GIT_OPTIONAL_LOCKS=0`，不会为刷新索引而重写 `.git/index`（其 mtime 也不变）；
- `./update.sh` 导出 `PYTHONDONTWRITEBYTECODE=1`，`scripts/update.py` 在导入更新器前设置
  `sys.dont_write_bytecode = True`，干净部署首次预检不会生成 `src/acmweb/__pycache__`；
- 只以只读方式解析 `.env` 中白名单键（`ACM_PUBLIC_ORIGIN`、`ACM_DATA_DIR`），不执行 `.env`
  内容、不做变量展开。

边界：预检会读取仓库与配置、访问远程（`ls-remote`）和只读查询 systemd/Docker；这不是“操作系统
层面绝对零字节变化”的承诺。直接运行 `acm-update` 控制台入口时，解释器仍可能写字节码缓存，
需要绝对干净时请用 `./update.sh` 或自行设置 `PYTHONDONTWRITEBYTECODE=1`。

预检会检查：仓库根目录、分支、已跟踪文件/索引是否干净、当前仓库是否已误跟踪私有文件、
上游目标是否快进、上游是否把 `data/`、凭据、虚拟环境、日志、数据库等私有路径变成跟踪文件、
目标版本新增文件是否会覆盖本地未跟踪文件、数据目录与健康 URL、Docker 旧镜像是否存在、
systemd 单元是否受支持。目标提交本地尚不存在时，会在预检输出中明确说明“执行更新时才会
核对文件清单”，不会假称已核对。

## 更新流程

1. **只读预检**：同上。
2. **加锁**：`.acm-update/update.lock` 使用 `flock` 非阻塞独占锁；已有更新进程时立即拒绝，不会并发操作。
3. **固定目标提交**：`git fetch` 后解析 `--ref` 为具体 SHA 并在整个流程中使用该 SHA；只接受
   当前 HEAD 的后代（快进）。本地已有未推送提交时也可快进（它们包含在目标历史中）。
4. **准备候选**：`git archive` 导出目标提交到 `.acm-update/candidates/<时间>-<SHA>`；
   归档中的符号链接、设备文件、越界路径会被拒绝（在停服之前）。
   - systemd：前端在候选目录执行 `npm ci` 下载锁定依赖并构建；
     然后创建**新的** venv，用该 venv 的 pip 安装**候选源码**（`pip install -c requirements.lock <候选目录>`），
     绝不在旧 venv 里原地安装；候选目录权限放开到服务用户可读可执行。
   - Docker：用**候选版本自己的 compose 文件**解析目标服务（image、build target 等），
     显式指定 `--project-directory <项目根>` 和原 `.env`，使 `./data`、`./account.toml`
     等相对挂载仍解析到项目目录而不是候选目录；构建上下文仍覆盖为候选目录，以独立 tag
     （`<目标仓库>:update-candidate-<时间>`）构建，旧镜像 tag 保持不变。
     目标版本更换 `image:` tag（例如 `0.19.1` → `0.20.0`）受支持；但若目标版本修改了服务的
     `volumes` 数据挂载、或删除了该服务名，会**明确拒绝**，不做自动数据迁移。
5. **停服前复核**：构建可能耗时数分钟；停服前再次确认 HEAD 仍是原提交、已跟踪文件/索引仍干净、
   没有新出现的未跟踪冲突、目标提交文件清单未变化。任何异常都以退出码 2 结束，**不停服、不覆盖**，
   保留你在构建期间的编辑。
6. **停服**：systemd 用 `systemctl stop` 并等待进入 inactive/failed；Compose 用
   `docker compose stop` 并等待运行容器消失。状态无法确认时按失败处理。
7. **停服后完整备份**：
   - `data` 整目录复制到 `.acm-update/backups/<时间>/data`；
   - 其中每个 SQLite 数据库再用 `sqlite3.Connection.backup()` 生成一致快照（包含已提交的 WAL），
     删除备份侧的 `-wal/-shm/-journal`，并对备份文件执行 `PRAGMA integrity_check`；
   - 逐个核对源文件是否都在备份中（大小/符号链接），缺失即失败，不会静默跳过备份；
   - `.env`、`account.toml`、`accounts.toml`、`accounts.web.toml` 及 `.env.*`（`.env.example` 除外）
     备份为 `0600`；备份目录 `0700`；
   - 旧代码归档 `worktree.tar`；`manifest.json`（`0600`）记录原 SHA、目标 SHA、模式、服务、
     数据目录与属主、数据库清单、配置清单、镜像 tag、日志路径。
8. **切换**：
   - 代码：只按 Git 跟踪文件清单写入候选内容，删除上游删除的跟踪文件；**不触碰未跟踪文件**；
     随后用 `git read-tree` + `git update-ref` 把分支快进到目标 SHA（不使用 `reset --hard/clean`）；
   - systemd：替换 `frontend/dist`（旧产物留在备份目录），把 `.venv` 换成指向候选 venv 的符号链接，
     旧 venv 移到 `backups/<时间>/venv-previous` 供回滚；文件属主调整为项目属主；
   - Docker：把旧镜像打成 `<仓库>:update-rollback-<时间>`，再把候选镜像打到**目标 compose 声明的
     `image:` tag**（可能与旧 tag 不同），随后 `docker compose up -d --no-build`（因此之后普通的
     `docker compose up` 也会使用正确镜像），并核对容器实际使用的镜像 ID。
     目标 tag 与旧 tag 相同时，旧镜像已先保存在回滚 tag 中。
9. **启动与健康检查**：真实 HTTP 请求 `--health-url`（默认本机回环），配置了公开站点时使用
   `ACM_PUBLIC_ORIGIN`（或 `--public-origin`）的 Host 头，避免被 TrustedHost 拒绝；**HTTP 200
   `{"status":"ok"}` 之外还要求目标确实是新版本**：systemd 必须 active 且 MainPID 已变化，
   Docker 容器必须是期望的新镜像。旧监听器返回 ok 不会被当成成功。
10. **成功**：写入 `manifest.json`（`status: ok`），输出原/新 SHA、备份路径、Docker 回滚 tag。

## 失败与回滚

- 停服前的任何失败（预检、构建、停服前复核）不会停止服务。
- 停服或备份阶段失败：立即尝试恢复旧服务并做健康检查，代码尚未切换。
- 切换/启动/健康检查失败：自动回滚——
  - 停止新服务；
  - 用停服时的备份恢复 `data`（旧版本的迁移可能已经改过数据库）与 `.env`/账号配置；
  - 用 `worktree.tar` 恢复跟踪文件、把分支恢复到原 SHA；
  - 恢复原 `frontend/dist`、原 `.venv`（或原符号链接）；
  - Docker：恢复旧 compose 文件（`image:` 回到旧 tag），把回滚 tag 重新指向旧 `image:`，
    `up -d --no-build` 后**核对旧镜像 ID 并做健康检查**；
  - 失败版本改过的数据移到 `.acm-update/failed/<时间>/`（保留故障现场），不删除；
  - `manifest.json` 记录 `rolled_back`；回滚未完全成功时记录 `rollback_incomplete` 与问题清单。
- 退出码非 0 时请先看日志与 `manifest.json`；`rollback_incomplete` 需要人工介入（旧服务未通过
  健康检查、或某项恢复失败）。备份目录不会被自动清理。

## `.acm-update` 布局

```
.acm-update/
  update.lock                  # 单实例锁（flock）
  logs/update-<时间>.log       # 0600，不含凭据
  backups/<时间>/              # 0700
    manifest.json              # 0600：原/新 SHA、状态、备份清单、镜像 tag
    worktree.tar               # 0600：更新前受跟踪代码
    data/                      # 停服时 data 的完整副本（SQLite 已快照含 WAL）
    credentials/               # .env、account.toml 等（0600）
    frontend-dist/             # 原前端产物（若切换过）
    venv-previous/             # 原虚拟环境（若切换过）
  failed/<时间>/               # 回滚时移出的故障版本数据/venv
  candidates/<时间>-<SHA>/     # 候选源码（及构建产物）
  venvs/<时间>-<SHA>/          # 候选虚拟环境
  build/                       # Docker 构建覆盖文件
```

该目录已加入 `.gitignore` 与 `.dockerignore`，不会进入 Git 或镜像构建上下文。候选目录和旧镜像
回滚 tag 不会自动清理，请按磁盘情况人工清理（保留最近的备份）。

## 安全约定

- 所有外部命令使用 argv 列表，`shell=False`；引用、服务名、健康 URL 在传给 Git/systemctl 前校验，
  拒绝前导 `-`、空白与 shell 元字符。
- 不执行 `.env` 内容；不把 Cookie、口令、Token 写入日志、终端或 `manifest.json`；命令日志、子进程
  错误输出以及 `--verbose` 的 traceback 都会屏蔽 URL 中的账号口令与 `token=/password=/secret=`
  一类参数。
- 健康检查不带 Cookie/Authorization，不跟随重定向。
- 备份目录 `0700`、清单与配置 `0600`；不会把私有路径加入 Git。
- 更新器不 `chmod` 你已有的项目目录；只调整自己创建的 `.acm-update` 及新代码/候选 venv 的可读性。

## 明确不支持的场景

- 新建部署（没有现成部署时拒绝）；
- 非快进更新（上游分叉、强推重写历史）；
- 修改 systemd 单元；修改 Compose 服务名、数据挂载（volumes）或需要人工确认的端口/网络变更
  （`image:` tag 与 build target 的常规版本变更受支持）；
- 子模块（submodule）与仓库内符号链接（候选准备阶段拒绝）；
- 多机/多实例滚动更新、负载均衡下的零停机切换（当前单实例更新，会短暂停服）；
- 网页端触发更新、远程 SSH 代执行。

## 退出码

| 码 | 含义 |
| --- | --- |
| 0 | 更新成功，或已是最新版本 |
| 1 | 更新失败（已自动回滚，或回滚未完全成功，需要看日志/清单） |
| 2 | 预检拒绝（工作区脏、私有路径、非快进、模式不明、单元不受支持等），未做切换 |
| 3 | 参数/配置错误（如非法 `--ref`、健康 URL、服务名） |
| 130 | 被中断（如已停机，请检查 `.acm-update` 下的备份与日志） |

## 验证范围（开发自检，不等同服务器验收）

- `tests/test_updater.py`：真实临时 Git 远程/克隆、真实文件切换、真实 SQLite（含 WAL）快照与
  完整性校验、真实本机 HTTP 健康检查、真实 flock 锁；`systemctl`、`docker`、`npm`、构建 venv 的
  Python 为记录 argv 的替身工具。
- `tests/test_update_acceptance.py`（独立验收）：真实 Git/HTTP/SQLite/回滚，运行时边界用替身。
- 开发机没有 Docker，也没有运行中的 systemd 部署；上述测试**不是**真实 Docker 镜像构建或真实
  systemd 服务切换的验收，也从未连接或更新任何线上服务。真实部署验收需由用户在服务器上执行。

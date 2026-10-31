# Hermes 可选接入模块

`acmhermes` 默认不加载、不开放接口。启用后，Hermes 使用本人的默认 AI 从真实题库选一道题，阅读完整官方题面，返回中文题目说明与独立解题报告。提供文字、图片两个入口及任务控制接口；不运行代码、不代交、不修改训练任务、签到或榜单。

## 按需启用

设置 `ACM_ENABLE_HERMES=true`，由服务所有者重启生效。默认关闭时接口不存在。文字入口只需要项目核心依赖；`.[hermes]` 空 extra 保留兼容。

图片入口额外需要安装 `.[hermes-image]`，并在运行服务的同一用户环境执行 `playwright install --with-deps chromium`。截图浏览器要求沙盒可用，不自动关闭沙盒；镜像或无桌面环境也须自行安装这些依赖。缺少图片组件时，图片任务在调用 AI 前失败，文字入口仍可用。项目不会通过接口下载系统依赖。

`GET /api/hermes/capabilities` 返回接口目录、`output_formats: ["text", "image"]`、题型目录与分数口径。`GET /api/hermes/readiness` 只读默认模型配置与本人活跃任务，不请求模型、不检测登录、不启动截图浏览器；它不代表真实上游可用性。

## 两种抽题入口、相同业务参数

| 请求 | 用途 |
| --- | --- |
| `POST /api/hermes/draw` | 返回中文文字题目说明及报告，无浏览器依赖 |
| `POST /api/hermes/draw/image` | 同样返回文字与报告，额外生成中文题目说明 PNG |

```json
{"score_range": [1200, 1600], "topic": "binary-search"}
```

- `score_range` 必填，两个整数，800～3500，下限不大于上限，采用 CF rating 参考口径。
- `topic` 可省略或为 null；支持 capabilities 中的专题 ID，或题库真实标签，如 `math`。细类沿用已核验的方法适用性证据，不强行用通用标签替代细类。
- 从本人已验证登录的平台筛选，遵守最早日期、已完成、跳过及对战占用规则。其他平台采用带来源的难度区间估计，按区间重叠筛选，不宣称官方等价。未知难度排除。
- 每次只选一道。AI 输出 schema 限定候选题号，读取完整题面后确认适用性；不自动付费换题或重试模型。
- 使用本人在连接与设置保存的默认 AI，业务参数不能指定其他用户、提供方或模型。正常流程包含两次 AI 调用：初选、完整题面阅读并生成中文说明/报告。图片复用同一说明，不额外调用翻译。

### 抽题前同步提交、排除已做题

文字和图片入口都会先同步本人当前已验证平台的提交，再构建候选。首次同步从历史起点读取；完整成功后保存该账号的独立进度，以后按重叠窗口增量读取，不使用“只同步今天”推进的采集游标。CF 大小写题号及 problemset/contest/gym 链接会归一后排除；候选还会排除本站已有尝试、已完成/跳过反馈和对战占用。

同步中可看到 `control.stage: "syncing_submissions"`。分页不完整、访问失败或同步锁正被占用时，在付费 AI 初选前终止；已经读到的官方记录保留用于后续排除，独立进度不推进，不把未知历史当作“从未做过”。切换平台凭据后正在执行的抽题也会停止交付，防止混用身份。

`result.history_sync` 提供本次各账号的读取范围、数量及覆盖说明。此处完整是对应可读取来源的分页完整：CF 私有 Gym/Mashup、洛谷不可见记录和牛客未归档比赛等并不保证覆盖；AtCoder 的公开提交索引可能延迟。不会为了通过而绕过平台防护或编造“未做过”。已经完整同步但没有候选时，会明确报无符合条件题目。

`GET /api/hermes/candidates?minimum=1200&maximum=1600&topic=binary-search` 可只读预览最多 60 道当前缓存候选及难度证据。不检测登录、不刷新题库、不调用模型、不锁定候选；count 是该预览数量，不是全库总数，正式抽题仍会重新核验。

## 认证、限流与幂等

沿用正常登录。多人模式取得本人 HttpOnly 会话后，通过 `/api/auth/status` 获取 CSRF。写请求附带：

```text
X-ACM-Request: 1
X-CSRF-Token: 本会话 csrf_token
Content-Type: application/json
```

两个抽题入口还须附带 `Idempotency-Key`，值为 32 位小写 UUID 十六进制字符串。会话正常通过 Cookie 传输；如果发送 Origin，须是站点自身来源。单用户模式沿用访问令牌。不要把 Cookie、密码、AI Key 放入业务 JSON、URL、日志或 Git。

文字、图片抽题共用已有每用户 AI 限流、队列与全局任务预算。急停不占收费任务额度，仍受正常认证、CSRF 和一般请求限流约束。每用户同类任务不可并行，停止清理完成前也不可启动另一个同类任务。

调用方保留本次幂等键。同一键、同一参数及输出方式重放返回原任务；更换参数或输出方式冲突返回 409。失败或中断任务不会因重放再次付费。响应丢失时先 `GET /api/hermes/requests/{request_key}` 恢复，不要另发新抽题。

## 任务结果与进度

POST 返回 `{ "id": "任务UUID", "status": "queued" }`。轮询 `GET /api/hermes/draws/{id}`，状态为 queued/running/completed/failed/interrupted；生成预算最长 600 秒。GET 不调用 AI。

`control.stage` 和 `stage_title` 提供当前阶段：配置检查、图片组件检查（仅图片）、登录检查、历史提交同步、AI 选题、完整题面读取、AI 说明与报告、资格复核、图片生成（仅图片）等。阶段不代表百分比。

成功的 `result` 包含：

- `format`：text 或 image。
- `problem`、`selection_reason`：真实题号、链接、难度证据与选择理由。
- `problem_explanation`：可直接告诉用户的中文标题、条件/变量/目标、输入输出、约束、注意事项及官方链接。它是 AI 阅读说明，不是官方逐字译文，不含解题步骤。
- `problem_explanation.samples`：直接附加同一官方题面的样例，保留空格、换行，禁止模型改写。
- `report`：独立的完整解题报告，含思路、正确性、复杂度、样例解释、陷阱、其他方法与限制；包含剧透。
- `report_provenance`：实际模型来源。配置或题面在生成期间变化时，拒绝交付混用结果。
- `notice`：AI 静态阅读可能有误，未运行或提交代码，不计签到。

两种入口均将说明与报告保存在本人私有任务中，不创建共享翻译。依赖无法理解的图片或缺失内容时不能编造题意，应判定无法确认适用性。

图片结果另含 `screenshots: [{"index":0,"url":"/api/hermes/draws/任务UUID/screenshots/0"}]`、`expires_at` 及 `artifacts_expired`。图片只包含题目说明、约束、原样例及原题链接，不包含解题报告。每页 1020×最多3072，最多16页；过长时明确失败，不截断交付。渲染仅使用本地资源，不访问任意远程图片或网页。原题插图请结合官方链接核对。

PNG 仅本人可读，30 分钟后或进程重启/关闭时清理；过期/缺失返回 410，文字说明与报告仍可读。下载需要正常认证。文字任务无截图目录或图片结果。历史旧格式返回 `legacy_report`、原报告及 `problem_explanation: null`，不自动收费补全；旧截图不可用。

## 取消、急停与任务列表

下列 POST 均发送空 JSON `{}`，不需要幂等键：

| 接口 | 范围 |
| --- | --- |
| `POST /api/hermes/draws/{id}/cancel` | 停止本人一项抽题任务 |
| `POST /api/hermes/requests/{request_key}/cancel` | 按已存在的请求键停止，适合抽题响应丢失时 |
| `POST /api/hermes/stop` | 停止此刻本人全部 Hermes 抽题任务；不影响爬取、交题、其他用户或未来新请求 |

未知任务/请求或不属于本人返回 404。完成或失败后取消不删除已有结果；重复取消不会再次中断清理。stop 返回 `requested_count` 和各任务控制状态；未匹配任务返回零。它是当前任务的快照，不能用来预先阻止尚未到达服务器的抽题请求。

取消后立即将任务标为 interrupted、清除未交付结果并持久化停止标记，后到的模型响应不能重新写成成功。区分：

- `control.stop_state: "stopping"`：已请求停止，本地协程仍在清理。
- `control.stop_state: "stopped"`：本地协程已结束，或前一进程已退出。
- `cancel_requested: false` / `stop_state: "not_requested"`：未请求取消。

急停会取消本地流程；已知官方 AI turn 会尽力发送其专属中断请求。已发送的 HTTP、线程内题面读取或上游推理可能继续完成，不能保证撤销上游工作或费用；`upstream_cancellation: "best_effort"` 不代表上游确认取消。恢复或重放已中断任务不会自动续跑。

`GET /api/hermes/draws?status=interrupted&limit=20&offset=0` 返回本人任务元数据与阶段，不返回完整报告。limit 为1～100、offset 为0～10000；status可省略。停止后轮询单任务直至 stop_state=stopped，再决定是否用新键发起另一次付费抽题。

## SDK 示例

SDK 使用已登录且设置好 CSRF 请求头的 `httpx.Client`，不保存密码或 AI Key、不执行报告、不跟随结果任意 URL、不自动重试收费请求。

```python
from acmhermes.client import HermesClient

api = HermesClient(session)
key = api.request_key()  # 在 Hermes 私密任务上下文保留
job = api.draw((1200, 1600), topic="binary-search", request_key=key)
# 图片模式：api.draw_image((1200, 1600), request_key=另一个新键)
job = api.wait(job["id"])
if job["status"] == "completed":
    question = api.explain(job)  # 可直接回复用户，不含解题步骤
    solution = api.solution(job)  # 完整报告，单独使用
    folder = api.download(job, "./training-bundles")
    # 保存 result.json/problem.txt/solution.txt；图片任务额外下载私有 PNG
    # 不需要图片时使用 include_images=False

# Hermes 收到停止指令时：
api.cancel(job["id"])  # 或 api.cancel_request(key)，或 api.stop_all()
job = api.wait_stopped(job["id"])  # 区分已请求停止与本地已停止
# 超时只继续查状态，不自动重启任务。
```

其他方法：`status(id)`、`recover(key)`、`list_jobs(...)`、`readiness()`、`candidates((low, high), topic)`。Hermes 的具体 skill/工具注册由接入方完成，本项目提供 API 与 SDK。

# Hermes 可选接入模块

`acmhermes` 默认不加载、不开放接口。启用后，服务从符合条件的真实题库随机抽一道题，使用本人的默认 AI 阅读完整官方题面，返回中文题目说明与独立解题报告。提供文字、图片两个入口及任务控制接口；不运行代码、不代交、不修改训练任务、签到或榜单。

## 按需启用

设置 `ACM_ENABLE_HERMES=true`，由服务所有者重启生效。默认关闭时接口不存在。文字入口只需要项目核心依赖；`.[hermes]` 空 extra 保留兼容。

图片入口额外需要安装 `.[hermes-image]`，并在运行服务的同一用户环境执行 `playwright install --with-deps chromium`。截图浏览器要求沙盒可用，不自动关闭沙盒；镜像或无桌面环境也须自行安装这些依赖。缺少图片组件时，图片任务在调用 AI 前失败，文字入口仍可用。项目不会通过接口下载系统依赖。

`GET /api/hermes/capabilities` 返回接口目录、`output_formats: ["text", "image"]`、题型目录与分数口径。`GET /api/hermes/readiness` 只读默认模型配置与本人活跃任务，不请求模型、不检测登录、不启动截图浏览器；它不代表真实上游可用性。

结果可靠交付支持保存后的明确回执，未收到回执则重发同一结果，不重新抽题或调用AI。新接收端使用交付流和ACK接口；原查询接口仍兼容。接入agent务必先读[结果回执协议](HERMES_DELIVERY_PROTOCOL.md)，按消息编号持久去重，在保存成功后才确认。SDK提供 `receive`、`delivery_status` 和 `acknowledge`。

## 两种抽题入口、相同业务参数

| 请求 | 用途 |
| --- | --- |
| `POST /api/hermes/draw` | 返回中文文字题目说明及报告，无浏览器依赖 |
| `POST /api/hermes/draw/image` | 同样返回文字与报告，额外生成中文题目说明 PNG |

```json
{"score_range": [1200, 1600], "topic": "binary-search", "exclude_ids": ["codeforces:4-A"]}
```

- `score_range` 必填，两个整数，800～3500，下限不大于上限，采用 CF 综合训练估计口径；官方原始 rating 不被覆盖。两端相等表示目标分数附近，默认±100且裁剪到800～3500；如 `[1800,1800]` 实际匹配1700～1900。两端不同是明确区间，保持原边界。
- `topic` 可省略或为 null；支持 capabilities 中的专题 ID、中文类别或题库真实标签，如 `math`。用 `&`、英文/中文逗号连接表示交集，用 `|` 连接表示并集。`&` 和逗号优先于 `|`，不接受括号；例如 `dp,博弈论`、`dp&博弈论` 都要求同时满足，`dp|博弈论` 满足其一，`dp|math&games` 表示 DP 或同时满足数学与博弈。最多8个类别，每个80字符；细类沿用已核验的方法适用性证据，不强行用通用标签替代细类。
- `exclude_ids` 可省略，最多 10000 个规范题号，如 `codeforces:4-A`。这是额外排除清单，不替代服务端的历史提交和已抽取记录。适合 Hermes 将本地记忆在抽题前提交；不要等付费报告生成后再发现重复、自动收费重抽。
- 从本人已验证登录的平台筛选，遵守最早日期、已完成、跳过及对战占用规则。其他平台采用带来源的难度区间估计，按区间重叠筛选，不宣称官方等价。未知难度排除。
- 每次只选一道。服务使用操作系统随机源，先形成最多60道候选，然后先抽平台、再在该平台内按适配度、统计样本支持和Gym优先分抽题。各可用平台基础权重相同，不按题量分配概率；最近8次成功结果中出现越多的平台权重越低，上次平台再乘0.5，权重公式为1000/(1+最近出现次数)，取正整数。单平台内部等权时均匀随机。AI 的 schema 只允许这道题号，不能替换题目。此策略不表示所有平台全库等概率。正常完成的抽取会持久记录并从后续新 roll 中排除。
- 使用本人在连接与设置保存的默认 AI，业务参数不能指定其他用户、提供方或模型。正常流程先取得完整官方题面，再进行两次AI调用：对系统抽中的单题确认理由、阅读题面并生成说明/报告。题面读取失败时在AI调用前换其他平台；报告明确不适合时，最多换一个平台再生成（每个任务最多两份报告，仍受600秒总预算约束）。AI提供方认证、额度、网络或生成报错不靠换OJ盲目重试。图片复用同一说明，不额外调用翻译。

### 抽题前同步提交、排除已做题

文字和图片入口都会先同步本人当前已验证平台的提交，再构建候选。首次同步从历史起点读取；完整成功后保存该账号的独立进度，以后按重叠窗口增量读取，不使用“只同步今天”推进的采集游标。CF 大小写题号及 problemset/contest/gym 链接会归一后排除；候选还会排除本站已有尝试、已完成/跳过反馈和对战占用。

同步中可看到 `control.stage: "syncing_submissions"`。某个平台分页不完整或访问失败时，该平台本轮排除，继续同步其他平台；已经读到的记录保留用于排除，失败进度不推进，不把未知历史当作从未做过。只有所有平台均不可用，或共享同步锁正被占用时，才在AI调用前停止。切换平台凭据后正在执行的抽题也会停止交付，防止混用身份。

`result.history_sync` 提供本次各账号的读取范围、数量及覆盖说明。此处完整是对应可读取来源的分页完整：CF 私有 Gym/Mashup、洛谷不可见记录和牛客未归档比赛等并不保证覆盖；AtCoder 的公开提交索引可能延迟。不会为了通过而绕过平台防护或编造“未做过”。已经完整同步但没有候选时，会明确报无符合条件题目。

### Codeforces 题库同步

本人已验证平台包含 CF 时，首次抽题或距离上次本模块成功同步超过 6 小时，会自动读取官方 `problemset.problems` 和 `contest.list(gym=false)`。只将已结束比赛中具有 rating、可靠日期的公开题目用于 CF 抽题，未评级题和所有 Gym 并不保证覆盖。此前的少量题面缓存不能充当已同步完整公开列表的证据。

`control.stage: "syncing_catalog"` 显示同步阶段，成功结果中的 `catalog_sync` 提供来源、数量、更新时间与缓存状态。响应异常、截断或缺少可靠日期时保留旧缓存，本轮跳过CF并尝试其他可用平台，不使用不完整题库凑题；未验证 CF 的用户仍只能从其本人其他已验证平台筛选，不借题库同步放宽登录要求。此路径不发送平台 Cookie 或 API Key。

`GET /api/hermes/candidates?minimum=1200&maximum=1600&topic=binary-search` 可只读预览最多 60 道当前缓存候选及难度证据。不检测登录、不刷新题库、不调用模型、不锁定候选；count 是该预览数量，不是全库总数，正式抽题仍会重新核验。`diagnostics.candidate_count`为按相同规则统计的完整缓存候选量；每个平台返回缓存总量、可用日期/专题元信息覆盖和互斥的排除数量（登录不可用、已尝试/占用、分数、未知/过早日期、屏蔽标签、专题、客户端/已交付记录）。GET仍不请求网络或模型。

查询与正式抽取共用同一表达式匹配器，并返回 `topic_query` 的 `any_of_all` 分组用于解释筛选。SDK 直接传 `topic="dp&博弈论"`；HTTP 查询须用客户端的 `params` 编码（裸 `&` 会被解释为查询参数分隔符）。原单类别请求和已保存的幂等请求不重写，恢复旧任务仍返回原任务。

### 简略题库与报告参考

题库同步优先批量获取链接、标签、原始分数、日期及平台公开的统计摘要；不逐题抓题面或源码，不调用 AI。抽取先按这些简略元信息过滤，再有界补充比赛统计、读取抽中的完整题面。统计的来源、口径、缺失字段及分页覆盖见 [简略题库策略](HERMES_CATALOG_BRIEFS.md)。

完整题面读取后，可匿名收集同题公开官方题解或 AC 代码，与初选理由调用并行。公开材料受严格题号校验和时间/请求预算限制；防护、不可读或没有材料时继续原有题面分析，不为参考获取失败重抽或额外付费。报告输入包含参考摘录，交付的 `report_references` 只列来源元信息，不回传完整参考程序。具体范围见 [公共参考材料](HERMES_REFERENCES.md)。

### 比赛统计、Gym 与现场参考

`checking_difficulty` 阶段每次至多采样三场普通 CF 比赛，优先抽查官方分数贴近目标的比赛，也允许核查其他分数；发现阶段不按综合分数提前拒绝。读取完整官方参赛榜，排除练习、虚拟参赛、幽灵队和判题未完成记录。分母为同场正式参赛个人/队伍，分子为通过该题的个人/队伍；提交人数和计分提交次数另外记录，不混为同一指标。旧题的累计 AC 人数不冒充通过率。继续遵守用户设置的最早出题日期，不根据比赛编号猜年份。

至少 100 个同口径样本、有完整可靠来源且题号/比赛一致时，使用平滑通过比例作**未经共同选手标定的启发式估计**：`p=(通过人数+1)/(参赛人数+2)`，经验分为 `1500+300*log2((1-p)/p)`，不凑整百并限于800～3500；综合分用未取整的经验值按经验55%、官方45%加权，无官方分时仅用经验估计。返回值保留两位小数（如1836.24），计算不会先取整百。单点目标匹配±100邻域，明确区间保持原边界；例如官方1800但通过比例85%的合成样本综合估计1250，不再进入1800附近候选。选手水平、赛制和队伍结构不同会造成偏差，这不是官方评级或已校准难度模型。统计不足的普通赛明确保留官方 rating；未知难度 Gym 排除。

有限统计采样不会封锁其他所有CF候选：有足量统计的题继续严格按综合分筛选，已证明偏易的题不能按原官方分回流；缺统计但具有官方rating的普通CF题保留官方依据参与。未知难度Gym仍不编造分数。这样不会因为只采样三场就把整个CF平台挤出候选。`result.contest_evidence_sync` 说明本次核对数量、公开比赛编号、Gym/现场来源状态与覆盖限制。不是每次遍历所有比赛。

Gym 使用本人已验证 CF 绑定的现有 API Key/Secret 读取可见比赛，每次随机发现至多两场；未绑定时返回 `gym_api_required`，不借用其他账号密钥。签名请求只发往 CF，不记录签名或原始榜单。Gym 没有官方 rating 时仍须满足上述统计门槛；合格后优先分额外增加0.12，仅提高抽取概率，不抬高难度，不计签到/榜单分。

Gym 官方样本不足时，每次至多尝试一个现场来源：[XCPCIO](https://board.xcpcio.com/) 或 [ICCPC](https://rank.iccpc.cn/)。只在比赛名称、日期（时区允许一天差异）和整套题号全部匹配后采纳，不能模糊匹配同名不同年比赛。现场榜只计正式队伍，未结束/封榜或结果不完整则不用。外部站无法访问或无法确认映射时明确保留限制，不伪造现场证据。正缓存6小时，失败缓存10分钟；只保存公开聚合与元信息，不保存队名、选手名或个人提交历史。

`problem.score_evidence` 提供官方分、经验分、综合估计、来源、参赛/提交人数、通过率、题龄、计数单位、置信度及优先分组成。报告提示词必须尊重这些限制，不把 Gym 加分说成更难，不把统计估计说成官方定级。

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

**一次新 roll 必须生成一个新键。** 使用旧键恢复得到同一道题是幂等恢复，不是新抽题。同一任务反复查询也不会触发重新随机选择。

## Hermes 本地题号记忆

`GET /api/hermes/problem-memory?limit=500` 返回当前用户私有工作区的题号记录：

```json
{
  "scope": "不透明的当前用户工作区范围",
  "revision": "本次记录快照版本",
  "items": [
    {"problem_id": "codeforces:4-A", "attempted": true, "solved": true, "drawn": false, "skipped": false}
  ],
  "next_after": null
}
```

`attempted` 表示已有练习或提交记录，`solved` 表示已做出，`drawn` 表示已抽取，`skipped` 表示已跳过；同一道题可有多个标志。接口只返回题号与这些标志，不返回 Cookie、Key、密码、源码或报告。

有下一页时，将 `next_after` 作为下一次 `after`，并携带第一页面的 `revision`。拉取期间记录变化返回 409，应重新执行只读同步；不要拼接不同版本的页面。`scope` 不同时不得共用本地缓存，换用户或站点后重新同步。同一用户切换所绑定的平台账号时，工作区保留既有题号并保守排除；Cookie 续期不会清空历史。

SDK 的 `sync_memory(path)` 完整分页成功后，以私有文件权限原子替换指定 JSON；失败保留旧文件。它只同步已有服务器记录，不代替抽题时的官方历史同步。`memory_ids(path)` 会先读取当前 scope，身份不符直接拒绝；`draw(..., memory_path=path)` / `draw_image(..., memory_path=path)` 自动执行该检查并发送额外排除题号，且不自动重试付费任务。服务器自身仍会排除历史记录与已抽取题；本地缓存是第二层保护。

## 任务结果与进度

POST 返回 `{ "id": "任务UUID", "status": "queued" }`。轮询 `GET /api/hermes/draws/{id}`，状态为 queued/running/completed/failed/interrupted；生成预算最长 600 秒。GET 不调用 AI。

`control.stage` 和 `stage_title` 提供当前阶段：配置检查、图片组件检查（仅图片）、登录检查、历史提交同步、CF 题库同步、比赛与 Gym 难度核对、系统随机抽题、AI 说明与报告、资格复核、图片生成（仅图片）等。阶段不代表百分比。

成功的 `result` 包含：

- `format`：text 或 image。
- `problem`、`selection_reason`：真实题号、链接、难度证据与选择理由；多平台时 `selection_strategy` 为 `system_platform_balanced_random_without_replacement`；单平台内部仍为 `system_random_without_replacement` 或 `system_weighted_random_without_replacement`。均为系统随机、交付后去重。
- `problem_explanation`：可直接告诉用户的中文标题、条件/变量/目标、输入输出、约束、注意事项及官方链接。它是 AI 阅读说明，不是官方逐字译文，不含解题步骤。
- `problem_explanation.samples`：直接附加同一官方题面的样例，保留空格、换行，禁止模型改写。
- `report`：独立的完整解题报告，含思路、正确性、复杂度、样例解释、陷阱、其他方法与限制；包含剧透。
- `report_provenance`：实际模型来源。配置或题面在生成期间变化时，拒绝交付混用结果。
- `requested_score_range`、`effective_score_range`：原始分数要求与本轮实际匹配区间。`problem.score_evidence.estimated_rating` 是推测评分，可以带小数；客户端不可按整百分箱或当作官方 rating。其他平台仍提供注明来源的不确定区间，不伪造精确分值。
- `platform_candidate_counts`、`platform_weights`：本轮初始候选数量和实际平台权重，平台概率不与候选数量成比例。
- `platform_fallbacks`、`report_attempts`：本轮跳过的平台及固定原因、生成报告次数；不包含上游正文。
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
api.sync_memory("./private/hermes-problem-memory.json")  # 此文件不要放入 Git 或公开目录
key = api.request_key()  # 在 Hermes 私密任务上下文保留
job = api.draw((1200, 1600), topic="binary-search", request_key=key,
               memory_path="./private/hermes-problem-memory.json")
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

其他方法：`status(id)`、`recover(key)`、`list_jobs(...)`、`readiness()`、`candidates((low, high), topic)`、`memory(...)`。每次新 roll 使用 `request_key()` 生成新键；网络超时则保留原键并恢复。Hermes 的具体 skill/工具注册由接入方完成，本项目提供 API 与 SDK。

## 四平台题库覆盖与分批续传

通用「同步平台题库」仍使用本人私有题库，不把其他用户的记录合并进排重。CF读取带rating的已结束公开比赛全量索引；AtCoder读取Kenkoooo公开题目索引、估计/分值及比赛时间，并补充官方ACL练习赛。AtCoder第三方比赛日期明确标注`kenkoooo.contests.start_epoch_second`，可参与日期筛选，已有官方日期优先保留；不将第三方日期伪称官方核实，不猜测缺失时间或专题。

洛谷普通列表、模板列表各每批最多8页，牛客每批最多8页；成功存储后持久保存各自分页游标，再次同步从后续页继续，直到列表结束后进入下一轮。失败不前移未读取页面，旧缓存保留。分页截断或部分失败标记partial，不再显示全量成功；返回本批`count`、累计`cached_count`、来源提供时的`total_count`及`next_page`，页面展示续传与来源限制。

这些是元信息同步，不代表所有题具有可比较难度、发布日期或专题证据。牛客/洛谷官方列表没有给出的日期保持未知，在设置日期下限时仍会排除；AtCoder无官方专题标签，需真实题名/已有阅读方法证据，不能为扩大题量给整个平台虚构博弈论标签。

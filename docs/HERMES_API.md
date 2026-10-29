# Hermes 可选接入模块

`acmhermes` 是独立命名空间，默认不加载、不开放接口。启用后，Hermes 请求用户已配置的 AI 从真实题库选一道题，阅读完整官方题面，直接返回可告诉用户的中文题目说明及独立解题报告。后续学习流程交给 Hermes；此模块不运行代码、不代交、不修改任务、签到或榜单。

## 按需启用，无需浏览器

安装正常 ACM Lab 项目后，设置 `ACM_ENABLE_HERMES=true` 并重启自己管理的服务即可启用。保留 `.[hermes]` 安装写法兼容旧接入说明，但此 extra 已不再增加依赖。

不需要 Chromium、Playwright、Markdown 渲染器、截图组件或额外翻译服务，不额外翻译整页。其他功能使用的浏览器登录依赖不受影响。Docker 的 Compose 配置会传递该开关，默认仍为 false。

`GET /api/hermes/capabilities` 返回 `output_format: "text"`、题型目录及分数口径。默认关闭时接口不存在。

## 两个业务参数

`POST /api/hermes/draw`

```json
{"score_range": [1200, 1600], "topic": "binary-search"}
```

- `score_range` 必填，两个整数，下限不大于上限，800～3500，采用 CF rating 参考口径。
- `topic` 可省略或为 null；支持 capabilities 中的专题 ID，或题库实际存在的标签，如 `math`。细类沿用已核验的方法适用性证据，没有证据时不把通用标签强行当作细类，不因找不到题而放宽限制。
- 从本人已验证登录的平台筛选，遵守已有最早日期、已完成、跳过及对战占用规则。CF 使用官方 rating；其他平台沿用带来源的宽区间估计，按区间重叠筛选，不宣称官方等价。无可靠难度证据的题不会入选。
- 一次请求只选一道；AI 输出 schema 限定精确候选题号，服务端再次核对。读取完整官方题面后还需确认适用性，不自动付费换题或重试模型。

调用使用当前用户在连接与设置保存的默认 AI 通道及模型，不能通过业务参数指定其他用户或提供方。正常生成包含两次 AI 调用：元信息初选、完整题面阅读与中文说明/报告。题目说明不包含解题步骤；完整解题报告另放在 report，调用者需注意剧透，不影响工作台原有的小提示策略。

## 认证、限流和幂等

沿用 ACM Lab 正常认证。多人模式先通过已有登录接口取得本人的 HttpOnly 会话，再读 `/api/auth/status` 取得 CSRF。写请求附带：

```text
X-ACM-Request: 1
X-CSRF-Token: 本会话的 csrf_token
Idempotency-Key: 本次请求的32位小写UUID十六进制值
Content-Type: application/json
```

会话使用正常 Cookie 传输。非浏览器客户端可以调用正常登录接口；若发送 Origin，必须是该 ACM Lab 站点自身来源。单用户部署沿用现有访问令牌。不新增绕过认证的公共入口；沿用每用户 AI 限流、后台队列和全局任务预算。不要将登录 Cookie、密码或 AI Key 填在业务 JSON、URL、日志或 Git 中。

调用方应保存本次 `Idempotency-Key`。同一键、同一参数重放返回原任务；不同参数冲突返回 409。失败任务也不会因重放自动再次付费。响应丢失时先查询 `GET /api/hermes/requests/{request_key}`，只读恢复不会重新选题。

## 文本结果

POST 返回 `{ "id": "任务UUID", "status": "queued" }`。轮询 `GET /api/hermes/draws/{id}`，状态为 queued/running/completed/failed/interrupted。最长生成预算 600 秒，失败原因在 error 中；GET 不调用 AI。

成功任务 `result.format` 为 `text`，包含：

- `problem`、`selection_reason`：原题号、链接、分数/估计证据及选择理由。
- `problem_explanation`：可以直接告诉用户的中文题目说明，包含中文 title、description（条件、变量含义、操作与目标）、input_format、output_format、constraints、notes，以及 problem_id、language、original_url。它是 AI 阅读说明，不是官方逐字译文。
- `problem_explanation.samples`：原题的输入/输出样例，由服务器直接附加，保留空格与换行，不交由模型重写。
- `report`：独立的完整解题报告，包括题意概述、约束、算法步骤、正确性论证、复杂度、样例解释、实现陷阱、其他思路与限制。
- `report_provenance`：本次阅读说明和报告实际使用的模型来源。生成期间配置或题面变化会拒绝交付混用结果。
- `notice`：AI 静态阅读可能出错，未执行或提交代码，不计签到。

不再返回 screenshots、artifact_id、expires_at 或截图下载链接，也不创建个人整页翻译或图片缓存。中文说明与报告作为私有 AI 任务结果保存在本人空间，不上传共享翻译库。题意依赖无法理解的图片、公式或缺失内容时应报告无法确认适用性，而非编造题意。

旧任务的原解题报告仍可读取，返回 `format: "legacy_report"`、`problem_explanation: null` 及说明，不自动重新调用 AI 补全。旧截图路由在核对任务所属用户后返回 410；其他用户不能借该入口查看任务。升级进程清理旧版本短期截图，遵守原有“重启后清理”的约定。

## Hermes 直接回复用户

轻量 SDK 使用已经登录 ACM Lab、且已设置 CSRF 请求头的 `httpx.Client`。SDK 不保存凭据，不执行报告，不跟随结果中的任意 URL，也不自动重试收费请求。

```python
from acmhermes.client import HermesClient

api = HermesClient(session)
key = api.request_key()  # 在 Hermes 自己的私密任务上下文保留
job = api.draw((1200, 1600), topic="binary-search", request_key=key)
job = api.wait(job["id"])
if job["status"] == "completed":
    question = api.explain(job)  # 中文题目说明，可直接告诉用户；不含解题步骤
    solution = api.solution(job)  # 完整解题报告，单独使用
    # 可选：保存 result.json、problem.txt、solution.txt，无额外网络请求
    folder = api.download(job, "./training-bundles")
else:
    print(job["error"])  # 不自动再次 draw；响应丢失可 recover(key)
```

SDK 不处理用户的 AI Key：AI 配置只存在 ACM Lab 内。Hermes 的具体 skill/工具注册由接入方完成，本项目提供 API 和 SDK。

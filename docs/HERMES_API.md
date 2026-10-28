# Hermes 可选接入模块

`acmhermes` 是独立命名空间，默认不加载、不开放接口。正常使用 ACM Lab 无需安装截图依赖，也不需要安装或运行 Hermes。启用后，Hermes 可请求用户已配置的 AI，从真实题库选一道题，阅读完整官方题面，生成完整解题报告和中文题面截图。后续学习流程交给 Hermes；此模块不运行代码、不代交、不修改任务、签到或榜单。

## 按需安装

```bash
python -m pip install '.[hermes]'
python -m playwright install chromium
export ACM_ENABLE_HERMES=true
```

重启自己管理的 ACM Lab 服务即可启用。Chromium 系统依赖及中文字体需要运行环境提供；若缺少依赖，可由管理员按环境安装 `playwright install-deps chromium` 及中文字体。保持 Chromium 沙盒启用；不会通过禁用沙盒绕过启动失败。Docker 用户可在自己的镜像中安装该 extra、浏览器和系统依赖，不能只设置开关。

`GET /api/hermes/capabilities` 返回接口能力、截图组件是否安装、题型目录和分数口径。每次生成会在任何收费 AI 调用前实际检查浏览器能否启动；失败可在任务中看到明确原因。默认关闭时接口不存在。

## 两个业务参数

`POST /api/hermes/draw`

```json
{"score_range": [1200, 1600], "topic": "binary-search"}
```

- `score_range` 必填，两个整数，下限不大于上限，800～3500，采用 CF rating 参考口径。
- `topic` 可省略或为 null；支持 capabilities 中的专题 ID，或题库实际存在的标签，如 `math`。细类没有可核验证据时不会把通用标签强行当作该细类，也不会因找不到题而放宽限制。
- 从本人已验证登录的平台筛选，遵守已有最早日期、已完成、跳过及对战占用规则。CF 使用官方 rating；其他平台沿用现有带来源的宽区间估计，按区间重叠筛选，不宣称官方等价。无可靠难度证据的题不会入选。
- 一次请求只选一道；AI 只能选真实候选。读取完整官方题面后还需确认适用性，才生成报告和翻译；不自动付费换题或恢复失败的模型请求。

调用会使用当前用户在连接与设置保存的默认 AI 通道和模型，不能在参数中指定其他用户、模型或提供方。一次生成包括选题、完整报告及可能多批的 AI 翻译，可能产生多次模型调用费用。生成完整报告属于明确的剧透请求，不影响工作台原来的小提示策略。

## 认证、限流和幂等

沿用 ACM Lab 正常认证。多人模式先通过已有登录接口取得本人的 HttpOnly 会话，再读 `/api/auth/status` 取得 CSRF；写请求附带：

```text
X-ACM-Request: 1
X-CSRF-Token: 本会话的 csrf_token
Idempotency-Key: 本次请求的32位小写UUID十六进制值
Content-Type: application/json
```

会话使用正常 Cookie 传输。非浏览器客户端可以正常调用登录接口；若发送 Origin，必须是该 ACM Lab 站点自身的来源。单用户部署沿用现有访问令牌。只对自己的空间开放，不新建绕过身份验证的公共接口；沿用每用户 AI 请求限流、后台队列和全局预算。不要将登录 Cookie、密码或 AI Key 填在业务 JSON、URL、日志或 Git 中。

调用方必须保存本次 `Idempotency-Key`。同一键、同一参数重放返回原任务；不同参数冲突返回 409。失败任务也不会因重放自动再次付费。如请求响应丢失，先查询 `GET /api/hermes/requests/{request_key}`；只读查询不会重新抽题。

## 异步返回和下载

POST 返回 `{ "id": "任务UUID", "status": "queued" }`。轮询 `GET /api/hermes/draws/{id}`，状态为 queued/running/completed/failed/interrupted。最长生成预算 600 秒，失败原因在 error 中；GET 不调用 AI。

成功任务的 `result` 包括：

- `problem` 与 `selection_reason`：原题号、链接、分数/估计证据、选题理由。
- `report`：题意、实际约束、算法步骤、正确性论证、时间空间复杂度、样例解释、实现陷阱、其他思路与限制，以及精确题号。
- `report_provenance` 与 `translation_provenance`：实际模型来源；生成期间配置改变会拒绝混用结果。
- `screenshots`：按页排列的 `{index,url}`；通过同一认证访问下载 PNG，最多 16 页，不截断后假称完整。
- `image_links`：图片原链接。截图不自动请求外部图片，防止服务器被题面 URL 带到内网；截图本身也保留链接文字。
- `expires_at` 与 `artifacts_expired`：个人中文题面截图约 30 分钟后清理，重启也清理；过期下载返回 410。只保留私人解题报告和原题元数据，不持久保存个人翻译正文，不自动上传共享译文。

中文截图在本地无网络浏览器渲染，包含样例及 KaTeX 公式。AI 报告为静态分析，不是官方题解、执行结果或通过证明；题面依赖无法理解的图片等材料时应拒绝确认适用性，而非编造答案。

## Hermes 调用示例

模块附有轻量 Python SDK，不保存凭据，不跟随模型提供的 URL，不执行报告。以下 `session` 应为 Hermes 已按上述方式登录 ACM Lab、并设置 CSRF 请求头的 `httpx.Client`：

```python
from acmhermes.client import HermesClient

api = HermesClient(session)
key = api.request_key()  # 在 Hermes 自己的私密任务上下文保留，响应丢失时用于 recover(key)
job = api.draw((1200, 1600), topic="binary-search", request_key=key)
job = api.wait(job["id"])
if job["status"] == "completed":
    folder = api.download(job, "./training-bundles")  # result.json 和各页 PNG
else:
    print(job["error"])  # 固定安全提示；不要自动重新 draw
```

SDK 不处理用户的 AI Key：AI 配置仅存在 ACM Lab 内。与 Hermes 的具体 skill/工具注册由接入方完成，本项目提供上述 API 和 SDK。

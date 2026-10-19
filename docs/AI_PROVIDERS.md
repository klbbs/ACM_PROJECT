# AI 提供方配置

0.7.0 将 API 接入扩展为提供方预设、协议适配和模型参数。训练报告、选题、小提示和代码复盘共用这套配置；ChatGPT 官方设备码登录仍是独立通道。

## 从网页配置

1. 进入「连接与设置 → AI API 接入」，选择提供方。系统填入 API 基础地址与协议。
2. 到该提供方的官方控制台创建自己的 API Key，确认服务区域、余额与模型权限；把 Key 填入密码框。
3. 从官方控制台复制可用的**文本模型 ID**。可以直接填入「默认模型 ID」，也可以点击「保存并读取模型列表」后在输入框候选中选择；选择后再保存。模型目录可能包含图像、嵌入模型等，请选支持文本和 JSON 的模型。
4. 保存配置后，按需点击「测试已保存配置（会计费）」。这会执行一次真实生成和 JSON 解析，页面显示结果、模型及耗时。测试只发送固定的连通性提示，不发送训练数据或源码。请求成功与否都可能按提供方规则计费，项目不估算费用。
5. 点击「使用此通道」，到分析、选题或工作台选择 API Key 通道。可直接使用默认模型、手动填写模型 ID，或刷新模型目录。

**未保存的表单不用于连接测试。** 保存配置不调用生成 API；读取模型列表只访问目录接口，不代表有生成权限。提供方不支持模型列表时，可以直接填写 ID；返回的手工项会明确标记未验证。

## 预设与专用选项

| 提供方 | 预设基础地址 | 协议与专用选项 |
| --- | --- | --- |
| OpenAI | `https://api.openai.com/v1` | Responses；可改 Chat；推理强度，结构化输出 |
| DeepSeek | `https://api.deepseek.com` | Chat；思考开关、推理强度 |
| Anthropic / Claude | `https://api.anthropic.com/v1` | 原生 Messages；独立鉴权和文本块解析 |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | 官方 OpenAI 兼容 Chat；推理强度 |
| 百炼 / Qwen 中国 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | Chat；思考开关 |
| 百炼 / Qwen 新加坡 | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` | Chat；区域 Key 独立，思考开关 |
| Moonshot / Kimi 中国 | `https://api.moonshot.cn/v1` | Chat；推理强度 |
| 硅基流动 | `https://api.siliconflow.cn/v1` | Chat；模型列表和输出参数 |
| 智谱 GLM 按量 API | `https://open.bigmodel.cn/api/paas/v4` | Chat；模型列表和输出参数 |
| 自定义网关 | 用户填写 HTTPS 地址 | 可选 Responses、Chat、Claude Messages；服务器需批准域名 |

模型 ID 不写死在项目中：以账号当前模型目录和控制台为准。表中选项需要具体模型支持；项目不会把所有同名厂商模型视为参数相同。Gemini 使用 Gemini API Key；百炼不同区域的 Key/模型不可混用；GLM Coding Plan、其他编程套餐与按量 API 地址/权益可能不同，请按官方文档修改地址并确认权限。

提供方选项变化时会重置协议相关参数，不会自动调用模型。更改协议时思考开关、推理强度和输出模式也会复位，避免把上一种协议的参数带过去。

## 输出和推理参数

- **按协议默认**：Responses 使用严格 JSON Schema；Chat 使用 JSON 对象；Claude 使用提示词约束。所有结果仍由任务自身的 schema 校验，选题必须来自候选题，提示/复盘继续执行工作台的限制。
- **JSON Schema**：发送对应协议的原生 schema 字段，需要模型支持。
- **JSON 对象**：仅 Responses / Chat；Claude 原生接口请选提示约束或 JSON Schema。
- **仅提示约束**：不发送结构化输出参数，适合网关不支持该参数的情况。模型不返回合法 JSON 时任务失败，不自动修复、重复计费或改用其他模型。
- **思考模式**：DeepSeek 使用 `thinking.type`；Qwen 使用 `enable_thinking`。其他提供方保持模型默认。部分 Qwen 思考模型不支持 JSON 模式，可根据模型文档关闭思考或选择提示约束。
- **推理强度**：OpenAI Responses 使用 `reasoning.effort`；兼容 Chat 使用 `reasoning_effort`。DeepSeek / Kimi 提供 low/high/max 选择，OpenAI / Gemini 提供 low/medium/high；实际支持范围仍取决于模型。
- **输出上限/超时/温度**：可调输出 256–32768 token、超时 30–240 秒；默认温度不发送。推理模型可能拒绝 temperature，或将思考计入输出额度。输出被截断不会保存为报告，请检查参数后自行重试。

只解析最终文本。DeepSeek 的 `reasoning_content`、Claude 的 thinking 块不会成为训练报告或小提示。API 不启用工具调用；本次接入没有引入 shell、文件执行或自动编程代理。

## 密钥与服务器

密钥只写入当前用户数据目录的 `ai-secrets.json`，文件权限为 600。浏览器只收到是否配置，读接口不返回密钥。密钥文件不在发布包或 Git 内，属于管理员需要单独备份的私有文件；它不是加密密钥库。

密钥按**完整 API 基础地址**隔离。修改地址不会复用其他地址的 Key；切回已有地址并保存，可以继续使用该地址之前保存的 Key。只改模型或参数、Key 留空时保持该地址的原密钥。「清除当前地址密钥」显式清空该地址；其他提供方的密钥保留。旧版单密钥在第一次保存时自动绑定旧配置地址迁移。

多人模式成员间的配置、密钥、连接测试任务分别隔离，不继承宿主机 `OPENAI_API_KEY`。个人模式该环境变量只用于官方 OpenAI 默认地址，不能作为任意网关的公共 Key。请求不跟随重定向；异常信息不展示上游响应正文。

多人部署的 `ACM_AI_ALLOWED_HOSTS` 默认包含表中官方域名；已有 `.env` 中手动设置的白名单保持有效，升级不覆盖。预设旁「需管理员批准域名」表示当前服务器尚未开放该域名。管理员可在 `.env` 中增加官方域名或自己信任的网关域名（逗号分隔，不带协议/路径），然后重建服务：

```dotenv
ACM_AI_ALLOWED_HOSTS=api.openai.com,api.deepseek.com,api.anthropic.com
```

```bash
docker compose up -d --build
```

自定义地址须为 HTTPS，不能填写 IP 内网地址或本机地址。白名单只接受管理员配置；模型调用仍受既有 CSRF、成员限流、每用户/全站后台任务配额保护。

## 失败排查

| 现象 | 检查项 |
| --- | --- |
| 当前地址没有 Key | 保存该地址的 Key；换区域/路径后需重新配置 |
| 401 / 403 | Key、区域、套餐和模型权限 |
| 404 | 基础地址是否重复写了 `/chat/completions`，或模型 ID 错误；基础地址应到版本/网关根路径 |
| 429 | 控制台余额、调用配额、限流 |
| 400 | 模型是否接受输出模式、思考/推理参数、token 上限和 temperature |
| 目录失败但有模型 ID | 直接填写 ID；404/405/501 目录不支持时不会冒充实时目录成功 |
| JSON 或截断错误 | 增大输出上限，按模型文档调整输出模式；连接测试仅证明该次短请求成功 |

不会自动换提供方、换模型或重试收费请求。

## 参考和验证范围

配置流程借鉴 [DeepSeek Harness 的提供方指南](https://github.com/deepseek-ai/deepseek-harness/blob/main/docs/user/guide/providers.md)（阅读版本 `687f282e8625c6b8d11ff58211a90524be2e4317`）：预设与自定义提供方、协议分层、只写凭据、动态目录与手工模型。项目未集成 Harness 运行时或复制其代理执行能力。

协议核对日期：2026-10-19。参考 [OpenAI 结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs)、[DeepSeek 思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[Claude Messages](https://platform.claude.com/docs/en/api/messages/create)、[Gemini 兼容接口](https://ai.google.dev/gemini-api/docs/openai)、[百炼兼容接口](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope)、[Kimi](https://platform.moonshot.cn/docs)、[硅基流动](https://api-docs.siliconflow.cn/docs/userguide/quickstart)、[智谱](https://docs.bigmodel.cn/api-reference)。

适配器、错误处理、密钥迁移、多用户隔离和页面配置用模拟响应/合成密钥测试。未使用用户真实付费 Key 验证各模型的在线权限、价格或所有参数组合；接入后由用户主动执行连接测试。

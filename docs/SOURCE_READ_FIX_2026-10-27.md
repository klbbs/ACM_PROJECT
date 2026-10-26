# 获取本次代码：原问题定位与修复记录

保持版本 0.21.4，不创建版本标签、发布公告或发布包。检查仅覆盖源码读取、账号私密配置及直接受影响的路径。

## 原因与修复

1. 原提交判题 API、本人提交列表和设置页可读，账号、题号和提交号一致。列表却使用 `hiddenSource` 占位符，由官方 `viewableSubmissionIds` 白名单创建源码按钮。旧实现强制寻找普通详情链接，误报记录不一致。纯解析函数已支持这种实际页面形态，同时保留严格身份与精确提交绑定，不执行上游 JavaScript。
2. 修正解析后，网页源码详情仍触发 Cloudflare，源码弹层的 `/data/submitSource` 仍返回 403。补全官方 AJAX 参数及更换传输实现未解决访问限制，不能将页面可读或登录正常视为源码入口可用，也不声称已绕过官方防护。
3. 改用官方已文档化的 `user.status(includeSources=true)` 本人源码接口，使用用户绑定的 API Key / Secret 签名。先在最近 600 条元数据中定位精确提交位置，再只请求该条源码。再次核对提交号、账号、题号、时间和语言；位置变化时拒绝导入其他提交。
4. 实际官方响应为 `sourceBase64`。严格校验 Base64、UTF-8、64 KiB 上限及冲突字段；缺失或无法核验时保留判题记录，不使用编辑器草稿替代。
5. CF 获取新官方源码需绑定 API；公开判题同步无需 API，交题仍使用 Cookie。API 读取可独立于 Cookie 使用，匿名读取会话仅允许官方判题和本人提交 API 的 GET，不能交题或打开其他页面。缓存中已经核验的源码仍可查看。
6. 连接与设置加入 Key / Secret 成对填写、保留和清除；支持实际官方 40 位密钥及既有 32 位配置。文件权限为 0600，公开接口仅返回是否配置，密钥不回填，不进入日志、Git、模型表示或测试。错误使用固定分类，限流保留重试提示。

官方协议：[API 签名](https://codeforces.com/apiHelp/)、[本人源码读取参数](https://codeforces.com/apiHelp/methods#user.status)。`sourceBase64` 字段和 40 位凭据形态依据本次真实响应确认，文档未列出的字段不靠猜测。

## 定向开发自检

后端 259 项通过：

- `tests/test_codeforces_api_source.py`
- `tests/test_cf_api_account_options.py`
- `tests/test_problem_submissions.py`
- `tests/test_remote_judges.py`
- `tests/test_platform_access_routes.py`
- `tests/test_codeforces_source_reference.py`
- `tests/test_runtime_logs.py::test_runtime_store_allowlists_metadata_and_sets_private_permissions`

前端 `problem-submissions.test.tsx` 与 `cf-api-fields.test.tsx` 共 25 项通过，TypeScript 和生产构建通过。仅有已有的 Starlette/httpx 弃用提示及前端体积提示。

覆盖签名、精确单条源码请求、判题与源码匹配、Base64 与大小边界、并发账号隔离、无 Cookie 读取、无 API 配置拒绝出站、限流、失败保留记录、草稿不变及密钥保留/清除/不泄露。上述自动化平台响应为合成数据，不代表真实 OJ 交题。

子 agent 分别完成 API 源码通道和账号配置，并做定向自检；另一代理及 Hermes 参与只读协议审查。未运行完整项目测试。

## 此次明确授权的线上故障复测

用户明确授权仅对此故障继续线上排查与验证，并允许使用和保存账号已有 API Key；未新建 Key。取得对应原提交的官方源码，严格核验后长度 2364 字节。

应用本次局部修复并仅重启 web 服务，保留原镜像、原 Compose 配置及私密账号配置备份；没有改动其他服务。故障专用镜像通过 `compose.source-read-fix.yaml` 覆盖，后续正常更新应使用相应正式源码，不能只更换基础镜像却保留旧故障覆盖。

在原网站、原题目、原失败提交旁实际点击“获取本次代码”成功：界面出现“重新读取代码”“AI 分析”“载入编辑器”，源码已关联该提交。未加载到编辑器、未改动草稿、未运行用户代码、未调用 AI、未交题或记通过/签到。界面证据只截取记录头和成功提示，不包含源码或密钥。

此复测仅确认本次原始故障。没有验收其他功能，也没有标注新版本。

## 已知范围

源码定位目前有最近 600 条全局提交的上限；更旧的目标会安全失败，不会选另一条替代。其他平台仍沿用原读取方式，本次未做真实平台验收。网页源码入口的访问限制仍可能存在，使用签名 API 不等于解除其防护。

# 工作台一键同步：双分支协作约定

本轮只开发与本地验收，不部署服务器。基础版本 0.19.1；全部功能合并验收后再统一发布新版本。

## A：主代理负责（codex/workbench-submit-core）

- CF / AtCoder 真正提交适配、幂等发送和判题持久化。
- 牛客扫码与 Cookie 二选一；截图所列应用层安全问题修复。
- 本题提交记录同步后端、源码读取与归属校验、多用户隔离。
- 后端测试、最终合并、全套验证、版本公告、GitHub 推送及打包。

## B：Hermes 第二会话负责（codex/workbench-sync-ui）

你只负责前端，文件范围：`frontend/src/workbench.tsx`、新增 `frontend/src/problem-submissions.tsx` / `.css` / `.test.tsx`、新增 `frontend/e2e/problem-submissions.spec.ts`。如原浏览器测试依赖旧手填流程，可以更新相关 `frontend/e2e` 测试。不要编辑 Python、登录/安全组件、版本文件、发布文档、构建配置。你不是唯一开发者；不要回退其他人的变更。

用户需求：在外部平台写完题后，回工作台点击一次「同步本题提交」，自动展示这个账号在当前题的提交，无需手填提交号、重新粘贴代码或勾选代码校对。记录同步不依赖当前编辑器是否为空。有源码才可复盘；不能以当前草稿冒充历史提交源码。

1. 新组件放在原「本次提交与错因复盘」区域，使用当前题号和已配置账号，提供明确同步按钮及账号选项。
2. 同步任务运行中禁用重复点击，轮询本人任务，完成后刷新记录与工作台。显示查询范围、部分成功、没有记录、访问/登录失败；保留旧记录，不以 HTTP 200 代表完整成功。
3. 列表展示提交号、时间、语言、判题及官方链接，AC 绿色、错误红色。官方链接仅允许当前平台的 HTTPS 官方域名。
4. 有 `attempt_id` 时可复用既有错因复盘功能。没有代码时提供「获取本次代码」：调用 source 接口，成功刷新工作台便可查看/分析；失败说明「判题已同步，源码暂不可读」，不要求用户填写代码来解锁记录。
5. 可选「载入编辑器」必须通过现有保存/换题保护，不能默默覆盖用户正在写的代码；查看记录、分析代码不强制载入编辑器。
6. 删除普通流程中的手填提交号和代码对应关系勾选。保留后端旧接口兼容历史客户端即可，不必保留旧表单。不要重复开发 `remote-submission.tsx`（它处理真的代交）。移除/纠正已过时的「本站不自动代交」「当前为手动交题」文字，CF/AtCoder 与其他平台分开描述。
7. 使用组件测试模拟接口验证；浏览器合成数据必须显式 mock 路由，禁止真实交题/登录、访问个人数据库或 Cookie、付费 AI。更新因新交互而失效的旧浏览器测试，不降低断言。
8. 完成后只提交到自己的分支，报告 SHA、变更文件、通过测试和未完成事项；不要合并 main、推送、部署、打包或操作私人配置。

## 后端接口（固定约定）

URL 中 pid 使用 `encodeURIComponent`，例如 `codeforces%3A4-A`。使用项目 `api` helper，已有会话/CSRF 机制自动生效。

### GET `/api/problem-submissions/{pid}?handle=fixture`

```json
{"problem_id":"codeforces:4-A","handles":["fixture"],"sync_status":null,"items":[{"submission_id":"12345","account":"fixture","verdict":"WA","submitted_at":1792640000,"language":"Python 3","url":"https://codeforces.com/contest/4/submission/12345","attempt_id":null,"has_local_code":false,"code_verified":false}]}
```

记录不含源码，不与当前草稿关联。`attempt_id` 可用于既有代码查看/AI 复盘。`code_verified=true` 仅代表从官方页面核对身份并读取过对应源码；本地已保存代码为 false，不能宣称官方核验。

### POST `/api/problem-submissions/{pid}/sync`

Body: `{"handle":"fixture"}`。不传 code/revision/提交号。返回标准任务 `{"id":"jobid","status":"queued"}`，通过 GET `/api/jobs` 查找该任务。

任务 result 示例：

```json
{"problem_id":"codeforces:4-A","handle":"fixture","count":2,"complete":true,"coverage":"本场比赛最近 600 条提交（仅保留当前题目）","warnings":[],"message":"已同步本题提交记录"}
```

`complete=false` 不是失败或没有记录；可能含 `error`。范围为 CF 本场最近 600 条、AtCoder Problems 最近 7 天最多 3 页（可能延迟）、洛谷公开列表最近 3 页、牛客公开练习最近 3 页（比赛记录覆盖有限）。不要写成全历史保证。无 Cookie 仍可尝试公开记录读取；读取源码需要有效登录。20 秒同题同账号冷却，429 显示原因。

### POST `/api/problem-submissions/{pid}/source`

Body: `{"handle":"fixture","submission_id":"12345"}`。

```json
{"problem_id":"codeforces:4-A","submission_id":"12345","handle":"fixture","code":"print(1)","language":"python3","attempt_id":"id","code_verified":true,"provenance":"official"}
```

优先返回已有当次不可变代码（`provenance=local_saved`，不伪称官方）；无代码时 CF/AtCoder 尝试读取本人官方源码。洛谷/牛客暂不自动读取源码，但仍能同步判题。接口不会修改当前编辑器草稿。400 表示源码暂不可用，原记录保留。

### 现有接口

- GET `/api/workbench/problem/{pid}`：刷新题目、draft、attempts。
- POST `/api/workbench/attempt/{attempt_id}/review`：沿用当前错因分析按钮及模型选项，实际路径以现有 workbench.tsx 为准。
- GET `/api/jobs` 返回 `{items:[...]}`（以当前 `api.ts` / 调用处为准，不要猜字段）。

## 验收最低范围

无需代码也能同步；同题不同账号隔离；WA 与 AC 列表；取不到源码不影响记录；已有代码直接查看；有代码后可复盘；保存/覆盖保护；旧请求不能覆盖换题后的界面；同一时刻不会重复同步；空列表有解释。最终主代理合并两分支后补充完整测试与隔离浏览器验收。

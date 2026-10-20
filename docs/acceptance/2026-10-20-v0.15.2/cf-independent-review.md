# CF 会话检测独立检查

日期：2026-10-20。此记录是本地源代码与合成响应验收，不是真实 Firefox、Tampermonkey Beta 或 Codeforces 登录/交题成功记录。

## 结论

本次范围内未发现阻断问题。新增 8 个独立回归用例，覆盖 JSESSIONID 存在却未认证、登录页/重定向/人机验证/访问失败、身份不匹配、换 Cookie 后成功缓存失效，以及缺少该字段但官方身份匹配仍能通过。

- `src/acmweb/credential_check.py`：会话字段仅输出布尔诊断，认证仍要求官方固定设置页的当前账号证据并匹配绑定 handle；不跟随重定向，不把公开资料链接或 Cookie 存在判为登录。
- `frontend/src/platform-connections.tsx`：JSESSIONID 提示明确属于完整性辅助信息，成功标签来自服务端认证状态。
- 教程要求独立核对 CF 当前 handle；明确安装 Beta/复制 Cookie 不代表服务器验证成功。
- 测试确认结果不包含合成 Cookie 值，官方交题能力仍标明 `auto_submit_supported=false`。

## 执行

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_cf_session_review.py tests/test_credential_check.py tests/test_credential_independent_review.py
npm --prefix frontend test -- src/extension-guide.test.tsx src/userscript.test.ts
```

结果：99 个 Python 用例通过；28 个前端用例通过。

## 剩余风险

本验证代理未读取真实 Firefox 资料、未访问真实账号、未安装真实 Beta，也未向平台提交代码。真实 Cookie 在 Firefox/Tampermonkey Beta 中的导出权限、服务器出口是否可通过 CF 验证、官方页面结构是否与解析规则一致，必须由另外的真实运行检查确认。合成成功结果不得作为上述真实能力的证明。缓存结果有既定有效期，主动重新检测仍受冷却限制。

# 牛客二维码登录独立安全验收

日期：2026-10-20。验证代理只改独立测试和本报告；未读取私人配置、密码或真实 Cookie，未扫码、未交题、未部署。所有接口响应和测试凭据均为合成数据。

## 结论

当前独立回归 25 项通过，未发现未修复的身份越权或凭据泄漏问题。此结论只覆盖本地合成上游的安全边界，不能作为牛客真实扫码登录成功的证据。

复现命令：

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_nowcoder_qr_security.py
```

结果：25 passed，13.21 秒。测试依赖产生一个现有 Starlette TestClient/httpx 弃用警告。

## 已验证范围

- 匿名创建返回 401，已登录缺 CSRF 返回 403。
- 同用户另一站内会话及另一用户，即使猜到流程 ID 并伪造 owner/user 请求头，轮询仍返回 404。所有权来自父应用注入的可信 scope state。
- 注销、注销所有会话、改密码、会话过期后，旧 pending 不可用于写入凭据。
- 特意阻塞上游轮询，在请求进行中注销，释放后旧请求返回 401/404，未保存凭据。
- 特意阻塞上游轮询，期间修改账号配置或超过截止时间；旧结果分别返回冲突/过期，并完整保留原账号或较新的 Cookie。
- 非固定图片源、伪域名、userinfo、端口、附加回调和不匹配票据均在发起图片请求前拒绝。状态响应任意 callback 不跟随。
- 扫码成功码不能独立证明身份；公开 profile、未登录 bootstrap、不可执行的 script 内容均不绑定。
- 公有接口不回显上游票据或 Cookie；无效 Cookie 不修改文件。原子 revision 合并不会删掉其他平台账号。

实现检查点：`src/acmweb/nowcoder_qr.py` 的 `_create`、`_poll`、`NowcoderQR.poll`；`src/acmweb/multiuser.py` 的 `owner_valid`、`dispatch`；`src/acmweb/accounts.py` 的 `bind_verified_account`。

## 本轮发现并已修复

1. 低风险：上游 `code` 最初使用 Python 宽松相等，`false`/`0.0` 被当成成功，`true`/`1.0` 被当成等待。最小复现 `pytest tests/test_nowcoder_qr_security.py -k unknown_status` 初次 4 failed、2 passed。实现现于 `nowcoder_qr.py:61,85` 严格限制整数，6 个参数全部通过。原实现仍要求最终当前身份，因此未形成身份绕过。
2. 低风险资源清理边界：配置 revision 读取失败可能发生在 pending/session 分配之后。实现已将 revision 读取前移到分配前，避免损坏配置消耗未释放的 pending 槽位；此项为静态审查，未声称动态故障注入通过。

## 剩余限制

- 真实扫码后的 Set-Cookie、微信确认、账号完善及可能的 SSO callback 行为需实际用户继续验收；本测试没有模拟结果替代真实登录。
- 任意 callback 被拒绝是安全边界；若官方真实登录必须进行额外交换，应依据精确官方证据单独适配，不能放开通用代理或重定向。
- 未做外网压力测试、长时间慢速响应测试或生产多实例验证。当前 pending 保存在单实例内存，重启需要重新扫码。

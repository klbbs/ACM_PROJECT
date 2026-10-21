# 牛客回调与公开采集接口复核（2026-10-21）

范围：独立只读复核上一轮 [扫码协议](../2026-10-20-nowcoder-qr/PROTOCOL.md) 中精确引用的官方 JavaScript，以及现有采集适配器的两个公开接口。本轮没有创建二维码、扫码、提交登录信息、读取 Cookie、执行下载脚本或访问上游返回的 callback。主代理已观察到本人官方浏览器处于登录状态；本报告不将该浏览器状态等同于服务器扫码流程已验收。

## 官方微信成功回调

重新下载以下两个精确 URL，均 HTTP200，字节数和 SHA-256 与上一轮完全一致：

| 官方脚本 | 字节数 | SHA-256 |
| --- | ---: | --- |
| [main.entry.js](https://static.nowcoder.com/nowcoder/2.0.314/javascripts-wp/page/login/login/main.entry.js) | 47,594 | `844f36610397fcbedbbafb65ce163519ced49121be672f752a377c3c35f95a47` |
| [common.js](https://static.nowcoder.com/nowcoder/2.0.314/javascripts-wp/lib/common.js) | 2,348,895 | `d089e72b403dc0456407b724265afb041fbce513ce4c0ede1244f9d38d041288` |

main.entry.js 的 WxLogin.checkStatus（UTF-8 解码后的零基字符偏移约 15767–16550）仍以 ticket 查询 `wechat_mp_status`。成功分支读取 data.callBack、data.needComplete、data.rtype，交给 window.loginCallBack 或 location.replace。该选定分支内没有发现独立 Token 交换请求。

common.js 的 window.loginCallBack 定义（模块 57358，偏移约 1824583；模块 25295，偏移约 2129023、2135273）执行目标地址导航/当前页重载；需要完善手机时打开相应 UI，完成后再导航。本次局部源码没有给出一条必须追加到旧微信路线的固定 SSO/Token 交换端点。因此仍不能声称 status 成功响应的 Set-Cookie 一定充分，也不能依据无 callback 的静态分支断言所有真实扫码均无需交换。真实成功响应、callback 的确切地址及跳转后的服务端当前身份，仍需原扫码会话实际验收。

同一 common.js 另有不同路线，必须与旧微信路线区分：

- SPARTA 微信组件向 `/api/sparta/wechat/login/qr-code/check-status` 发起请求。部分无 callback 的成功分支调用 recordTicket，将响应 token 写为 `ls_sess_id`、`t`（模块 57358 约 1820538、模块 25295 约 2128341）。这不能证明旧 `wechat_mp_status` 使用相同返回值。
- `authSetToken` 在模块 56105 定义为知页源 `/v1/auth/set-t`；本文件中的调用位于知页同步相关逻辑（模块 32887 约 2106949），读取当前 Token 后同步知页。没有发现从旧 WxLogin.checkStatus 到该调用的直接连接，不能把该流程拼到牛客 ACM 的旧微信登录中。

此前 main.entry.js 的 REA 父 Evidence `ev_1c7904ffecf4ba8d45b1d94e443a1f626ec736ce77cd294067b5fb541fc4240e` 与当前精确字节一致，沿用其静态观察，不重复执行。此次 common.js 的 REA 父 Evidence 为 `ev_e089e9cc537e85505f55490a1fa40d8bb137568944b30d27549db0f7831fcfe8`，summary `ev_8876d396ad8d13d464998b4311b1456d093a870d0a2e242d7f35e2343956ab76`：1 个脚本解析成功、897 个模块、0 parse failure。语义图受 5,000/10,000 节点上限截断；上述结论结合原始完整脚本的精确局部文本检查，只覆盖所述分支，不代表全站行为穷尽。分析目录 `/tmp/acm-nowcoder-protocol-review`。

## 现有适配器公开数据接口

匿名用户 `567431790`，每个现有固定接口只请求一次，不携带个人凭据，不跟随重定向，没有创建额外重试或分页请求。

| 现有官方端点 | 实际结果 | 现有解析兼容性 |
| --- | --- | --- |
| `GET https://ac.nowcoder.com/acm/contest/rating-history?uid=567431790` | HTTP200，application/json，10,767 字节；code=0，data 数组75条 | 首项字段 contestId/rating/rank/changeValue/time/contestName/colorLevel，包含现有 rating_event 转换所需字段 |
| `GET https://ac.nowcoder.com/acm/contest/profile/567431790/practice-coding?page=1&pageSize=20&orderType=DESC` | HTTP200，text/html，44,183 字节 | 直接调用现有 parse_submissions 得20条记录、next_page=2，首条 AC |

这验证了公开评级和练习提交接口在本机本次匿名请求仍可用，不验证本人登录会话、完整历史分页或私人/比赛内未归档记录。现有适配器关于公开练习采集范围的限制仍适用。

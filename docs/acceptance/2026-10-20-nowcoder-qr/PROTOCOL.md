# 牛客官方扫码协议核对（2026-10-20）

范围：匿名公开登录页及其精确引用的官方 JavaScript；无个人账号配置访问、无扫码、无代码提交。临时文件位于 0700 临时目录。该报告不保存票据、二维码 URL、Cookie 或完整页面。

## 可用路径与实际匿名探测

采用同一 requests.Session，先请求 `https://ac.nowcoder.com/login?callBack=/`，再请求下列固定端点，全部禁止自动重定向：

| 操作 | 官方固定端点 | 匿名实际观察 |
| --- | --- | --- |
| 微信二维码 | `GET https://ac.nowcoder.com/nccommon/oauth2/login/wechat_qr_code` | HTTP 200、code 0；data 字段 ticket、imageUrl、expireSecond；本次有效期 120 秒 |
| 微信状态 | `GET https://ac.nowcoder.com/nccommon/oauth2/login/wechat_mp_status`，query 为 ticket | HTTP 200、code 1、消息“二维码未被扫描”，data 为 null |
| 微信图片 | `GET https://mp.weixin.qq.com/cgi-bin/showqrcode`，query 为 ticket | 同一 Session 可取得 HTTP 200、image/jpg、37,183 字节、无跳转；未存储/展示图片 |
| 非法票据状态 | 同微信状态端点，固定无效测试文本 | HTTP 200、code 1、消息“ticket长度错误”；说明 code 1 并不全是正常等待 |
| 牛客 APP 二维码（本轮不接入） | `GET https://www.nowcoder.com/nccommon/scan/code` | HTTP 200、code 0；data.code、expireSecs，本次 300 秒 |
| 牛客 APP 状态（本轮不接入） | `GET https://gw-c.nowcoder.com/api/sparta/login/app-scan-login`，query 为 code | HTTP 200、code 1、“用户还未扫APP扫描确认” |

`https://ac.nowcoder.com/oauth2/login/wechat_qr_code` 本次返回 301，不宜假设无需 `/nccommon` 前缀。www 源的 `/oauth2/...` 与 `/nccommon/oauth2/...` 均实测返回 JSON，但首版应只固定一条已验证 ac 源路径。

## 官方交付 JavaScript 的静态证据

来源：

- [登录入口](https://ac.nowcoder.com/login?callBack=/)
- [main.entry.js](https://static.nowcoder.com/nowcoder/2.0.314/javascripts-wp/page/login/login/main.entry.js)，47,594 bytes，SHA-256 `844f36610397fcbedbbafb65ce163519ced49121be672f752a377c3c35f95a47`。
- [common.js](https://static.nowcoder.com/nowcoder/2.0.314/javascripts-wp/lib/common.js)，2,348,895 bytes，SHA-256 `d089e72b403dc0456407b724265afb041fbce513ce4c0ede1244f9d38d041288`。
- [vendor.dll.js](https://static.nowcoder.com/nowcoder/2.0.314/javascripts-wp/lib/vendor.dll.js)，410,693 bytes，SHA-256 `fb11ae065e9aca62307b7111705e596e5ba305fba8b51d3b3ce5b239c62e8ae3`。

已按 reverse-engineer-anything 技能以 REA 惰性解析，未执行下载代码：

- main.entry.js 父 Evidence：`ev_1c7904ffecf4ba8d45b1d94e443a1f626ec736ce77cd294067b5fb541fc4240e`，summary `ev_a4281e029141e7a2cc6cfe7c68345bf6d14839f24618d9a89568278a2e12a2ba`；1 个 JS 解析成功、15 个模块、96 个 findings、0 parse failure。应用图部分覆盖，动态引用未穷尽。
- vendor.dll.js 父 Evidence：`ev_e1f3075a1931d780dd1259bff29f75269d136e2721e3ae0bd12ff670b7aa9a89`，summary `ev_87e6d9dd77e2763233dea35d760a335f4f06884d17ac63dfc38d404258598ce8`；1 个 JS 解析成功、651 个模块、0 parse failure，语义图受 5,000 节点上限截断。完整源码的精确局部字符串核对支持下列控制流程，不把截断图当成完整行为证明。

main 的模块 30940 定义微信两个端点。WxLogin.getWxCode 读取 data.ticket / imageUrl / expireSecond；initCountDown 达到有效期令 isOverdue=true；checkStatus 使用 frequency(3000)，到期停止轮询。状态请求以 query.ticket 调用；vendor 模块 47598 的 getRequestType 为有 query 无 body 时 GET，否则 POST，显式 type 优先。

微信 code 0 经官方请求封装的成功分支后，消费 data.callBack、data.needComplete、data.rtype。如果有 userCallback 且符合 `/users/数字` 则跳个人页，否则 callBack 存在时拼接当前页面的 callBack 后交给 window.loginCallBack，或执行 location.replace；无回调时跳首页/重新加载。该组件未显示独立的验证码处理或扫码拒绝枚举。

本次选定的 WxLogin 路线没有观察到独立 token 交换调用；其他登录组件可能有不同流程，不能因此断言 status 的 Set-Cookie 一定足够，也不能把其他路线的 authSetToken 混入当前路线。callback 是否包含 SSO 交换必须在真实扫码后继续验证。

APP 的 timer 为 2 秒，error.code===2 会置为过期，这是 APP 静态规则，不能移植成微信的状态枚举。

## 实现契约建议

1. 创建时须 ACM Lab 登录、CSRF 校验；每用户独立 Session + 随机本地流程 ID，替换/取消/到期即清理。上游 ticket 留后端内存，不提供通用代理；本地页面只取得专属图片 URL 或图像字节。
2. 后端只请求固定 ac.nowcoder.com 的初始化/二维码/状态/身份端点及固定 mp.weixin.qq.com 图片路径。禁任意 imageUrl、任意重定向、任意回调，限制超时/响应大小/MIME；图片真实 Content-Type 是 image/jpg，不能仅允许 image/png。
3. 轮询后端至少 3 秒节流，本地过期由创建时 expireSecond 单调时钟决定。已知“二维码未被扫描”可等待；code 1 也能是非法票据，未知消息和 HTTP/JSON 异常应给可恢复错误，不能假定成功或永久等待。
4. code 0 后先使用同一 Session 请求固定 `https://ac.nowcoder.com/` 并用既有 nowcoder_identity 静态验证当前会话：唯一可执行 bootstrap 的 window.isLogin=true 和 flat globalInfo.ownerId 正整数。不得用公开 profile、仅存在 Cookie 或客户端提交的 UID 作为凭据成功依据。
5. 若 code 0 后无可靠当前 UID，应返回“扫码完成但尚未验证关联”；遇 needComplete 提示去官方页面完善账号，不虚报已绑定。成功 callback 尚未实测，不能安全地泛化为任意网址访问；保留严格官方路径适配或后续真实证据再实现。
6. 验证成功后才将该 Session 的适用 Nowcoder Cookie 按现有用户独立目录与0600私有文件逻辑保存，并绑定服务端验证的 UID；不能把整个 Session jar 展示或返回前端。失败、取消、过期不修改原凭据。

## 结论与未覆盖项

无桌面服务器能够用 requests 完成匿名二维码创建、图像获取和等待轮询，这一点已经实际观察。真实用户扫码确认、成功 Set-Cookie/callback 行为、UID 验证及会话跨域有效性尚未完成，不能据此报告“牛客真实登录成功”。验证码/完善账号/微信关注由用户本人在官方交互中完成，不做自动绕过。

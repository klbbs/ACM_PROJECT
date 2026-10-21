# 浏览器登录重定向实际传输验收（2026-10-21）

结论：修复后的真实 Chromium 传输通过原10项探测、OOPIF 回归及普通/嵌套/跨站iframe内的 dedicated worker，共15项；受禁止目的地实际收到的请求数合计 **0**。这是本地受控网络验收，不代表真实平台登录或验证码已经通过。

## 复现命令与边界

```sh
.venv/bin/python scripts/verify_browser_login_redirects.py --run-local-transport-probe
```

脚本必须显式选择 `--run-local-transport-probe`，否则不会启动浏览器或本地服务。需要项目依赖、`requirements-browser.lock` 对应的 Chromium 安装及系统 `openssl`。

脚本启动127.0.0.1上由操作系统分配空闲端口的临时 HTTPS 服务。测试专用 Chromium 的 host-resolver-rules 将所有使用的域名映射到该本地服务，其他域名解析失败；不使用真实平台网络、个人浏览器 profile 或个人凭据。临时目录权限0700，自签证书仅用于该测试 context；测试包装器先断言生产 manager 传入 `ignore_https_errors=False`，然后只对本地 fixture 改为 True。`chromium_sandbox=True` 保留。浏览器、服务器、临时账号数据及证书均在 finally/TemporaryDirectory 中清理。

服务只保存合成请求的路径、方法及是否包含固定合成测试正文的布尔值；输出仅含用例名和计数，不保存网页、图片、原始请求头或真实凭据。

## 原10项与追加回归

| 用例 | 本地 fixture 收到请求数 | 禁止目的地收到请求数 | 结果 |
| --- | ---: | ---: | --- |
| 同源302到最终页 | 3 | 0 | 允许 |
| 官方源302到外域 | 2 | 0 | 在下一跳发出前拒绝 |
| 官方源307到私网IP地址 | 2 | 0 | 在下一跳发出前拒绝 |
| 顶层302到仅允许作子资源的平台子域 | 2 | 0 | 拒绝并撤销流程 |
| 同源第一跳后的第二跳转外域 | 3 | 0 | 第二跳发出前拒绝 |
| 主frame图片302到外域 | 3 | 0 | 仅拒绝图片，原流程保留 |
| 合法平台子域图片 | 3 | 0 | 允许，原流程保留 |
| 合法Cloudflare嵌套frame | 3 | 0 | 允许，原流程保留 |
| 同源POST经307 | 3 | 0 | 保留POST和固定合成正文 |
| 同源POST经302 | 3 | 0 | 保留Chromium改为GET的语义 |
| 追加：跨站OOPIF内部图片302到外域 | 4 | 0 | 子target拒绝下一跳，原流程保留 |

所有用例使用实际 BrowserLogin.start/context/route/私有 CDP guard 路径，未以假的路由实现替代真实 Chromium 行为。追加回归要求 iframe 的图片重定向起点实际到达服务，不能靠完全不加载 iframe 得到零请求。

## 独立审阅发现及修复验证

1. 最初仅有 Playwright context.route 白名单。已核对安装的 Playwright 文档及 Chromium transport 实现：redirectedFrom 分支会直接继续后续请求，不再进入 route handler。受允许首跳的302可以跨越该白名单。修复加入 Response-stage 私有 CDP 拦截，在继续响应前检查 Location，保留 Chromium 自身的TLS、Cookie与重定向方法语义。
2. 第一版 Response guard 仅比较frameId，将主frame的普通JS/CSS/图片误当顶层。独立实际探测发现后已修正为同时要求resourceType=Document；合法子域资源与图片失败后保留会话均通过实测。
3. 页面级 CDP session 未覆盖跨站 iframe 的独立 OOPIF target。追加实际探测曾使外域收到一次 GET。修复递归附加相关iframe/worker targets，先安装 Response guard 再允许其运行；修复后相同回归的外域请求数为0。

另外进行了负控：仅在一次本地测试进程中禁用 Response guard，原“官方源302到外域”用例立即检测到禁止目的地收到一次请求。负控表明服务端计数能够发现真实外发，不是只检查页面最终URL；负控未写入生产代码。

## 相关隔离与生命周期检查

独立执行真实多用户 ASGI 路由、合成浏览器传输的探测，通过未登录401、缺CSRF403、四个接口跨owner404、非法文字输入422不回显输入、注销立即撤销流程、应用关闭后释放浏览器。相关Python回归178项通过；早期前端组件回归12项通过。此后后台追加的私有子target传输测试由实现代理执行，完整项目检查由主线汇总。

当前验证覆盖所列传输路径、用户隔离和合成会话生命周期；没有将公开资料响应、已存在的本人浏览器登录或本地fixture当成三平台真实密码登录成功，也没有进行新的真实登录、扫码或验证码求解。


## 最终 dedicated-worker 验收与 AtCoder 修复

真实 AtCoder 的 Turnstile 会创建 blob dedicated worker。最初向 worker 执行 Fetch.enable 会因不支持该域而撤销整个登录。尝试 Network.setBlockedURLs('*') 虽被协议接受，真实探针仍观察到第一条同源 fetch 外发，证明其不是可靠的禁网边界；最终实现没有采用这一假设。

独立验证者进行了区分性真实探针：仅在测试进程去掉 worker Network 禁网，保留 creator page/iframe 的 Response Fetch 和 context.route。普通及嵌套 worker 的计算42、postMessage均运行，同源 fetch 和同源302起点确实到达本地 fixture，直接外域和302坏目标收到0请求；302/XHR事件实际由父 page CDP session接收。这与本机已安装 Playwright 的 workerFrame 传输模型一致。

最终实现由 creator page 或 OOPIF 的既有 Fetch session检查 dedicated-worker 响应，不向 worker 发不支持的 Fetch 命令，同时递归跟踪其子target。主线进一步实测四种情况：普通blob、嵌套blob、Cloudflare跨站iframe内blob、该iframe内嵌套blob，全部通过。每一种都要求计算及消息完成、同源fetch和302起点各实际发送1次、外域/坏跳转目标收到0请求；并在异步worker全部结束后统计，避免过早取样造成假0。SharedWorker没有此验证证据，broker对它单独关闭target，不声称支持。

最终15项无凭据原始计数保存于 [redirect-results.json](redirect-results.json)。实际官方匿名AtCoder 15秒观察也可显式运行：

```sh
.venv/bin/python scripts/verify_browser_login_redirects.py --run-official-atcoder-passive
```

该观察不填写、点击或处理验证码，不保存账号，不能冒充真实登录成功。主线也用鼠标确认最终网页中输入账号、密码和显示真人验证框均可工作，最终登录仍按独立的真实身份标准验收。

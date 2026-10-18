import React from 'react'
const platforms = [
  {
    name: 'Codeforces',
    url: 'https://codeforces.com/enter',
    profile: 'https://codeforces.com/profile/你的用户名',
    identity: 'handle 用户名，不是昵称或数字题号',
    detail:
      '公开提交和题库通常不需要 Cookie。题面可能遇到平台反爬；登录 Cookie 不能保证服务器 IP 通过验证。',
  },
  {
    name: '洛谷',
    url: 'https://www.luogu.com.cn/auth/login',
    profile: 'https://www.luogu.com.cn/user/数字UID',
    identity: '个人主页地址里的数字 UID，不是昵称',
    detail: '部分资料、提交或题目内容需要登录态。请复制 www.luogu.com.cn 同域请求的 Cookie。',
  },
  {
    name: '牛客',
    url: 'https://www.nowcoder.com/login',
    profile: 'https://ac.nowcoder.com/acm/contest/profile/数字UID',
    identity: '个人主页的数字 UID',
    detail:
      '算法页面常使用 ac.nowcoder.com；登录后请在实际访问算法资料的页面复制该请求发送的 Cookie。',
  },
  {
    name: 'AtCoder',
    url: 'https://atcoder.jp/login',
    profile: 'https://atcoder.jp/users/你的用户名',
    identity: 'users 后面的用户名',
    detail:
      '公开比赛成绩通常不需要 Cookie；受限页面和官方提交页面需要有效登录。请复制 atcoder.jp 同域请求。',
  },
]
export function PlatformGuide() {
  return (
    <div className="platform-guide">
      <section className="card">
        <h2>平台绑定与提交教程</h2>
        <p>
          网站账号与算法平台账号是两套登录。添加 handle / UID
          表示配置采集目标，不代表已经登录平台，也不能证明你拥有该账号。
        </p>
        <p>
          首次使用：登录算法平台 → 找到主页标识 → 在「连接与设置」添加账号 → 按需填写 Cookie → 保存
          → 同步账号。
        </p>
      </section>
      <section className="card">
        <h3>F12 到底复制哪一部分？</h3>
        <ol>
          <li>在你自己的浏览器登录目标平台，确认可以看到个人主页或提交列表。</li>
          <li>
            按 F12，选择「网络 / Network」，然后刷新网页。Chrome / Edge 也可用 Ctrl+Shift+I；Firefox
            可用 Ctrl+Shift+E。
          </li>
          <li>
            选择发往目标平台的成功请求：优先选择文档（Document）或已成功返回个人资料的
            Fetch/XHR。不要选择图片、广告或其他域名。
          </li>
          <li>
            打开「标头 / Headers」→「请求标头 / Request Headers」，找到 <code>Cookie</code>
            ，只复制它右侧完整的值。Firefox 也可在 Cookies 页查看请求 Cookie，但不要把响应 Cookie
            的属性一起粘贴。
          </li>
          <li>
            在本网站「连接与设置」添加对应平台，填写主页链接或 handle / UID，将上一步的值粘贴到
            Cookie 输入框并保存。
          </li>
        </ol>
        <pre className="guide-example">
          正确格式（示意，不是真实凭据）：session=你的值; other_name=另一个值
        </pre>
        <p>
          只需要 handle / UID 与 Cookie 值。不需要密码、验证码、整段请求头、Copy as cURL、HAR
          文件、Authorization、响应里的 Set-Cookie 或平台 CSRF token。不要添加 Cookie: 前缀或 Path /
          Domain / Expires 属性。
        </p>
        <p>
          Cookie 等同登录凭据，只填入你信任的本网站 HTTPS 部署；不要分享给其他成员、上传 Git 或放进
          AI 对话。已保存 Cookie 不回显；留空保留原值，更新或清除操作见配置表单。
        </p>
        <a
          href="https://firefox-source-docs.mozilla.org/devtools-user/network_monitor/request_details/index.html"
          target="_blank"
          rel="noreferrer"
        >
          Firefox 官方网络请求查看说明 ↗
        </a>
      </section>
      <div className="guide-platforms">
        {platforms.map((p) => (
          <section className="card" key={p.name}>
            <h3>{p.name}</h3>
            <p>
              <a href={p.url} target="_blank" rel="noreferrer">
                打开官方登录页 ↗
              </a>
            </p>
            <p>标识：{p.identity}</p>
            <code>{p.profile}</code>
            <p>{p.detail}</p>
          </section>
        ))}
      </div>
      <section className="card">
        <h3>提交前必须知道</h3>
        <p>
          目前工作台尚未实现自动代提交。保存快照只保存在本网站，不会把代码发送给平台。当前判题关联支持
          Codeforces；其他平台的采集支持不代表已支持在线提交。
        </p>
        <ol>
          <li>在自己的浏览器登录 Codeforces，并确保登录的 handle 与本网站所选账号一致。</li>
          <li>
            保存代码快照，下载本次代码，前往官方提交页选择题号和对应语言、粘贴或上传同一份代码。
          </li>
          <li>
            在官方页面点击提交，复制生成的数字提交号，返回工作台关联并核验判题。判题中可稍后刷新。
          </li>
        </ol>
        <p>
          浏览器已登录，不等于服务器 Cookie 有效；已配置
          Cookie，也不代表登录状态已经验证。本网站无法直接读取另一个网站的浏览器登录状态。验证码由用户在官方页面完成。
        </p>
      </section>
      <section className="card">
        <h3>题面获取失败怎么办？</h3>
        <p>
          HTTP 403、验证页或超时可能来自平台反爬、网络或服务器
          IP；不应反复快速重试。先打开原题，在浏览器完成正常验证，再刷新本网站题面。失效 Cookie
          可按上述步骤更新。
        </p>
        <p>
          仍失败时：在原题按 F12 →「元素 / Inspector」→ Ctrl+F 搜索 <code>problem-statement</code> →
          选中对应 div → 右键复制 Outer HTML / 外部
          HTML，粘贴到工作台「导入题面」。只复制题面节点，不复制整页或登录表单。
        </p>
        <p>
          导入内容将标为「手动导入」，不能独立核验题号对应关系。只提取文本和样例，不执行
          HTML；图片和复杂公式请同时查看原题。
        </p>
      </section>
    </div>
  )
}

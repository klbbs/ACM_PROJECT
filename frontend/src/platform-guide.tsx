import React from 'react'
const platforms = [
  {
    name: 'Codeforces',
    url: 'https://codeforces.com/enter',
    profile: 'https://codeforces.com/profile/你的用户名',
    identity: 'handle 用户名，不是昵称或数字题号',
    detail:
      '在 codeforces.com 登录后进入个人主页或 My submissions，再复制同站请求。公开题面、公开提交或 HTTP 200 均不能证明 Cookie 已登录。',
  },
  {
    name: '洛谷',
    url: 'https://www.luogu.com.cn/auth/login',
    profile: 'https://www.luogu.com.cn/user/数字UID',
    identity: '个人主页地址里的数字 UID，不是昵称',
    detail:
      '配置个人主页里的数字 UID 后，同步可公开访问的资料与提交记录。本站当前提供洛谷公开资料同步，不提供洛谷登录检测；交题请在自己的浏览器登录洛谷官方网页，提交后回到本站同步记录。',
  },
  {
    name: '牛客',
    url: 'https://www.nowcoder.com/login',
    profile: 'https://ac.nowcoder.com/acm/contest/profile/数字UID',
    identity: '个人主页的数字 UID',
    detail:
      '优先使用上方牛客卡片的微信扫码绑定，由官方当前会话确认数字 UID 后自动保存。也可登录 ac.nowcoder.com 算法页面，复制该域实际发送的 Cookie 后保存检测；公开用户主页不能证明登录。',
  },
  {
    name: 'AtCoder',
    url: 'https://atcoder.jp/login',
    profile: 'https://atcoder.jp/users/你的用户名',
    identity: 'users 后面的用户名',
    detail:
      '复制 atcoder.jp 已登录页面的同站请求。公开比赛成绩可匿名读取，与可提交代码的登录状态是两回事。',
  },
]
export function PlatformGuide() {
  return (
    <div className="platform-guide">
      <section className="card">
        <h2>平台绑定与提交教程</h2>
        <p>
          推荐先使用上方 Cookie
          助手：安装油猴脚本，在已登录的平台点击获取，回到本页粘贴、保存并检测。若只读到非 HttpOnly
          Cookie，脚本会标明完整性未知，由你选择是否复制；需要完整凭据时按下面的 F12 步骤手动完成。洛谷同步公开资料只需配置数字 UID。
        </p>
        <p>
          油猴助手仅支持普通默认会话；Firefox 容器、隐私窗口或首方隔离请直接使用 F12，
          并在当前已登录的平台标签页复制实际请求 Cookie，不要换到其他窗口获取。
        </p>
        <p>
          本站账号与算法平台账号分别登录。添加 handle / UID
          只是选择采集目标，不能证明你拥有该账号。请依次完成以下四步。
        </p>
        <ol>
          <li>
            <strong>同步信息：</strong>在「连接与设置」添加平台与 handle /
            UID，保存后同步公开提交和资料；公开采集成功不等于登录成功。
          </li>
          <li>
            <strong>登录检测：</strong>需要会话时按下方步骤保存
            Cookie，再点击对应账号的登录检测（Codeforces、牛客、AtCoder）。核对检测返回的真实账号是否与你填写的一致；洛谷使用数字 UID 同步公开资料，不提供登录检测。
          </li>
          <li>
            <strong>官方交题：</strong>
            在当前浏览器单独登录官方平台，确认账号，前往官方提交页选择题目和语言，粘贴代码后亲自提交。本站目前不会自动代交。
          </li>
          <li>
            <strong>提交结果关联：</strong>
            按工作台提示填写官方提交号并核验结果。当前实时判题关联以界面列出的支持范围为准；其他平台请先在官方查看判题，再同步账号更新训练记录。代码自测不等于官方
            AC。
          </li>
        </ol>
      </section>
      <section className="card">
        <h3>Codeforces：F12 详细复制步骤</h3>
        <p>
          如需一键获取 HttpOnly，可先按上方说明选择官方 Firefox Beta 并授权脚本。 获取前打开 CF
          设置页，核对 handle；保存后以本站检测返回同一 handle 为准。 JSESSIONID
          是常见会话字段，出现与否仅辅助排查，不能当作登录成功证明。
          当前仍由用户在官方网页交题，本站关联判题结果。
        </p>
        <ol>
          <li>
            在自己的浏览器打开 Codeforces 官方登录页，正常完成登录与验证，确认右上角显示你的
            handle，再进入个人主页或 My submissions。
          </li>
          <li>
            按 F12 打开开发者工具，选择「网络 / Network」。Firefox 可按 Ctrl+Shift+E；Chrome / Edge
            可按 Ctrl+Shift+I 后选择 Network。保持工具打开并刷新平台页面。
          </li>
          <li>
            选择发往 <code>codeforces.com</code>{' '}
            的页面请求（Document）或个人资料请求（Fetch/XHR）。确认域名正确，不选图片、广告、第三方统计或登录失败的请求。
          </li>
          <li>
            打开「标头 / Headers」→「请求标头 / Request Headers」，找到 <code>Cookie</code>
            ，复制右侧<strong>完整的值</strong>。Firefox 若折叠了
            Cookie，请展开或切换原始标头查看。不要复制「响应标头 / Response Headers」中的
            Set-Cookie。
          </li>
          <li>
            返回本站「连接与设置」，选择 Codeforces，填写同一个 handle，把完整值粘贴到 Cookie
            输入框，保存账号配置。
          </li>
          <li>
            保存成功后点击该账号的「检测登录状态」。只有检测确实识别出真实登录账号、且与配置 handle
            匹配时，才说明服务器此次验证了该会话。检测失败或无法确定时，按下方排查。
          </li>
        </ol>
        <pre className="guide-example">
          {'示意格式（不是实际凭据）：JSESSIONID=你的值; RCPC=你的值; other_cookie=其他值'}
        </pre>
        <p>
          JSESSIONID、RCPC 及其他验证 Cookie
          是否存在，以浏览器当前请求为准；不要只挑某一个名称，也不要照抄示例补造字段。平台会调整验证方式，任何固定
          Cookie 名称组合都不能保证服务器可用。
        </p>
        <p>
          只粘贴 Cookie 的完整值，不带 <code>Cookie:</code> 前缀。不要粘贴 Set-Cookie、Path / Domain
          / Expires 属性、整段请求头、Copy as cURL 或整个
          HAR。无需提交平台密码、验证码或浏览器用户目录。
        </p>
        <p>
          找不到 Cookie？确认已登录，再刷新并选择该平台页面请求；不要在本站页面的 Network
          中找，也不要复制「Application / 存储」面板的整张 Cookie 表。
        </p>
        <p>
          Cookie 等同登录凭据，只交给你信任的本站 HTTPS 部署。不要发给其他成员、上传 Git 或放进 AI
          对话。已保存 Cookie 不回显；留空保留原值，勾选清除则移除原会话。更新 Cookie
          后必须保存再检测。
        </p>
        <a
          href="https://firefox-source-docs.mozilla.org/devtools-user/network_monitor/request_details/index.html"
          target="_blank"
          rel="noreferrer"
        >
          Firefox 官方网络请求说明 ↗
        </a>
        {' · '}
        <a
          href="https://developer.chrome.com/docs/devtools/network/reference#headers"
          target="_blank"
          rel="noreferrer"
        >
          Chrome 官方请求标头说明 ↗
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
        <h3>从历史提交回到工作台</h3>
        <p>
          在「提交记录」选择「在工作台查看」，打开对应题目并定位那次历史提交。公开采集的历史记录通常不含源码，不能自动恢复当时的代码。需要旧代码请在官方提交记录中查看并自行复制；不要把当前草稿当作历史源码。
        </p>
      </section>
      <section className="card">
        <h3>检测失败或结果不一致怎么办？</h3>
        <ul>
          <li>
            <strong>Cookie 格式不完整：</strong>
            不要复制开发者工具里被省略的预览，展开或切换原始请求标头后复制完整值。不要带 Cookie:
            前缀、换行、中文说明或省略号。本站不会自动补全缺失的凭据。
          </li>
          <li>
            <strong>Cookie 过期或已退出：</strong>在官方浏览器重新登录，重新复制当前请求
            Cookie，保存后再检测。旧 Cookie 不会自行更新。
          </li>
          <li>
            <strong>其他平台检测出现 403 / Cloudflare / 验证页面：</strong>可能是服务器
            IP、网络或平台验证限制。即使浏览器可访问，复制 Cookie
            也未必让服务器通过。先在官方完成正常验证，避免连续快速重试；仍受限就使用官方页面。
          </li>
          <li>
            <strong>账号错配：</strong>核对真实检测账号和配置 handle /
            UID。不要通过更改昵称掩盖不一致；切换官方登录账号并重新复制
            Cookie，或将本站配置改为正确目标。
          </li>
          <li>
            <strong>仅公开数据可用 / 无法确认登录：</strong>
            可以继续同步可公开访问的记录，但不能声称已绑定登录态。HTTP
            200、公开个人主页和题面可读都不足以证明已登录。
          </li>
          <li>
            <strong>服务器检测通过，官方提交页仍未登录：</strong>这是两套独立会话。服务器 Cookie
            不会注入或共享给你的浏览器，Firefox
            的登录也不会自动共享给本站内置浏览器；在实际打开提交页的浏览器中单独登录。
          </li>
        </ul>
      </section>
      <section className="card">
        <h3>题面获取失败怎么办？</h3>
        <p>
          先打开原题确认比赛访问权限、网络和登录状态，再尝试刷新本站题面。失败时界面应显示真实原因；不要把反爬验证页当作题面或把请求成功当作登录成功。
        </p>
        <p>
          仍失败可在原题 F12 →「元素 / Inspector」中找到题面节点（Codeforces 为{' '}
          <code>problem-statement</code>，AtCoder 为 <code>task-statement</code>），复制该节点 Outer
          HTML / 外部 HTML，粘贴到工作台「导入题面」。只复制题面，不复制整页或登录表单。
        </p>
        <p>
          导入内容标为手动导入，不能独立核验题号，也不能上传为官方共享译文。页面不会执行导入脚本；图片与复杂公式请对照原题。
        </p>
      </section>
    </div>
  )
}

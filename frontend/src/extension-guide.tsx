import React from 'react'

export function ExtensionGuide() {
  return (
    <section className="card extension-guide" id="cookie-helper">
      <span className="eyebrow">BROWSER COMPANION · v1.0.0</span>
      <h2>一键获取平台 Cookie</h2>
      <p>
        在你已登录的算法平台打开插件，点击获取并复制，再回到本站粘贴。支持 Codeforces、洛谷、牛客和
        AtCoder。
      </p>
      <div className="extension-downloads">
        <a className="extension-download" href="/api/browser-extension/chromium.zip" download>
          下载 Chrome / Edge 插件 ZIP ↓
        </a>
        <a className="extension-download" href="/api/browser-extension/firefox.zip" download>
          下载 Firefox 测试插件 ZIP ↓
        </a>
      </div>
      <div className="guide-platforms">
        <div>
          <h3>Chrome / Edge</h3>
          <ol>
            <li>下载并解压 ZIP，保留整个文件夹。</li>
            <li>
              打开 <code>chrome://extensions</code>（Edge 用 <code>edge://extensions</code>
              ），开启「开发者模式」。
            </li>
            <li>
              点击「加载已解压的扩展程序」，选择包含 <code>manifest.json</code> 的文件夹。
            </li>
            <li>将「ACM Lab 平台 Cookie 助手」固定到浏览器工具栏。</li>
          </ol>
        </div>
        <div>
          <h3>Firefox 128+ · 临时测试安装</h3>
          <ol>
            <li>
              下载并解压 ZIP，打开 <code>about:debugging#/runtime/this-firefox</code>。
            </li>
            <li>
              选择「临时载入附加组件」，选择文件夹中的 <code>manifest.json</code>。
            </li>
            <li>
              关闭并重启 Firefox 后需要重新载入；当前包尚未经过 Mozilla
              签名，不能作为正式扩展永久安装。
            </li>
          </ol>
          <a
            href="https://extensionworkshop.com/documentation/develop/temporary-installation-in-firefox/"
            target="_blank"
            rel="noreferrer"
          >
            Firefox 官方临时安装说明 ↗
          </a>
        </div>
      </div>
      <h3>安装后：获取 → 粘贴 → 保存 → 检测</h3>
      <ol>
        <li>
          在同一浏览器正常登录平台并完成验证。打开 HTTPS 官方页面：<code>codeforces.com</code>、
          <code>www.luogu.com.cn</code>、<code>ac.nowcoder.com</code> 或 <code>atcoder.jp</code>。
        </li>
        <li>
          点击工具栏中的插件，核对平台，点击「获取 Cookie
          并复制」，首次使用允许当前域名权限。牛客建议在 <code>ac.nowcoder.com</code> 算法页面获取。
        </li>
        <li>
          回到下方「算法平台连接」，选择平台、填写自己的 handle / UID，将剪贴板内容粘贴到 Cookie
          输入框并保存；已有账号选择「编辑」。
        </li>
        <li>
          点击对应账号的「检测登录状态」。复制成功不等于检测通过；检测无法确认身份时请按下方教程排查。
        </li>
      </ol>
      <p className="help">
        插件只读取当前允许域名及当前浏览器容器的 Cookie（包含
        HttpOnly），不访问其他网站、不保存、不上传、不修改
        Cookie，也不会自动更新。关闭弹窗清除临时内容；系统剪贴板请在使用后自行覆盖。读取不到时可使用下方
        F12 教程。
      </p>
      <p className="help">
        Cookie 等同登录凭据，仅粘贴到你信任的 ACM Lab HTTPS 网站。服务器 IP 或平台验证限制可能导致
        Cookie 不可复用；插件不能绕过此限制，也不提供自动代交。
      </p>
    </section>
  )
}

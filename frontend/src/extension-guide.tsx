import React, { useState } from 'react'
import { api } from './api'

export function ExtensionGuide() {
  const [source, setSource] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  async function copyScript() {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const result = await api<{ source: string }>('/browser-userscript')
      setSource(result.source)
      try {
        await navigator.clipboard.writeText(result.source)
        setMessage('脚本源码已复制。请在 Tampermonkey 新建脚本，全选替换后保存。')
      } catch {
        setMessage(
          '浏览器未允许自动复制，请在下方源码框全选复制，再粘贴到 Tampermonkey 新建脚本中。',
        )
      }
    } catch (e: any) {
      setError(e.message || '暂时无法读取脚本，请重试。')
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="card extension-guide" id="cookie-helper">
      <span className="eyebrow">BROWSER COMPANION · USERSCRIPT</span>
      <h2>油猴脚本：获取平台 Cookie</h2>
      <p>
        使用 Tampermonkey 用户脚本管理器，在已登录的平台页面获取 Cookie，再回到本站粘贴。
        Chrome、Edge、Firefox 使用同一份脚本，无需安装 ACM Lab 未签名扩展。
      </p>
      <div className="extension-downloads">
        <a
          className="extension-download"
          href="https://www.tampermonkey.net/"
          target="_blank"
          rel="noreferrer"
        >
          ① 安装 Tampermonkey ↗
        </a>
        <button className="extension-download" disabled={busy} onClick={copyScript}>
          {busy ? '正在读取脚本…' : '② 复制 ACM Lab 脚本源码'}
        </button>
        <a
          className="extension-download"
          href="/api/browser-userscript/acm-lab-cookie-helper.user.js"
          download
        >
          下载 .user.js 文件（备用）↓
        </a>
      </div>
      {message && <p role="status">{message}</p>}
      {error && <p role="alert">{error}</p>}
      {source && (
        <details className="userscript-source">
          <summary>查看并手动复制脚本源码</summary>
          <textarea
            aria-label="ACM Lab 油猴脚本源码"
            readOnly
            value={source}
            onFocus={(e) => e.currentTarget.select()}
          />
        </details>
      )}
      <ol>
        <li>
          从管理器官网选择当前浏览器并安装 Tampermonkey
          5.3+。若浏览器要求启用用户脚本权限，请按管理器提示操作。
        </li>
        <li>
          点击「复制 ACM Lab 脚本源码」，打开 Tampermonkey → 添加新脚本，按 Ctrl+A
          全选默认内容并替换为复制的源码，按 Ctrl+S 保存。
          可用下载文件的完整内容替换。直接让管理器访问本站下载链接可能缺少本站登录会话，推荐复制源码。
        </li>
        <li>
          在同一浏览器正常登录平台并完成验证，刷新官方页面：<code>codeforces.com</code>、
          <code>www.luogu.com.cn</code>、<code>ac.nowcoder.com</code> 或 <code>atcoder.jp</code>。
          牛客建议在 <code>ac.nowcoder.com</code> 算法页面获取。
        </li>
        <li>使用页面上的「ACM Lab」助手或管理器脚本菜单获取 Cookie，核对平台后复制。</li>
        <li>
          回到下方「算法平台连接」，选择平台、填写自己的 handle / UID，粘贴 Cookie
          并保存；已有账号选择「编辑」。 然后点击「检测登录状态」。
        </li>
      </ol>
      <div className="cookie-helper-note">
        <strong>脚本读到 Cookie ≠ 服务器已登录</strong>
        <p>
          本脚本通过 Tampermonkey 的 Cookie API 读取；普通 document.cookie 无法读取 HttpOnly。
          HttpOnly 读取在部分管理器版本中受限，可能需要支持该能力的测试版；脚本检测不到 HttpOnly
          时会停止复制并提示 F12。 无法使用时直接按下方 F12 教程复制官方请求中完整的 Cookie
          值，不必更换浏览器。 即使 API 返回了 HttpOnly，检测结果仍以服务器实际验证为准。
        </p>
      </div>
      <p className="help">
        脚本仅支持普通窗口的默认会话。Firefox 容器标签页、隐私窗口和首方隔离不支持脚本复制，
        请在同一个已登录会话的 F12「网络」中复制官方请求的完整 Cookie，避免误取其他会话。
      </p>
      <p className="help">
        助手仅在支持的官方域名运行，主动点击后读取、复制，不自动向 ACM Lab 上传。 Cookie
        等同登录凭据，仅粘贴到你信任的 ACM Lab HTTPS 网站，使用后可覆盖系统剪贴板。
      </p>
      <p className="help">
        遇到验证码，请在官方平台页面亲自完成，再获取最新 Cookie、保存并重新检测。 验证可能与浏览器或
        IP 绑定，完成浏览器验证仍不保证服务器通过；本站不会转发或执行平台的验证页面。
      </p>
    </section>
  )
}

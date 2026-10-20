// ==UserScript==
// @name         ACM Lab · 平台 Cookie 助手
// @namespace    https://github.com/klbbs/ACM_PROJECT
// @version      1.0.1
// @updateURL    none
// @downloadURL  none
// @description  点击后仅复制当前算法平台 Cookie；不上传，不读取其他网站。不支持完整读取时提供 F12 备选。
// @match        https://codeforces.com/*
// @match        https://www.luogu.com.cn/*
// @match        https://www.nowcoder.com/*
// @match        https://ac.nowcoder.com/*
// @match        https://atcoder.jp/*
// @grant        GM.cookie
// @grant        GM_cookie
// @grant        GM_info
// @grant        GM_setClipboard
// @grant        GM_registerMenuCommand
// @noframes
// @run-in       normal-tabs
// @run-at       document-idle
// ==/UserScript==

(() => {
  'use strict'
  const platforms = Object.freeze({
    'codeforces.com': 'Codeforces',
    'www.luogu.com.cn': '洛谷',
    'www.nowcoder.com': '牛客',
    'ac.nowcoder.com': '牛客算法',
    'atcoder.jp': 'AtCoder',
  })
  const current = new URL(location.href)
  if (window.top !== window.self || current.protocol !== 'https:' || current.port || !Object.hasOwn(platforms, current.hostname)) return
  // Strip query/fragment tokens. Page-readable fallback never claims HttpOnly access.
  const cookieUrl = current.origin + current.pathname
  const fallback = '请按 F12 → 网络，刷新并选中平台自己的已登录请求，在请求标头中复制完整 Cookie；回到 ACM Lab 手动粘贴并检测登录。不要复制密码或 Authorization。'
  const host = document.createElement('aside')
  host.id = 'acm-lab-cookie-helper'
  host.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:2147483647;max-width:min(360px,calc(100vw - 32px))'
  const root = host.attachShadow({ mode: 'closed' })
  const style = document.createElement('style')
  style.textContent = ':host{font:14px/1.6 system-ui,sans-serif;color:#172338}section{background:white;border:2px solid #7457e8;border-radius:16px;padding:14px;box-shadow:0 5px 20px #17233830}button{font:inherit;cursor:pointer;border:0;border-radius:9px;padding:8px 12px;background:#7457e8;color:white}button:disabled{opacity:.6;cursor:wait}p{margin:8px 0 0;overflow-wrap:anywhere}summary{cursor:pointer}small{color:#58667c}header{display:flex;align-items:center;gap:10px;justify-content:space-between}.close{background:#edf0f5;color:#334;padding:2px 9px}'
  const section = document.createElement('section')
  const header = document.createElement('header')
  const title = document.createElement('strong')
  title.textContent = 'ACM Lab 1.0.1 · ' + platforms[current.hostname]
  const close = document.createElement('button')
  close.className = 'close'
  close.textContent = '×'
  close.setAttribute('aria-label', '关闭 Cookie 助手')
  close.addEventListener('click', () => host.remove())
  header.append(title, close)
  const button = document.createElement('button')
  button.textContent = '获取 Cookie 并复制'
  button.style.marginTop = '10px'
  const partial = document.createElement('button')
  partial.textContent = '复制当前可读 Cookie（可能不完整）'
  partial.hidden = true
  partial.style.marginTop = '10px'
  const diagnostic = document.createElement('p')
  diagnostic.setAttribute('role', 'note')
  diagnostic.style.fontSize = '12px'
  const status = document.createElement('p')
  status.setAttribute('role', 'status')
  status.textContent = '仅点击后读取当前平台。不上传；复制后请手动粘贴到 ACM Lab。'
  const details = document.createElement('details')
  const summary = document.createElement('summary')
  summary.textContent = '无法读取？查看 F12 备选'
  const help = document.createElement('p')
  help.textContent = '仅支持普通默认标签页；无痕、Firefox 容器及第一方隔离请使用 F12。Tampermonkey 稳定版可能不返回 HttpOnly；支持该能力的版本还需在该脚本的权限设置中允许 HttpOnly Cookie。没有 HttpOnly 标记不等于没有登录，但脚本不能保证凭据完整。API 为空或不可用时，仅尝试本页 document.cookie 可读部分，需你另点按钮确认复制；这不能读取 HttpOnly。' + fallback
  details.append(summary, help)
  section.append(header, button, partial, status, diagnostic, details)
  root.append(style, section)
  document.documentElement.append(host)

  function assertContext() {
    if (typeof GM_info === 'undefined' || GM_info.scriptHandler !== 'Tampermonkey')
      throw Error('无法确认油猴管理器与会话环境，请使用 F12。')
    if (!/^(?:[6-9]|[1-9]\d+)\.|^5\.(?:[3-9]|[1-9]\d+)(?:\.|$)/.test(String(GM_info.version || '')))
      throw Error('需要 Tampermonkey 5.3 及以上版本确认会话环境；当前版本请直接使用 F12。')
    if (GM_info.isIncognito || GM_info.isFirstPartyIsolation || (GM_info.container?.id && !['default', 'firefox-default', '0'].includes(GM_info.container.id)))
      throw Error('当前是无痕、容器或隔离会话，无法可靠确认 Cookie 所属会话，已停止读取。')
  }

  function readCookies() {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('油猴 API 读取超时')), 10000)
      const done = (cookies, error) => {
        clearTimeout(timer)
        if (error || !Array.isArray(cookies)) reject(Error('油猴 API 不可用或权限不足'))
        else resolve(cookies)
      }
      try {
        if (typeof GM !== 'undefined' && GM.cookie && typeof GM.cookie.list === 'function')
          Promise.resolve(GM.cookie.list({ url: cookieUrl })).then(cookies => done(cookies), () => done(null, true))
        else if (typeof GM_cookie !== 'undefined' && typeof GM_cookie.list === 'function')
          GM_cookie.list({ url: cookieUrl }, done)
        else done(null, true)
      } catch { done(null, true) }
    })
  }

  function filter(cookies) {
    const now = Date.now() / 1000
    const counts = { domain: 0, path: 0, partition: 0, expired: 0, invalid: 0 }
    const eligible = cookies.filter(cookie => {
      if (!cookie || typeof cookie !== 'object') { counts.invalid++; return false }
      const domain = String(cookie.domain || '').replace(/^\./, '')
      const correctDomain = domain && (cookie.hostOnly ? current.hostname === domain : current.hostname === domain || current.hostname.endsWith('.' + domain))
      if (!correctDomain) { counts.domain++; return false }
      const path = cookie.path || '/'
      if (typeof path !== 'string' || !(current.pathname === path || current.pathname.startsWith(path.endsWith('/') ? path : path + '/'))) { counts.path++; return false }
      if (cookie.firstPartyDomain || cookie.partitionKey) { counts.partition++; return false }
      // Managers may serialize session-cookie expiration as null/0/-1.
      // Only an explicit session flag overrides a numeric expired timestamp.
      const expiry = cookie.expirationDate
      const unexpired = cookie.session === true || expiry == null || (typeof expiry === 'number' && Number.isFinite(expiry) && expiry > now)
      if (!unexpired) { counts.expired++; return false }
      return true
    })
    return { eligible, counts }
  }

  function pageCookies() {
    try {
      // This is deliberately a partial fallback in the CURRENT page session.
      // It cannot read HttpOnly and is never represented as a complete capture.
      return document.cookie.split(';').filter(part => part.trim()).map(part => {
        const text = part.trim(), at = text.indexOf('=')
        if (at < 1) throw Error('Invalid page cookie')
        return { name: text.slice(0, at), value: text.slice(at + 1), domain: current.hostname,
          hostOnly: true, path: current.pathname, session: true, httpOnly: false }
      })
    } catch { return [] }
  }

  function serialize(cookies) {
    const sorted = [...cookies].sort((a, b) => (b.path || '/').length - (a.path || '/').length)
    const header = sorted.map(cookie => {
      if (typeof cookie.name !== 'string' || typeof cookie.value !== 'string' || !/^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/.test(cookie.name) || !/^[\x20-\x7e]*$/.test(cookie.value) || /[;\r\n]/.test(cookie.value))
        throw Error('Cookie 无法安全转换为请求标头。')
      return cookie.name + '=' + cookie.value
    }).join('; ')
    if (header.length > 16384) throw Error('Cookie 超过 ACM Lab 的 16 KiB 限制。')
    return { header, count: sorted.length, httpOnly: sorted.some(cookie => cookie.httpOnly === true) }
  }

  function copy(header) {
    return new Promise((resolve, reject) => {
      if (typeof GM_setClipboard !== 'function') return reject(Error('油猴剪贴板接口不可用。'))
      const timer = setTimeout(() => reject(Error('剪贴板未确认写入，请使用 F12 备选。')), 5000)
      try {
        GM_setClipboard(header, 'text', () => { clearTimeout(timer); resolve() })
      } catch { clearTimeout(timer); reject(Error('剪贴板写入失败。')) }
    })
  }

  let busy = false
  function samePage() {
    if (location.origin !== current.origin || location.pathname !== current.pathname)
      throw Error('页面地址已变化，请刷新后重试。')
  }
  async function capture(allowPartial = false) {
    if (busy) return
    busy = true
    button.disabled = partial.disabled = true
    partial.hidden = true
    diagnostic.textContent = ''
    status.textContent = '正在读取当前平台 Cookie…'
    try {
      samePage()
      assertContext()
      let cookies = [], apiState = '可用'
      try { cookies = await readCookies() } catch (error) { apiState = error.message }
      samePage()
      const { eligible, counts } = filter(cookies)
      let selected = eligible, source = '油猴 API'
      if (!selected.length) { selected = pageCookies(); source = '本页可读部分' }
      const version = String(GM_info.version || '').match(/^\d+(?:\.\d+){1,3}/)?.[0] || '未知'
      diagnostic.textContent = 'Tampermonkey ' + version + ' · API：' + apiState + ' · 返回 ' + cookies.length + ' 项，适用 ' + eligible.length + ' 项；过滤域名 ' + counts.domain + '、路径 ' + counts.path + '、分区 ' + counts.partition + '、过期 ' + counts.expired + '、无效 ' + counts.invalid + '；当前来源：' + source + '（' + selected.length + ' 项）。诊断不包含 Cookie 名称或值。'
      if (!selected.length)
        throw Error('油猴和当前页面都没有返回可用 Cookie；可能是 HttpOnly 权限/版本限制，也可能尚未登录，不能仅据此断言你未登录。')
      const captured = serialize(selected)
      if (!captured.httpOnly && !allowPartial) {
        partial.hidden = false
        status.textContent = '读到 ' + captured.count + ' 项可读 Cookie，未确认 HttpOnly 读取能力，完整性未知；不是登录失败结论。可另点「复制当前可读 Cookie」明确复制这些结果，再到 ACM Lab 检测；需要完整 Cookie 时使用 F12。'
        details.open = true
        return
      }
      samePage()
      await copy(captured.header)
      status.textContent = '已复制 ' + captured.count + ' 项（' + (captured.httpOnly ? '包含 HttpOnly' : '仅当前可读 Cookie，可能不完整') + '；来源：' + source + '）。请手动粘贴到 ACM Lab 并检测登录；复制成功不代表服务器已通过平台验证。' + (captured.httpOnly ? '' : '检测不通过时，请用 F12 复制完整请求 Cookie。')
    } catch (error) {
      // API errors are sanitized at their boundary; no credential values enter UI.
      status.textContent = error.message + ' ' + fallback
      details.open = true
    } finally {
      busy = false
      button.disabled = partial.disabled = false
    }
  }
  button.addEventListener('click', () => capture(false))
  partial.addEventListener('click', () => capture(true))
  if (typeof GM_registerMenuCommand === 'function') GM_registerMenuCommand('ACM Lab：获取当前平台 Cookie', () => {
    if (!host.isConnected) document.documentElement.append(host)
    return capture(false)
  })
})()

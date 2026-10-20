// ==UserScript==
// @name         ACM Lab · 平台 Cookie 助手
// @namespace    https://github.com/klbbs/ACM_PROJECT
// @version      1.0.0
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
  // Strip query/fragment tokens. Never read document.cookie: it excludes HttpOnly.
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
  title.textContent = 'ACM Lab · ' + platforms[current.hostname]
  const close = document.createElement('button')
  close.className = 'close'
  close.textContent = '×'
  close.setAttribute('aria-label', '关闭 Cookie 助手')
  close.addEventListener('click', () => host.remove())
  header.append(title, close)
  const button = document.createElement('button')
  button.textContent = '获取 Cookie 并复制'
  button.style.marginTop = '10px'
  const status = document.createElement('p')
  status.setAttribute('role', 'status')
  status.textContent = '仅点击后读取当前平台。不上传；复制后请手动粘贴到 ACM Lab。'
  const details = document.createElement('details')
  const summary = document.createElement('summary')
  summary.textContent = '无法读取？查看 F12 备选'
  const help = document.createElement('p')
  help.textContent = '仅支持普通默认标签页；无痕、Firefox 容器及第一方隔离环境请使用 F12。油猴的 Cookie API 与 HttpOnly 支持因版本和浏览器而异，稳定版可能无法完整读取；无需更换测试版，直接使用 F12。' + fallback
  details.append(summary, help)
  section.append(header, button, status, details)
  root.append(style, section)
  document.documentElement.append(host)

  function readCookies() {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('读取 Cookie 超时。')), 10000)
      const done = (cookies, error) => {
        clearTimeout(timer)
        if (error || !Array.isArray(cookies)) reject(Error('油猴 Cookie 权限未开启或当前浏览器不支持。'))
        else resolve(cookies)
      }
      try {
        if (typeof GM_info === 'undefined' || GM_info.scriptHandler !== 'Tampermonkey') {
          done(null, true)
        } else if (!/^(?:[6-9]|[1-9]\d+)\.|^5\.(?:[3-9]|[1-9]\d+)(?:\.|$)/.test(String(GM_info.version || ''))) {
          clearTimeout(timer)
          reject(Error('需要 Tampermonkey 5.3 及以上版本确认会话环境；当前版本请直接使用 F12。'))
        } else if (GM_info.isIncognito || GM_info.isFirstPartyIsolation || (GM_info.container?.id && !['default', 'firefox-default', '0'].includes(GM_info.container.id))) {
          clearTimeout(timer)
          reject(Error('当前是无痕、容器或隔离会话，无法可靠确认 Cookie 所属会话，已停止读取。'))
        } else if (typeof GM !== 'undefined' && GM.cookie && typeof GM.cookie.list === 'function') {
          Promise.resolve(GM.cookie.list({ url: cookieUrl })).then(cookies => done(cookies), () => done(null, true))
        } else if (typeof GM_cookie !== 'undefined' && typeof GM_cookie.list === 'function') {
          GM_cookie.list({ url: cookieUrl }, done)
        } else done(null, true)
      } catch { done(null, true) }
    })
  }

  function serialize(cookies) {
    const now = Date.now() / 1000
    const eligible = cookies.filter(cookie => {
      const domain = String(cookie.domain || '').replace(/^\./, '')
      const correctDomain = domain && (cookie.hostOnly ? current.hostname === domain : current.hostname === domain || current.hostname.endsWith('.' + domain))
      const path = cookie.path || '/'
      const correctPath = current.pathname === path || current.pathname.startsWith(path.endsWith('/') ? path : path + '/')
      const unexpired = cookie.expirationDate === undefined || (typeof cookie.expirationDate === 'number' && Number.isFinite(cookie.expirationDate) && cookie.expirationDate > now)
      return correctDomain && correctPath && !cookie.firstPartyDomain && !cookie.partitionKey && unexpired
    })
    if (!eligible.length) throw Error('没有可复制的当前平台 Cookie，请先在此浏览器登录平台并刷新。')
    // An API existing is insufficient: some stable manager versions silently
    // omit HttpOnly. Reject such a capture instead of presenting it as complete.
    if (!eligible.some(cookie => cookie.httpOnly === true)) throw Error('当前油猴未返回任何 HttpOnly Cookie，无法确认登录凭据完整，已停止复制。')
    const sorted = [...eligible].sort((a, b) => (b.path || '/').length - (a.path || '/').length)
    const header = sorted.map(cookie => {
      if (typeof cookie.name !== 'string' || typeof cookie.value !== 'string' || !/^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/.test(cookie.name) || !/^[\x20-\x7e]*$/.test(cookie.value) || /[;\r\n]/.test(cookie.value))
        throw Error('Cookie 无法安全转换为请求标头。')
      return cookie.name + '=' + cookie.value
    }).join('; ')
    if (header.length > 16384) throw Error('Cookie 超过 ACM Lab 的 16 KiB 限制。')
    return { header, count: sorted.length }
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
  async function capture() {
    if (busy) return
    busy = true
    button.disabled = true
    status.textContent = '正在读取当前平台 Cookie…'
    try {
      // Recheck location in case an SPA changed it after the panel was created.
      if (location.origin !== current.origin || location.pathname !== current.pathname) throw Error('页面地址已变化，请刷新后重试。')
      const captured = serialize(await readCookies())
      if (location.origin !== current.origin || location.pathname !== current.pathname) throw Error('页面地址已变化，请刷新后重试。')
      await copy(captured.header)
      status.textContent = '已复制 ' + captured.count + ' 项（包含 HttpOnly）。请手动粘贴到 ACM Lab 并检测登录；复制成功不代表服务器已通过平台验证。'
    } catch (error) {
      // Only local, predefined errors reach this block; never echo API errors or values.
      status.textContent = error.message + ' ' + fallback
      details.open = true
    } finally {
      busy = false
      button.disabled = false
    }
  }
  button.addEventListener('click', capture)
  if (typeof GM_registerMenuCommand === 'function') GM_registerMenuCommand('ACM Lab：获取当前平台 Cookie', () => {
    if (!host.isConnected) document.documentElement.append(host)
    return capture()
  })
})()

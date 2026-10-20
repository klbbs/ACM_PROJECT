import { identify, cookieHeader } from './cookies.js'

const api = globalThis.browser || globalThis.chrome
const $ = id => document.getElementById(id)
const capture = $('capture'), status = $('status'), fallback = $('fallback'), value = $('value')
let tab, platform
function message(text, error = false) {
  status.textContent = text
  status.classList.toggle('error', error)
}
function clear() { value.value = ''; fallback.hidden = true }
async function copy(header) {
  try {
    await navigator.clipboard.writeText(header)
    clear()
    return true
  } catch {
    value.value = header
    fallback.hidden = false
    return false
  }
}
try {
  ;[tab] = await api.tabs.query({ active: true, currentWindow: true })
  platform = identify(tab?.url)
  $('platform').textContent = platform ? `当前平台：${platform.name} · ${platform.host}` : '请在 HTTPS 官方平台页面打开插件：CF、洛谷、牛客、AtCoder。'
  capture.disabled = !platform
} catch { message('无法读取当前页面。请关闭弹窗，在官方平台重新点击插件。', true) }

capture.addEventListener('click', async () => {
  clear()
  message('')
  capture.disabled = true
  try {
    // Request inside the click handler, before any await: browsers require a user gesture.
    const granted = await api.permissions.request({ origins: [platform.origin + '/*'] })
    if (!granted) throw Error('未授权，未读取任何 Cookie。你也可以使用网站中的 F12 教程。')
    // Fail closed if the tab navigated away while the permission prompt was open.
    const current = await api.tabs.get(tab.id)
    if (identify(current.url)?.url !== platform.url) throw Error('当前页面已变化，请重新打开插件。')
    const details = { url: platform.url }
    // Firefox containers must use the current tab's store, not the default account.
    if (tab.cookieStoreId) details.storeId = tab.cookieStoreId
    else {
      const stores = await api.cookies.getAllCookieStores()
      const store = stores.find(s => s.tabIds.includes(tab.id))
      if (!store) throw Error('无法确认当前标签页的 Cookie 容器，未读取 Cookie。')
      details.storeId = store.id
    }
    const result = cookieHeader(await api.cookies.getAll(details))
    const copied = await copy(result.header)
    message(copied
      ? `已复制 ${result.count} 项 Cookie。请回到 ACM Lab 粘贴、保存并检测登录状态。${result.skipped ? '已排除无法在服务器复用的分区 Cookie。' : ''}`
      : '已获取 Cookie，但剪贴板写入失败，请在下方重试或手动复制。', !copied)
  } catch (error) {
    // Never echo browser errors which could include sensitive cookie data.
    const known = ['未授权', '当前页面已变化', '无法确认当前标签页', '此页面没有', 'Cookie 含', 'Cookie 超过']
    message(known.some(s => error.message?.startsWith(s)) ? error.message : '读取失败，请确认已登录并允许当前平台权限，再重新打开插件。', true)
  } finally { capture.disabled = false }
})
$('retry').addEventListener('click', async () => {
  if (!value.value) return
  if (await copy(value.value)) message('已复制，请回到 ACM Lab 粘贴、保存并检测登录状态。')
  else message('仍无法写入剪贴板，请手动复制下方文本。', true)
})
$('clear').addEventListener('click', () => { clear(); message('弹窗内容已清除；系统剪贴板请自行覆盖。') })
window.addEventListener('pagehide', clear)

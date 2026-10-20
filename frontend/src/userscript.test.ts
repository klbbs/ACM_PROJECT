import { afterEach, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
const source = readFileSync('../src/acmweb/userscripts/acm-lab-cookie-helper.user.js', 'utf8')
const nativeAttach = Element.prototype.attachShadow
const synthetic = (patch = {}) => ({ name: 'session', value: 'synthetic-secret', httpOnly: true, domain: '.luogu.com.cn', path: '/', ...patch })

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.getElementById('acm-lab-cookie-helper')?.remove()
})

function install(options: { cookies?: any[], url?: string, manager?: string, missingAPI?: boolean, clipboardFailure?: boolean, callbackAPI?: boolean, apiError?: boolean, context?: Record<string, any> } = {}) {
  const url = new URL(options.url || 'https://www.luogu.com.cn/problem/P1000?secret=removed#token')
  vi.stubGlobal('location', url)
  vi.spyOn(Element.prototype, 'attachShadow').mockImplementation(function (this: Element) { return nativeAttach.call(this, { mode: 'open' }) })
  vi.stubGlobal('GM_info', { scriptHandler: options.manager || 'Tampermonkey', version: '5.5.1', ...options.context })
  const list = vi.fn().mockImplementation(() => options.apiError ? Promise.reject(Error('secret-session-must-not-leak')) : Promise.resolve(options.cookies || [synthetic()]))
  const callback = vi.fn().mockImplementation((_details, done) => done(options.cookies || [synthetic()], options.apiError ? 'secret-session-must-not-leak' : undefined))
  vi.stubGlobal('GM', options.missingAPI || options.callbackAPI ? {} : { cookie: { list } })
  vi.stubGlobal('GM_cookie', options.callbackAPI ? { list: callback } : undefined)
  const clipboard = vi.fn().mockImplementation((_text, _type, done) => {
    if (options.clipboardFailure) throw Error('secret-clipboard-error')
    done()
  })
  vi.stubGlobal('GM_setClipboard', clipboard)
  const menu = vi.fn()
  vi.stubGlobal('GM_registerMenuCommand', menu)
  new Function(source)()
  const panel = document.getElementById('acm-lab-cookie-helper')?.shadowRoot
  const capture = async () => {
    (panel!.querySelector('button:not(.close)') as HTMLButtonElement).click()
    await new Promise(resolve => setTimeout(resolve, 0))
  }
  return { list, callback, clipboard, menu, panel, capture }
}

it('reads only after a user click and copies HttpOnly using stripped current official URL', async () => {
  const { list, clipboard, menu, panel, capture } = install()
  expect(list).not.toHaveBeenCalled()
  expect(clipboard).not.toHaveBeenCalled()
  expect(menu).toHaveBeenCalledTimes(1)
  await capture()
  expect(list).toHaveBeenCalledWith({ url: 'https://www.luogu.com.cn/problem/P1000' })
  expect(clipboard).toHaveBeenCalledWith('session=synthetic-secret', 'text', expect.any(Function))
  expect(panel!.querySelector('[role=status]')!.textContent).toContain('包含 HttpOnly')
  expect(panel!.textContent).not.toContain('synthetic-secret')
})

it.each([
  ['codeforces.com', 'codeforces.com'],
  ['www.luogu.com.cn', 'luogu.com.cn'],
  ['www.nowcoder.com', 'nowcoder.com'],
  ['ac.nowcoder.com', 'nowcoder.com'],
  ['atcoder.jp', 'atcoder.jp'],
])('captures only the active official host %s through the manager Cookie API', async (host, domain) => {
  const { list, clipboard, panel, capture } = install({
    url: `https://${host}/?private=drop#private`,
    cookies: [synthetic({ domain: '.' + domain })],
  })
  expect(list).not.toHaveBeenCalled()
  await capture()
  expect(list).toHaveBeenCalledWith({ url: `https://${host}/` })
  expect(clipboard).toHaveBeenCalledWith('session=synthetic-secret', 'text', expect.any(Function))
  expect(panel!.querySelector('[role=status]')!.textContent).toContain('包含 HttpOnly')
})

it('restores the closed helper when the user invokes the manager menu', async () => {
  const { list, clipboard, panel, menu } = install()
  ;(panel!.querySelector('.close') as HTMLButtonElement).click()
  expect(document.getElementById('acm-lab-cookie-helper')).toBeNull()
  expect(list).not.toHaveBeenCalled()
  await menu.mock.calls[0][1]()
  expect(document.getElementById('acm-lab-cookie-helper')).not.toBeNull()
  expect(clipboard).toHaveBeenCalledTimes(1)
  expect(panel!.querySelector('[role=status]')!.textContent).toContain('已复制 1 项')
})

it('does not read when an old manager cannot report its browsing context reliably', async () => {
  const { list, clipboard, panel, capture } = install({ context: { version: '5.2.6196' } })
  await capture()
  expect(list).not.toHaveBeenCalled()
  expect(clipboard).not.toHaveBeenCalled()
  expect(panel!.querySelector('[role=status]')!.textContent).toContain('5.3')
})

it('fails clearly without copying partial non-HttpOnly results, missing API or another manager', async () => {
  for (const options of [{ cookies: [synthetic({ httpOnly: false })] }, { missingAPI: true }, { manager: 'Violentmonkey' }, { apiError: true }]) {
    const { clipboard, panel, capture } = install(options)
    await capture()
    expect(clipboard).not.toHaveBeenCalled()
    expect(panel!.querySelector('[role=status]')!.textContent).toContain('F12')
    expect(panel!.textContent).not.toContain('secret-session-must-not-leak')
    document.getElementById('acm-lab-cookie-helper')?.remove()
  }
})

it('supports the legacy callback Cookie API and filters unrelated, expired and partitioned values', async () => {
  const { callback, clipboard, capture } = install({ callbackAPI: true, cookies: [
    synthetic({ value: 'root' }), synthetic({ value: 'specific', path: '/problem' }),
    synthetic({ value: 'foreign', domain: 'evil.test' }), synthetic({ value: 'expired', expirationDate: 1 }),
    synthetic({ value: 'expired-epoch', expirationDate: 0 }),
    synthetic({ value: 'partitioned', partitionKey: { topLevelSite: 'https://elsewhere.test' } }),
    synthetic({ value: 'unknown-partition', partitionKey: {} }),
    synthetic({ value: 'other-path', path: '/user' }), synthetic({ value: 'foreign-container', firstPartyDomain: 'other.test' }),
  ] })
  await capture()
  expect(callback).toHaveBeenCalledWith({ url: 'https://www.luogu.com.cn/problem/P1000' }, expect.any(Function))
  expect(clipboard).toHaveBeenCalledWith('session=specific; session=root', 'text', expect.any(Function))
})

it('rejects header injection, oversize headers and missing cookie values without copying', async () => {
  for (const value of ['unsafe; injected=value', 'unsafe\r\nheader', 'x'.repeat(16384), undefined]) {
    const { clipboard, panel, capture } = install({ cookies: [synthetic({ value })] })
    await capture()
    expect(clipboard).not.toHaveBeenCalled()
    expect(panel!.querySelector('[role=status]')!.textContent).toContain('F12')
    document.getElementById('acm-lab-cookie-helper')?.remove()
  }
})

it('does not activate on insecure, nonstandard-port, lookalike or unsupported hosts', () => {
  for (const url of ['http://www.luogu.com.cn/', 'https://www.luogu.com.cn:8443/', 'https://www.luogu.com.cn.evil.test/', 'https://sub.atcoder.jp/']) {
    const { list, panel, menu } = install({ url })
    expect(panel).toBeUndefined()
    expect(list).not.toHaveBeenCalled()
    expect(menu).not.toHaveBeenCalled()
  }
})

it('never exposes secrets on clipboard failure and refuses captures after SPA navigation', async () => {
  const { clipboard, list, panel, capture } = install({ clipboardFailure: true })
  await capture()
  expect(panel!.querySelector('[role=status]')!.textContent).toContain('剪贴板写入失败')
  expect(panel!.textContent).not.toContain('secret-clipboard-error')
  vi.stubGlobal('location', new URL('https://www.luogu.com.cn/user/123'))
  await capture()
  expect(list).toHaveBeenCalledTimes(1)
  expect(clipboard).toHaveBeenCalledTimes(1)
  expect(panel!.querySelector('[role=status]')!.textContent).toContain('地址已变化')
})

it.each([
  { isIncognito: true },
  { isFirstPartyIsolation: true },
  { container: { id: 'container-id-2', name: 'synthetic-isolated-container' } },
])('does not read a potentially different session in an isolated browsing context %j', async (context) => {
  const { list, clipboard, panel, capture } = install({ context })
  await capture()
  expect(list).not.toHaveBeenCalled()
  expect(clipboard).not.toHaveBeenCalled()
  expect(panel!.querySelector('[role=status]')!.textContent).toContain('已停止读取')
  expect(panel!.querySelector('[role=status]')!.textContent).toContain('F12')
})

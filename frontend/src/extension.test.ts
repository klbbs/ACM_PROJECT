import { afterEach, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
const source = (name: string) => readFileSync('../src/acmweb/browser_extension/' + name, 'utf8')
const { identify, cookieHeader } = new Function(
  source('cookies.js').replaceAll('export ', '') + '\nreturn { identify, cookieHeader }',
)()
const popupSource = source('popup.js')

afterEach(() => {
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

it('only recognizes supported official HTTPS hosts and drops query tokens', () => {
  for (const host of [
    'codeforces.com',
    'www.luogu.com.cn',
    'ac.nowcoder.com',
    'www.nowcoder.com',
    'atcoder.jp',
  ]) {
    expect(identify(`https://${host}/profile?token=secret#fragment`).url).toBe(
      `https://${host}/profile`,
    )
  }
  for (const url of [
    'http://codeforces.com/',
    'https://codeforces.com.evil.test/',
    'https://evil.test/?codeforces.com',
    'https://sub.codeforces.com/',
    'https://codeforces.com:8443/',
    'file:///tmp/cf',
    'invalid',
  ])
    expect(identify(url)).toBeNull()
})

it('preserves HttpOnly cookies, browser duplicate-name path ordering and safe ASCII values', () => {
  const output = cookieHeader([
    { name: 'session', value: 'root', path: '/', httpOnly: true },
    { name: 'session', value: 'local', path: '/contest/' },
    { name: 'quoted', value: '"hello, world"', path: '/' },
    {
      name: 'partitioned',
      value: 'must-not-copy',
      partitionKey: { topLevelSite: 'https://other.test' },
    },
    { name: 'isolated', value: 'must-not-copy', firstPartyDomain: 'another.test' },
  ])
  expect(output).toEqual({
    header: 'session=local; session=root; quoted="hello, world"',
    count: 3,
    skipped: 2,
  })
  expect(() => cookieHeader([])).toThrow('没有可复制')
  expect(() => cookieHeader([{ name: 'session', value: 'unsafe\r\nheader' }])).toThrow('字符')
  expect(() => cookieHeader([{ name: 'session', value: 'x; injected=y' }])).toThrow('字符')
  expect(() => cookieHeader([{ name: 'session', value: 'x'.repeat(16384) }])).toThrow('16 KiB')
})

async function popup(
  options: {
    denied?: boolean
    clipboardError?: boolean
    navigated?: boolean
    container?: boolean
    unsupported?: boolean
  } = {},
) {
  document.body.innerHTML =
    '<p id="platform"></p><button id="capture" disabled></button><p id="status"></p><section id="fallback" hidden><textarea id="value"></textarea><button id="retry"></button><button id="clear"></button></section>'
  const official = {
    id: 5,
    url: options.unsupported ? 'https://other.test/' : 'https://codeforces.com/profile/demo',
    ...(options.container ? { cookieStoreId: 'firefox-container-2' } : {}),
  }
  const api = {
    tabs: {
      query: vi.fn().mockResolvedValue([official]),
      get: vi
        .fn()
        .mockResolvedValue(
          options.navigated ? { ...official, url: 'https://evil.test/' } : official,
        ),
    },
    permissions: { request: vi.fn().mockResolvedValue(!options.denied) },
    cookies: {
      getAll: vi
        .fn()
        .mockResolvedValue([{ name: 'session', value: 'synthetic-only', httpOnly: true }]),
      getAllCookieStores: vi.fn().mockResolvedValue([{ id: 'normal-store', tabIds: [5] }]),
    },
  }
  vi.stubGlobal('chrome', api)
  const writeText = vi.fn().mockImplementation(async () => {
    if (options.clipboardError) throw Error('denied')
  })
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
  await new AsyncFunction('identify', 'cookieHeader', popupSource.replace(/^import .*\n/, ''))(
    identify,
    cookieHeader,
  )
  const click = async (id: string) => {
    document.getElementById(id)!.click()
    await new Promise((resolve) => setTimeout(resolve, 15))
  }
  return { api, writeText, click }
}

it('requires an explicit click, requests only the current host, copies HttpOnly and selects the active cookie store', async () => {
  const { api, writeText, click } = await popup()
  expect(api.cookies.getAll).not.toHaveBeenCalled()
  await click('capture')
  expect(api.permissions.request).toHaveBeenCalledWith({ origins: ['https://codeforces.com/*'] })
  expect(api.cookies.getAll).toHaveBeenCalledWith({
    url: 'https://codeforces.com/profile/demo',
    storeId: 'normal-store',
  })
  expect(writeText).toHaveBeenCalledWith('session=synthetic-only')
  expect(document.getElementById('status')!.textContent).toContain('已复制 1 项')
  expect((document.getElementById('value') as HTMLTextAreaElement).value).toBe('')
})

it('uses the Firefox container and exposes a manual-copy fallback only after clipboard failure', async () => {
  const { api, writeText, click } = await popup({ container: true, clipboardError: true })
  await click('capture')
  expect(api.cookies.getAll).toHaveBeenCalledWith({
    url: 'https://codeforces.com/profile/demo',
    storeId: 'firefox-container-2',
  })
  expect(api.cookies.getAllCookieStores).not.toHaveBeenCalled()
  expect(document.getElementById('fallback')!.hidden).toBe(false)
  expect((document.getElementById('value') as HTMLTextAreaElement).value).toBe(
    'session=synthetic-only',
  )
  writeText.mockResolvedValueOnce(undefined)
  await click('retry')
  expect(document.getElementById('fallback')!.hidden).toBe(true)
  expect((document.getElementById('value') as HTMLTextAreaElement).value).toBe('')
})

it('does not read cookies after a rejected permission or changed tab', async () => {
  for (const options of [{ denied: true }, { navigated: true }]) {
    const { api, writeText, click } = await popup(options)
    await click('capture')
    expect(api.cookies.getAll).not.toHaveBeenCalled()
    expect(writeText).not.toHaveBeenCalled()
  }
})

it('leaves unsupported pages disabled', async () => {
  const { api } = await popup({ unsupported: true })
  expect((document.getElementById('capture') as HTMLButtonElement).disabled).toBe(true)
  expect(api.permissions.request).not.toHaveBeenCalled()
})

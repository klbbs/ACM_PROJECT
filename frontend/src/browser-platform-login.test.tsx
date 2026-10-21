import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { BrowserPlatformLogin } from './browser-platform-login'
vi.mock('./api', () => ({ api: vi.fn() }))
import { api } from './api'

let host: HTMLDivElement
let root: Root
const picture = 'data:image/png;base64,c3ludGhldGlj'
const pending = (extra = {}) => ({
  login_id: 'session-1',
  status: 'human_required',
  width: 1000,
  height: 720,
  expires_in: 300,
  ...extra,
})
function backend(overrides: Record<string, unknown> = {}) {
  vi.mocked(api).mockImplementation(async (path) => {
    const operation = path.split('/').at(-1)!
    if (operation in overrides) return overrides[operation] as never
    return (
      operation === 'start'
        ? pending()
        : operation === 'image'
          ? { image: picture }
          : operation === 'cancel'
            ? { status: 'cancelled' }
            : pending()
    ) as never
  })
}
async function render(
  platform: 'codeforces' | 'atcoder' | 'luogu' = 'codeforces',
  onBound = vi.fn(),
) {
  await act(async () => root.render(<BrowserPlatformLogin platform={platform} onBound={onBound} />))
  return onBound
}
async function click(text: string) {
  await act(async () =>
    Array.from(host.querySelectorAll('button'))
      .find((button) => button.textContent === text)!
      .click(),
  )
}
async function fill(suffix: 'expected' | 'text', value: string) {
  await act(async () => {
    const input = host.querySelector<HTMLInputElement>(`input[id$="-${suffix}"]`)!
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
beforeEach(() => {
  vi.useFakeTimers()
  vi.mocked(api).mockReset()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  backend()
  // jsdom does not implement the browser's native dialog top layer.
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute('open', '')
  })
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.removeAttribute('open')
    this.dispatchEvent(new Event('close'))
  })
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

it('starts directly, refreshes the initial image once after painting, then only on demand', async () => {
  const bound = await render()
  expect(host.querySelector('input[type="checkbox"]')).toBeNull()
  expect(api).not.toHaveBeenCalled()
  await fill('expected', ' fixture ')
  await click('打开安全登录窗口')
  expect(api).toHaveBeenNthCalledWith(1, '/platform-login/browser/start', 'POST', {
    platform: 'codeforces',
    expected_handle: 'fixture',
  })
  expect(host.querySelector('img')!.src).toBe(picture)
  await act(async () => vi.advanceTimersByTimeAsync(45000))
  expect(vi.mocked(api).mock.calls.filter(([path]) => path.endsWith('/image'))).toHaveLength(2)
  expect(vi.mocked(api).mock.calls.filter(([path]) => path.endsWith('/status'))).toHaveLength(3)
  expect(bound).not.toHaveBeenCalled()
  await click('刷新窗口截图')
  expect(vi.mocked(api).mock.calls.filter(([path]) => path.endsWith('/image'))).toHaveLength(3)
})

it('maps a scaled screenshot click to original pixels, clamps edges and supports remote keys', async () => {
  await render()
  await click('打开安全登录窗口')
  const image = host.querySelector('img')!
  vi.spyOn(image, 'getBoundingClientRect').mockReturnValue({
    left: 10,
    top: 20,
    width: 500,
    height: 360,
  } as DOMRect)
  await act(async () =>
    image.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 260, clientY: 200 })),
  )
  expect(api).toHaveBeenCalledWith('/platform-login/browser/input', 'POST', {
    login_id: 'session-1',
    kind: 'click',
    x: 500,
    y: 360,
  })
  await act(async () =>
    image.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 700, clientY: 800 })),
  )
  expect(api).toHaveBeenCalledWith('/platform-login/browser/input', 'POST', {
    login_id: 'session-1',
    kind: 'click',
    x: 999,
    y: 719,
  })
  await click('Control+A')
  expect(api).toHaveBeenCalledWith('/platform-login/browser/input', 'POST', {
    login_id: 'session-1',
    kind: 'key',
    key: 'Control+A',
  })
  await click('放大登录页面')
  expect(image.classList.contains('zoomed')).toBe(true)
})

it('masks entered secrets and clears the input before waiting for the request', async () => {
  await render()
  await click('打开安全登录窗口')
  let finish!: (value: unknown) => void
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve
      }),
  )
  await fill('text', 'synthetic secret')
  const input = host.querySelector<HTMLInputElement>('input[id$="-text"]')!
  expect(input.type).toBe('password')
  await act(async () =>
    host
      .querySelector('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })),
  )
  expect(input.value).toBe('')
  expect(api).toHaveBeenLastCalledWith('/platform-login/browser/input', 'POST', {
    login_id: 'session-1',
    kind: 'text',
    text: 'synthetic secret',
  })
  await act(async () => finish(pending()))
  expect(input.value).toBe('')
  expect(host.textContent).not.toContain('synthetic secret')
})

it('binds only when the official status returns an identified account', async () => {
  const bound = await render()
  await click('打开安全登录窗口')
  await click('检测登录并绑定')
  expect(bound).not.toHaveBeenCalled()
  backend({ confirm: { status: 'authenticated', account: { handle: 'fixture' } } })
  await click('检测登录并绑定')
  expect(bound).toHaveBeenCalledOnce()
  expect(bound).toHaveBeenCalledWith({ handle: 'fixture' })
  expect(host.textContent).toContain('已绑定账号：fixture')
  expect(host.querySelector('img')).toBeNull()
  const calls = vi.mocked(api).mock.calls.length
  await act(async () => vi.advanceTimersByTimeAsync(60000))
  expect(api).toHaveBeenCalledTimes(calls)
})

it('does not claim success when an authenticated response lacks a verified account', async () => {
  const bound = await render()
  await click('打开安全登录窗口')
  backend({ confirm: { status: 'authenticated' } })
  await click('检测登录并绑定')
  expect(bound).not.toHaveBeenCalled()
  expect(host.querySelector('[role="alert"]')!.textContent).toContain('未返回已验证的账号')
  expect(host.querySelector('img')).not.toBeNull()
})

it.each(['expired', 'unavailable'])(
  'clears the image and does not bind a %s result',
  async (status) => {
    const bound = await render()
    await click('打开安全登录窗口')
    backend({ status: { status, message: '服务器说明' } })
    await act(async () => vi.advanceTimersByTimeAsync(15000))
    expect(host.querySelector('img')).toBeNull()
    expect(host.textContent).toContain('尚未绑定账号')
    expect(host.textContent).toContain('服务器说明')
    expect(bound).not.toHaveBeenCalled()
    expect(api).toHaveBeenCalledWith('/platform-login/browser/cancel', 'POST', {
      login_id: 'session-1',
    })
  },
)

it('cancels promptly, ignores a late successful confirmation and cleans orphan starts', async () => {
  const bound = await render()
  await click('打开安全登录窗口')
  let finish!: (value: unknown) => void
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve
      }),
  )
  await click('检测登录并绑定')
  await click('取消安全登录')
  await act(async () => finish({ status: 'authenticated', account: { handle: 'late' } }))
  expect(bound).not.toHaveBeenCalled()
  expect(host.querySelector('img')).toBeNull()
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve
      }),
  )
  await click('返回账号卡片')
  await click('打开安全登录窗口')
  await click('取消安全登录')
  await act(async () => finish(pending({ login_id: 'orphan' })))
  expect(api).toHaveBeenLastCalledWith('/platform-login/browser/cancel', 'POST', {
    login_id: 'orphan',
  })
  expect(host.textContent).toContain('已取消')
})

it('validates Luogu numeric UID before opening a session', async () => {
  await render('luogu')
  await fill('expected', 'not-a-uid')
  await click('打开安全登录窗口')
  expect(api).not.toHaveBeenCalled()
  expect(host.querySelector('[role="alert"]')!.textContent).toContain('数字 UID')
  await fill('expected', '12345')
  await click('打开安全登录窗口')
  expect(api).toHaveBeenCalledWith('/platform-login/browser/start', 'POST', {
    platform: 'luogu',
    expected_handle: '12345',
  })
})

it('cleans sessions on unmount and stops work after the local expiry deadline', async () => {
  await render()
  backend({ start: pending({ expires_in: 2 }) })
  await click('打开安全登录窗口')
  await act(async () => vi.advanceTimersByTimeAsync(2000))
  expect(host.querySelector('img')).toBeNull()
  expect(host.textContent).toContain('已过期')
  await click('返回账号卡片')
  await click('打开安全登录窗口')
  await act(async () => root.unmount())
  root = createRoot(host)
  expect(api).toHaveBeenLastCalledWith('/platform-login/browser/cancel', 'POST', {
    login_id: 'session-1',
  })
  const calls = vi.mocked(api).mock.calls.length
  await act(async () => vi.advanceTimersByTimeAsync(60000))
  expect(api).toHaveBeenCalledTimes(calls)
})

it('rejects remote image URLs and presents request errors without claiming a binding', async () => {
  const bound = await render()
  backend({ image: { image: 'https://example.invalid/screenshot.png' } })
  await click('打开安全登录窗口')
  expect(host.querySelector('img')).toBeNull()
  expect(host.querySelector('[role="alert"]')!.textContent).toContain('未能读取')
  expect(bound).not.toHaveBeenCalled()
})

it('renews the idle deadline from input responses while respecting the server remaining lifetime', async () => {
  await render()
  backend({
    start: pending({ expires_in: 20 }),
    input: pending({ expires_in: 40 }),
    status: pending({ expires_in: 35 }),
  })
  await click('打开安全登录窗口')
  await act(async () => vi.advanceTimersByTimeAsync(10000))
  await click('Tab')
  await act(async () => vi.advanceTimersByTimeAsync(15000))
  expect(host.querySelector('img')).not.toBeNull()
  // The status at t=25 returns 35 remaining, not an unbounded fresh 300 seconds.
  backend({ status: pending({ expires_in: 1 }) })
  await act(async () => vi.advanceTimersByTimeAsync(15000))
  expect(host.querySelector('img')).not.toBeNull()
  await act(async () => vi.advanceTimersByTimeAsync(1000))
  expect(host.querySelector('img')).toBeNull()
  expect(host.textContent).toContain('已过期')
})

it('preserves the cancellation id on failure, blocks restarting and ignores a late verified response', async () => {
  const bound = await render()
  await click('打开安全登录窗口')
  let finish!: (value: unknown) => void
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve
      }),
  )
  await click('检测登录并绑定')
  vi.mocked(api).mockRejectedValueOnce(new Error('network unavailable'))
  await click('取消安全登录')
  expect(host.querySelector('img')).toBeNull()
  expect(host.textContent).toContain('尚未确认服务器窗口已关闭')
  expect(
    [...host.querySelectorAll('button')].some(
      (button) => button.textContent === '打开安全登录窗口',
    ),
  ).toBe(false)
  await act(async () => finish({ status: 'authenticated', account: { handle: 'late' } }))
  expect(bound).not.toHaveBeenCalled()
  await click('重试取消安全登录')
  expect(api).toHaveBeenLastCalledWith('/platform-login/browser/cancel', 'POST', {
    login_id: 'session-1',
  })
  expect(host.textContent).toContain('服务器窗口已关闭')
  await click('返回账号卡片')
  expect(
    [...host.querySelectorAll('button')].some(
      (button) => button.textContent === '打开安全登录窗口',
    ),
  ).toBe(true)
})

it('can retry cancellation when an in-flight start returned an id but cancellation failed', async () => {
  await render()
  let finish!: (value: unknown) => void
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve
      }),
  )
  await click('打开安全登录窗口')
  await click('取消安全登录')
  vi.mocked(api).mockRejectedValueOnce(new Error('cancel offline'))
  await act(async () => finish(pending({ login_id: 'orphan-id' })))
  expect(host.textContent).toContain('重试取消安全登录')
  await click('重试取消安全登录')
  expect(api).toHaveBeenLastCalledWith('/platform-login/browser/cancel', 'POST', {
    login_id: 'orphan-id',
  })
  expect(host.querySelector('img')).toBeNull()
})

it('retains the failed cancellation until its deadline, then allows another login', async () => {
  await render()
  backend({ start: pending({ expires_in: 2 }) })
  await click('打开安全登录窗口')
  vi.mocked(api).mockRejectedValueOnce(new Error('cancel offline'))
  await click('取消安全登录')
  await act(async () => vi.advanceTimersByTimeAsync(2000))
  expect(host.textContent).toContain('已超过有效期')
  expect(host.textContent).not.toContain('重试取消安全登录')
  await click('返回账号卡片')
  expect(
    [...host.querySelectorAll('button')].some(
      (button) => button.textContent === '打开安全登录窗口',
    ),
  ).toBe(true)
})

it('matches the server input limit and rejects oversized text without clearing the edit', async () => {
  await render()
  await click('打开安全登录窗口')
  const input = host.querySelector<HTMLInputElement>('input[id$="-text"]')!
  expect(input.maxLength).toBe(512)
  await fill('text', 'x'.repeat(513))
  const calls = vi.mocked(api).mock.calls.length
  await act(async () =>
    host
      .querySelector('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })),
  )
  expect(api).toHaveBeenCalledTimes(calls)
  expect(input.value).toHaveLength(513)
  expect(host.textContent).toContain('每次最多输入 512 个字符')
  await fill('text', 'x'.repeat(512))
  await click('发送文字')
  expect(input.value).toBe('')
})

it('opens a named modal with initial focus and loading before startup settles, then shows failure', async () => {
  await render('atcoder')
  let rejectStart!: (reason: Error) => void
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise((_resolve, reject) => {
        rejectStart = reject
      }),
  )
  const opener = host.querySelector<HTMLButtonElement>('.browser-login-start button')!
  opener.focus()
  const originalOverflow = document.documentElement.style.overflow
  await click('打开安全登录窗口')
  const modal = host.querySelector('dialog')!
  expect(modal.open).toBe(true)
  expect(modal.showModal).toHaveBeenCalledOnce()
  expect(modal.querySelector('h2')!.textContent).toBe('AtCoder 安全登录窗口')
  expect(document.activeElement).toBe(modal.querySelector('h2'))
  expect(modal.querySelector('[role="status"]')!.textContent).toContain('正在打开')
  expect(modal.querySelector('.browser-login-loading')).not.toBeNull()
  expect(host.querySelector('.browser-login-start')).not.toBeNull()
  expect(opener.disabled).toBe(true)
  expect(document.documentElement.style.overflow).toBe('hidden')
  await act(async () => rejectStart(new Error('浏览器启动失败')))
  expect(modal.open).toBe(true)
  expect(modal.querySelector('[role="status"]')!.textContent).toContain('浏览器启动失败')
  await click('返回账号卡片')
  expect(modal.open).toBe(false)
  expect(document.activeElement).toBe(opener)
  expect(document.documentElement.style.overflow).toBe(originalOverflow)
  expect(host.querySelector('[role="status"]')!.closest('dialog')).toBeNull()
})

it('Escape during startup waits for confirmed cancellation before dismissing and restores focus', async () => {
  const bound = await render()
  let finishStart!: (value: unknown) => void
  let finishCancel!: (value: unknown) => void
  vi.mocked(api).mockImplementation(
    (path) =>
      new Promise((resolve) => {
        if (path.endsWith('/start')) finishStart = resolve
        if (path.endsWith('/cancel')) finishCancel = resolve
      }),
  )
  await click('打开安全登录窗口')
  const modal = host.querySelector('dialog')!
  const escape = new Event('cancel', { cancelable: true })
  await act(async () => {
    modal.dispatchEvent(escape)
    modal.dispatchEvent(new Event('cancel', { cancelable: true }))
  })
  expect(escape.defaultPrevented).toBe(true)
  expect(modal.open).toBe(true)
  expect(modal.textContent).toContain('正在取消')
  await act(async () => finishStart(pending({ login_id: 'late-start' })))
  expect(api).toHaveBeenLastCalledWith('/platform-login/browser/cancel', 'POST', {
    login_id: 'late-start',
  })
  expect(vi.mocked(api).mock.calls.filter(([path]) => path.endsWith('/cancel'))).toHaveLength(1)
  expect(modal.open).toBe(true)
  expect(host.querySelector('img')).toBeNull()
  await act(async () => finishCancel({ status: 'cancelled' }))
  expect(modal.open).toBe(false)
  expect(host.textContent).toContain('服务器窗口已关闭')
  expect(document.activeElement).toBe(host.querySelector('.browser-login-start button'))
  expect(bound).not.toHaveBeenCalled()
})

it('keeps a failed close visible and retryable until cancellation succeeds', async () => {
  await render()
  await click('打开安全登录窗口')
  const modal = host.querySelector('dialog')!
  vi.mocked(api).mockRejectedValueOnce(new Error('cancel offline'))
  await click('关闭')
  expect(modal.open).toBe(true)
  expect(modal.querySelector('[role="alert"]')!.textContent).toContain('cancel offline')
  expect(modal.textContent).toContain('重试取消安全登录')
  expect(host.querySelector('img')).toBeNull()
  await click('重试取消安全登录')
  expect(modal.open).toBe(false)
  expect(host.textContent).toContain('服务器窗口已关闭')
  expect(document.activeElement).toBe(host.querySelector('.browser-login-start button'))
})

it('allows a verified result to be dismissed while retaining the account result on its card', async () => {
  const bound = await render()
  await click('打开安全登录窗口')
  backend({ confirm: { status: 'authenticated', account: { handle: 'fixture' } } })
  await click('检测登录并绑定')
  const modal = host.querySelector('dialog')!
  expect(modal.open).toBe(true)
  expect(bound).toHaveBeenCalledOnce()
  await click('关闭')
  expect(modal.open).toBe(false)
  expect(host.querySelector('[role="status"]')!.textContent).toContain('已绑定账号：fixture')
  expect(vi.mocked(api).mock.calls.some(([path]) => path.endsWith('/cancel'))).toBe(false)
})

it('guards an unexpected native close so an active server session cannot remain hidden', async () => {
  await render()
  await click('打开安全登录窗口')
  const modal = host.querySelector('dialog')!
  let finishCancel!: (value: unknown) => void
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finishCancel = resolve
      }),
  )
  await act(async () => modal.close())
  expect(modal.open).toBe(true)
  expect(modal.textContent).toContain('正在取消')
  expect(api).toHaveBeenLastCalledWith('/platform-login/browser/cancel', 'POST', {
    login_id: 'session-1',
  })
  await act(async () => finishCancel({ status: 'cancelled' }))
  expect(modal.open).toBe(false)
})

it('restores page scrolling on unmount and cancels a late startup without reopening UI', async () => {
  await render()
  let finish!: (value: unknown) => void
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve
      }),
  )
  const originalOverflow = document.documentElement.style.overflow
  await click('打开安全登录窗口')
  await act(async () => root.unmount())
  root = createRoot(host)
  expect(document.documentElement.style.overflow).toBe(originalOverflow)
  await act(async () => finish(pending({ login_id: 'unmounted-start' })))
  expect(api).toHaveBeenLastCalledWith('/platform-login/browser/cancel', 'POST', {
    login_id: 'unmounted-start',
  })
  expect(host.querySelector('dialog')).toBeNull()
})

it('bounds a startup rejection during cancellation by the original maximum lifetime', async () => {
  await render()
  let rejectStart!: (reason: Error) => void
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise((_resolve, reject) => {
        rejectStart = reject
      }),
  )
  await click('打开安全登录窗口')
  const modal = host.querySelector('dialog')!
  await act(async () => modal.dispatchEvent(new Event('cancel', { cancelable: true })))
  await act(async () => vi.advanceTimersByTimeAsync(120000))
  await act(async () => rejectStart(new Error('network disconnected')))
  expect(modal.open).toBe(true)
  expect(modal.textContent).toContain('未取得窗口标识')
  expect(modal.textContent).not.toContain('已取消')
  expect(modal.textContent).toContain('等待窗口自动过期')
  await click('关闭')
  await act(async () => modal.dispatchEvent(new Event('cancel', { cancelable: true })))
  await act(async () => vi.advanceTimersByTimeAsync(179999))
  expect(modal.textContent).toContain('等待窗口自动过期')
  await act(async () => vi.advanceTimersByTimeAsync(1))
  expect(modal.textContent).toContain('已超过最长有效期')
  await click('返回账号卡片')
  expect(modal.open).toBe(false)
  expect(document.activeElement).toBe(host.querySelector('.browser-login-start button'))
  await click('打开安全登录窗口')
  expect(host.querySelector('img')).not.toBeNull()
})

it('bounds a startup that never settles and safely discards its late id after a newer start', async () => {
  await render()
  let finishOld!: (value: unknown) => void
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finishOld = resolve
      }),
  )
  await click('打开安全登录窗口')
  await click('关闭')
  await act(async () => vi.advanceTimersByTimeAsync(300000))
  expect(host.textContent).toContain('已超过最长有效期')
  await click('返回账号卡片')
  let finishNew!: (value: unknown) => void
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finishNew = resolve
      }),
  )
  await click('打开安全登录窗口')
  await act(async () => finishOld(pending({ login_id: 'old-id' })))
  expect(api).toHaveBeenLastCalledWith('/platform-login/browser/cancel', 'POST', {
    login_id: 'old-id',
  })
  expect(host.querySelector('dialog')!.open).toBe(true)
  expect(host.textContent).toContain('正在打开官方登录页面')
  await click('关闭')
  await act(async () => finishNew(pending({ login_id: 'new-id' })))
  expect(api).toHaveBeenLastCalledWith('/platform-login/browser/cancel', 'POST', {
    login_id: 'new-id',
  })
  expect(host.querySelector('dialog')!.open).toBe(false)
})

it('shows the specific startup failure as the main status and retries only on explicit action', async () => {
  await render('atcoder')
  vi.mocked(api).mockRejectedValueOnce(new Error('服务器尚未安装 Chromium，请联系管理员'))
  await click('打开安全登录窗口')
  const modal = host.querySelector('dialog')!
  expect(modal.open).toBe(true)
  expect(modal.querySelector('[role="status"]')!.textContent).toBe(
    '服务器尚未安装 Chromium，请联系管理员',
  )
  expect(modal.textContent).not.toContain('安全登录窗口未能打开')
  expect(modal.querySelector('[role="alert"]')).toBeNull()
  const retry = [...modal.querySelectorAll('button')].find(
    (button) => button.textContent === '重试打开登录窗口',
  )!
  expect(retry.disabled).toBe(false)
  await act(async () => vi.advanceTimersByTimeAsync(60000))
  expect(api).toHaveBeenCalledTimes(1)
  await click('重试打开登录窗口')
  expect(modal.open).toBe(true)
  expect(modal.showModal).toHaveBeenCalledOnce()
  expect(document.activeElement).toBe(modal.querySelector('h2'))
  expect(modal.querySelector('img')).not.toBeNull()
})

it('honors the server 429 cooldown without automatically retrying when it expires', async () => {
  const bound = await render('luogu')
  vi.mocked(api).mockRejectedValueOnce(
    Object.assign(new Error('请稍后重新开始浏览器登录'), {
      status: 429,
      retryAfterSeconds: 10,
    }),
  )
  await click('打开安全登录窗口')
  const modal = host.querySelector('dialog')!
  expect(modal.querySelector('[role="status"]')!.textContent).toBe('请稍后重新开始浏览器登录')
  const retry = [...modal.querySelectorAll('button')].find(
    (button) => button.textContent === '重试打开登录窗口',
  )!
  expect(retry.disabled).toBe(true)
  expect(modal.textContent).toContain('请等待 10 秒后重试')
  await click('重试打开登录窗口')
  expect(api).toHaveBeenCalledTimes(1)
  await act(async () => vi.advanceTimersByTimeAsync(9000))
  expect(retry.disabled).toBe(true)
  expect(modal.textContent).toContain('请等待 1 秒后重试')
  await act(async () => vi.advanceTimersByTimeAsync(1000))
  expect(retry.disabled).toBe(false)
  expect(api).toHaveBeenCalledTimes(1)
  await act(async () => vi.advanceTimersByTimeAsync(60000))
  expect(api).toHaveBeenCalledTimes(1)
  await click('重试打开登录窗口')
  expect(vi.mocked(api).mock.calls.filter(([path]) => path.endsWith('/start'))).toHaveLength(2)
  expect(modal.querySelector('img')).not.toBeNull()
  expect(bound).not.toHaveBeenCalled()
})

it('keeps a 429 cooldown after closing the dialog and restores focus without permitting early restart', async () => {
  await render()
  vi.mocked(api).mockRejectedValueOnce(
    Object.assign(new Error('浏览器登录服务繁忙'), {
      status: 429,
      retryAfterSeconds: 3,
    }),
  )
  await click('打开安全登录窗口')
  await click('返回账号卡片')
  const opener = host.querySelector<HTMLButtonElement>('.browser-login-start button')!
  expect(document.activeElement).toBe(opener)
  expect(opener.getAttribute('aria-disabled')).toBe('true')
  expect(opener.textContent).toBe('请等待 3 秒后再打开')
  await act(async () => opener.click())
  expect(api).toHaveBeenCalledTimes(1)
  expect(host.querySelector('dialog')!.open).toBe(false)
  await act(async () => vi.advanceTimersByTimeAsync(3000))
  expect(opener.getAttribute('aria-disabled')).toBeNull()
  expect(api).toHaveBeenCalledTimes(1)
  await click('打开安全登录窗口')
  expect(host.querySelector('dialog')!.open).toBe(true)
  expect(host.querySelector('img')).not.toBeNull()
})

it.each([0, undefined, -1, Number.NaN])(
  'handles a 429 retry interval of %s conservatively',
  async (retryAfterSeconds) => {
    await render()
    vi.mocked(api).mockRejectedValueOnce(
      Object.assign(new Error('请稍后重试'), {
        status: 429,
        retryAfterSeconds,
      }),
    )
    await click('打开安全登录窗口')
    const retry = [...host.querySelectorAll('button')].find(
      (button) => button.textContent === '重试打开登录窗口',
    )!
    expect(retry.disabled).toBe(retryAfterSeconds !== 0)
    if (retryAfterSeconds !== 0) {
      expect(host.textContent).toContain('请等待 10 秒后重试')
      await act(async () => vi.advanceTimersByTimeAsync(10000))
    }
    expect(retry.disabled).toBe(false)
    expect(api).toHaveBeenCalledTimes(1)
  },
)

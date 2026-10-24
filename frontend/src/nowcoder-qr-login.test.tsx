import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { NowcoderQrLogin } from './nowcoder-qr-login'
vi.mock('./api', async (importOriginal) => ({
  ...await importOriginal<typeof import('./api')>(),
  api: vi.fn(),
}))
import { api, ApiError } from './api'

let host: HTMLDivElement
let root: Root
const image = 'data:image/jpeg;base64,aGVsbG8='
const pending = () => ({
  login_id: 'test-session',
  status: 'waiting',
  qr_image: image,
  expires_at: Date.now() / 1000 + 120,
  poll_after_seconds: 3,
})
async function render(onBound = vi.fn()) {
  await act(async () => root.render(<NowcoderQrLogin handles={['123']} onBound={onBound} />))
  return onBound
}
async function click(text: string) {
  await act(async () =>
    Array.from(host.querySelectorAll('button'))
      .find((button) => button.textContent?.includes(text))!
      .click(),
  )
}
async function begin() {
  await click('获取微信登录二维码')
}
beforeEach(() => {
  vi.useFakeTimers()
  vi.mocked(api).mockReset()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.useRealTimers()
})
it('starts on the explicit action, explains session saving, supports existing UID, and binds only verified status', async () => {
  const onBound = await render()
  expect(host.querySelector('button')?.disabled).toBe(false)
  expect(host.querySelector('input[type="checkbox"]')).toBeNull()
  expect(host.textContent).toContain('点击获取即申请本人扫码登录，验证后保存会话至当前账户')
  expect(host.textContent).toContain('两种独立的绑定入口，任选其一即可')
  expect(host.textContent).toContain('无需手动复制或再导入 Cookie')
  expect(host.querySelector('input[type="password"], textarea')).toBeNull()
  expect(api).not.toHaveBeenCalled()
  await act(async () => {
    const select = host.querySelector('select')!
    select.value = '123'
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
  vi.mocked(api)
    .mockResolvedValueOnce(pending())
    .mockResolvedValueOnce({ login_id: 'test-session', status: 'authorizing' })
    .mockResolvedValueOnce({ login_id: 'test-session', status: 'verified', verified_uid: '123' })
  await begin()
  expect(api).toHaveBeenNthCalledWith(1, '/platform-login/nowcoder/start', 'POST', {
    consent: true,
    expected_handle: '123',
  })
  expect(host.querySelector('img')?.src).toBe(image)
  await act(async () => vi.advanceTimersByTimeAsync(3000))
  expect(onBound).not.toHaveBeenCalled()
  expect(host.textContent).toContain('正在验证牛客身份')
  await act(async () => vi.advanceTimersByTimeAsync(3000))
  expect(onBound).toHaveBeenCalledOnce()
  expect(host.textContent).toContain('已绑定 UID：123')
  expect(host.querySelector('img')).toBeNull()
  await act(async () => vi.advanceTimersByTimeAsync(9000))
  expect(api).toHaveBeenCalledTimes(3)
})
it('cancels immediately and ignores a late poll response', async () => {
  const onBound = await render()
  let finish!: (value: unknown) => void
  vi.mocked(api)
    .mockResolvedValueOnce(pending())
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    .mockResolvedValueOnce({ status: 'cancelled' })
  await begin()
  await act(async () => {
    vi.advanceTimersByTime(3000)
  })
  await click('取消扫码登录')
  expect(host.querySelector('img')).toBeNull()
  await act(async () => finish({ status: 'verified', verified_uid: '456' }))
  expect(onBound).not.toHaveBeenCalled()
  expect(host.textContent).toContain('已取消本次扫码')
  await act(async () => vi.advanceTimersByTimeAsync(15000))
  expect(api).toHaveBeenCalledTimes(3)
})
it('cancels an orphan start response after user cancels while requesting', async () => {
  await render()
  let finish!: (value: unknown) => void
  vi.mocked(api)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    .mockResolvedValue({ status: 'cancelled' })
  await begin()
  await click('取消扫码登录')
  await act(async () => finish(pending()))
  expect(api).toHaveBeenLastCalledWith('/platform-login/nowcoder/cancel', 'POST', {
    login_id: 'test-session',
  })
  expect(host.querySelector('img')).toBeNull()
})
it.each(['expired', 'failed'])('clears the QR and stops polling on %s', async (status) => {
  const onBound = await render()
  vi.mocked(api)
    .mockResolvedValueOnce(pending())
    .mockResolvedValueOnce({
      login_id: 'test-session',
      status,
      message: status === 'expired' ? '二维码已过期' : '身份验证失败',
    })
  await begin()
  await act(async () => vi.advanceTimersByTimeAsync(9000))
  expect(host.querySelector('img')).toBeNull()
  expect(onBound).not.toHaveBeenCalled()
  expect(api).toHaveBeenCalledTimes(2)
})
it('shows the backend safe failure reason and retains Cookie fallback guidance', async () => {
  await render()
  vi.mocked(api).mockRejectedValueOnce(Error('申请过于频繁，请稍后重试。'))
  await begin()
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('申请过于频繁，请稍后重试。')
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('导入浏览器 Cookie')
  expect(host.querySelector('img')).toBeNull()
})
it('cancels on unmount and never polls again', async () => {
  await render()
  vi.mocked(api).mockResolvedValueOnce(pending()).mockResolvedValue({ status: 'cancelled' })
  await begin()
  await act(async () => root.unmount())
  root = createRoot(host)
  await act(async () => vi.advanceTimersByTimeAsync(12000))
  expect(api).toHaveBeenLastCalledWith('/platform-login/nowcoder/cancel', 'POST', {
    login_id: 'test-session',
  })
  expect(api).toHaveBeenCalledTimes(2)
})
it('expires locally and rejects external QR image URLs', async () => {
  await render()
  vi.mocked(api)
    .mockResolvedValueOnce({ ...pending(), qr_image: 'https://untrusted.example/qr' })
    .mockResolvedValue({ status: 'cancelled' })
  await begin()
  expect(host.querySelector('img')).toBeNull()
  expect(host.querySelector('[role="alert"]')).not.toBeNull()
  vi.mocked(api)
    .mockResolvedValueOnce({ ...pending(), expires_at: Date.now() / 1000 + 1 })
    .mockResolvedValue({ status: 'cancelled' })
  await click('获取微信登录二维码')
  await act(async () => vi.advanceTimersByTimeAsync(3000))
  expect(host.textContent).toContain('二维码已过期')
  expect(host.querySelector('img')).toBeNull()
})

it.each([
  { login_id: 'test-session', status: 'verified' },
  { login_id: 'test-session', status: 'verified', verified_uid: '0' },
  { login_id: 'other-session', status: 'verified', verified_uid: '123' },
  { login_id: 'test-session', status: 'unexpected' },
])('does not claim a binding from an incomplete or mismatched poll result', async result => {
  const onBound = await render()
  vi.mocked(api).mockResolvedValueOnce(pending()).mockResolvedValueOnce(result).mockResolvedValue({ status: 'cancelled' })
  await begin()
  await act(async () => vi.advanceTimersByTimeAsync(3000))
  expect(onBound).not.toHaveBeenCalled()
  expect(host.textContent).not.toContain('牛客身份验证通过，已绑定 UID')
  expect(host.textContent).toContain('尚未确认绑定')
  expect(host.querySelector('img')).toBeNull()
})
it('shows a safe configuration conflict instead of hiding the binding failure', async () => {
  const onBound = await render()
  vi.mocked(api).mockResolvedValueOnce(pending()).mockRejectedValueOnce(Error('账号配置已变化，请重新扫码绑定')).mockResolvedValue({ status: 'cancelled' })
  await begin()
  await act(async () => vi.advanceTimersByTimeAsync(3000))
  expect(host.textContent).toContain('账号配置已变化，请重新扫码绑定')
  expect(onBound).not.toHaveBeenCalled()
  expect(host.querySelector('img')).toBeNull()
})
it('shows an expired start response without treating it as an unknown protocol failure', async () => {
  const onBound = await render()
  vi.mocked(api).mockResolvedValueOnce({ login_id: 'test-session', status: 'expired', message: '二维码获取期间已过期，请重新获取' })
  await begin()
  expect(host.textContent).toContain('二维码获取期间已过期')
  expect(host.querySelector('[role="alert"]')).toBeNull()
  expect(host.querySelector('img')).toBeNull()
  expect(onBound).not.toHaveBeenCalled()
  await act(async () => vi.advanceTimersByTimeAsync(9000))
  expect(api).toHaveBeenCalledTimes(1)
})

it.each([
  { name: 'network disconnect', error: new TypeError('Failed to fetch'), delay: 3 },
  { name: 'HTTP 408', error: new ApiError('请求超时', 408), delay: 3 },
  { name: 'HTTP 429', error: new ApiError('稍后重试', 429, 10), delay: 10 },
  { name: 'HTTP 503', error: new ApiError('暂时不可用', 503, 7), delay: 7 },
])('recovers from $name using the same QR and respects the retry interval', async ({ error, delay }) => {
  const onBound = await render()
  vi.mocked(api)
    .mockResolvedValueOnce(pending())
    .mockResolvedValueOnce({ login_id: 'test-session', status: 'authorizing', poll_after_seconds: 3 })
    .mockRejectedValueOnce(error)
    .mockResolvedValueOnce({ login_id: 'test-session', status: 'verified', verified_uid: '123' })
  await begin()
  await act(async () => vi.advanceTimersByTimeAsync(6000))
  expect(host.textContent).toContain('检测暂时失败，正在重试，无需重新扫码')
  expect(host.querySelector('img')?.src).toBe(image)
  expect(onBound).not.toHaveBeenCalled()
  expect(vi.mocked(api).mock.calls.filter(([path]) => path.endsWith('/start'))).toHaveLength(1)
  expect(vi.mocked(api).mock.calls.some(([path]) => path.endsWith('/cancel'))).toBe(false)
  await act(async () => vi.advanceTimersByTimeAsync(delay * 1000 - 1))
  expect(onBound).not.toHaveBeenCalled()
  await act(async () => vi.advanceTimersByTimeAsync(1))
  expect(onBound).toHaveBeenCalledOnce()
  expect(host.textContent).toContain('已绑定 UID：123')
  expect(vi.mocked(api).mock.calls.filter(([path]) => path.endsWith('/poll'))).toEqual([
    ['/platform-login/nowcoder/poll', 'POST', { login_id: 'test-session' }],
    ['/platform-login/nowcoder/poll', 'POST', { login_id: 'test-session' }],
    ['/platform-login/nowcoder/poll', 'POST', { login_id: 'test-session' }],
  ])
})

it('keeps retryable backend waiting and authorizing states alive without recreating a QR', async () => {
  const onBound = await render()
  vi.mocked(api)
    .mockResolvedValueOnce(pending())
    .mockResolvedValueOnce({ login_id: 'test-session', status: 'waiting', retryable: true, poll_after_seconds: 4,
      message: '牛客检测接口 HTTP 503，暂时无法确认状态。' })
    .mockResolvedValueOnce({ login_id: 'test-session', status: 'authorizing', retryable: true, poll_after_seconds: 4,
      message: '微信已确认，牛客身份尚未就绪。' })
    .mockResolvedValueOnce({ login_id: 'test-session', status: 'verified', verified_uid: '123' })
  await begin()
  await act(async () => vi.advanceTimersByTimeAsync(3000))
  expect(host.textContent).toContain('正在重试，无需重新扫码')
  expect(host.textContent).toContain('牛客检测接口 HTTP 503')
  expect(host.querySelector('img')?.src).toBe(image)
  await act(async () => vi.advanceTimersByTimeAsync(4000))
  expect(onBound).not.toHaveBeenCalled()
  expect(host.textContent).toContain('正在重试，无需重新扫码')
  expect(host.textContent).toContain('牛客身份尚未就绪')
  await act(async () => vi.advanceTimersByTimeAsync(4000))
  expect(onBound).toHaveBeenCalledOnce()
  expect(vi.mocked(api).mock.calls.filter(([path]) => path.endsWith('/start'))).toHaveLength(1)
})

it.each([401, 403, 404, 409])('stops and clears the QR on non-retryable HTTP %s', async (status) => {
  const onBound = await render()
  vi.mocked(api)
    .mockResolvedValueOnce(pending())
    .mockRejectedValueOnce(new ApiError('会话或配置已失效，请重新扫码', status))
    .mockResolvedValue({ status: 'cancelled' })
  await begin()
  await act(async () => vi.advanceTimersByTimeAsync(3000))
  expect(host.querySelector('img')).toBeNull()
  expect(host.textContent).toContain('会话或配置已失效')
  expect(onBound).not.toHaveBeenCalled()
  expect(api).toHaveBeenLastCalledWith('/platform-login/nowcoder/cancel', 'POST', { login_id: 'test-session' })
  const requests = vi.mocked(api).mock.calls.length
  await act(async () => vi.advanceTimersByTimeAsync(15000))
  expect(api).toHaveBeenCalledTimes(requests)
})

it('expires at the original deadline when Retry-After would postpone polling beyond it', async () => {
  const onBound = await render()
  vi.mocked(api)
    .mockResolvedValueOnce({ ...pending(), expires_at: Date.now() / 1000 + 8 })
    .mockRejectedValueOnce(new ApiError('暂时限流', 429, 20))
    .mockResolvedValue({ status: 'cancelled' })
  await begin()
  await act(async () => vi.advanceTimersByTimeAsync(3000))
  expect(host.textContent).toContain('正在重试，无需重新扫码')
  await act(async () => vi.advanceTimersByTimeAsync(4999))
  expect(host.querySelector('img')).not.toBeNull()
  await act(async () => vi.advanceTimersByTimeAsync(1))
  expect(host.querySelector('img')).toBeNull()
  expect(host.textContent).toContain('二维码已过期')
  expect(onBound).not.toHaveBeenCalled()
  expect(vi.mocked(api).mock.calls.filter(([path]) => path.endsWith('/poll'))).toHaveLength(1)
  const requests = vi.mocked(api).mock.calls.length
  await act(async () => vi.advanceTimersByTimeAsync(30000))
  expect(api).toHaveBeenCalledTimes(requests)
})

it('expires an in-flight poll and ignores its late verified response', async () => {
  const onBound = await render()
  let finish!: (value: unknown) => void
  vi.mocked(api)
    .mockResolvedValueOnce({ ...pending(), expires_at: Date.now() / 1000 + 5 })
    .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    .mockResolvedValue({ status: 'cancelled' })
  await begin()
  await act(async () => { vi.advanceTimersByTime(3000) })
  await act(async () => vi.advanceTimersByTimeAsync(2000))
  expect(host.textContent).toContain('二维码已过期')
  expect(host.querySelector('img')).toBeNull()
  await act(async () => finish({ login_id: 'test-session', status: 'verified', verified_uid: '123' }))
  expect(onBound).not.toHaveBeenCalled()
  expect(host.textContent).not.toContain('已绑定 UID')
})

it('does not extend the original deadline after authorizing responses advertise a later expiry', async () => {
  const onBound = await render()
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path.endsWith('/start')) return { ...pending(), expires_at: Date.now() / 1000 + 8 } as any
    if (path.endsWith('/poll')) return {
      login_id: 'test-session', status: 'authorizing', retryable: true,
      expires_at: Date.now() / 1000 + 120,
    } as any
    return { status: 'cancelled' } as any
  })
  await begin()
  await act(async () => vi.advanceTimersByTimeAsync(8000))
  expect(host.textContent).toContain('二维码已过期')
  expect(host.querySelector('img')).toBeNull()
  expect(onBound).not.toHaveBeenCalled()
  expect(vi.mocked(api).mock.calls.filter(([path]) => path.endsWith('/poll'))).toHaveLength(2)
})

it('cancels a retrying flow and ignores a later response without scheduling another poll', async () => {
  const onBound = await render()
  let finish!: (value: unknown) => void
  vi.mocked(api)
    .mockResolvedValueOnce(pending())
    .mockRejectedValueOnce(new TypeError('Failed to fetch'))
    .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    .mockResolvedValue({ status: 'cancelled' })
  await begin()
  await act(async () => vi.advanceTimersByTimeAsync(3000))
  expect(host.textContent).toContain('正在重试，无需重新扫码')
  await act(async () => { vi.advanceTimersByTime(3000) })
  await click('取消扫码登录')
  await act(async () => finish({ login_id: 'test-session', status: 'verified', verified_uid: '123' }))
  expect(onBound).not.toHaveBeenCalled()
  expect(host.textContent).toContain('已取消本次扫码')
  const requests = vi.mocked(api).mock.calls.length
  await act(async () => vi.advanceTimersByTimeAsync(12000))
  expect(api).toHaveBeenCalledTimes(requests)
})

it.each([null, undefined])('treats an empty poll body as a protocol failure rather than a retryable network error', async (result) => {
  const onBound = await render()
  vi.mocked(api)
    .mockResolvedValueOnce(pending())
    .mockResolvedValueOnce(result)
    .mockResolvedValue({ status: 'cancelled' })
  await begin()
  await act(async () => vi.advanceTimersByTimeAsync(3000))
  expect(host.querySelector('img')).toBeNull()
  expect(host.textContent).toContain('尚未确认绑定')
  expect(onBound).not.toHaveBeenCalled()
  const requests = vi.mocked(api).mock.calls.length
  await act(async () => vi.advanceTimersByTimeAsync(9000))
  expect(api).toHaveBeenCalledTimes(requests)
})

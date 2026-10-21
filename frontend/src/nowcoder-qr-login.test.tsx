import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { NowcoderQrLogin } from './nowcoder-qr-login'
vi.mock('./api', () => ({ api: vi.fn() }))
import { api } from './api'

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
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('下方 Cookie 绑定')
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

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { PlatformConnections } from './platform-connections'
vi.mock('./api', () => ({ api: vi.fn(), date: () => '刚刚', names: { codeforces: 'Codeforces' } }))
import { api } from './api'

it('checks saved credentials explicitly and discards successful status after configuration changes', async () => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  const onError = vi.fn()
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === '/platforms')
      return {
        items: [{ platform: 'codeforces', accounts: [{ handle: 'demo', has_cookie: true }] }],
      } as never
    if (path === '/accounts/checks') return { items: [] } as never
    if (path === '/accounts/check')
      return {
        platform: 'codeforces',
        handle: 'demo',
        status: 'authenticated',
        matched_account: 'demo',
        checked_at: Math.floor(Date.now() / 1000),
        message: '凭据匹配；仍需官方提交。',
      } as never
    throw Error('unexpected request')
  })
  try {
    await act(async () =>
      root.render(<PlatformConnections revision={1} run={vi.fn()} onError={onError} />),
    )
    expect(host.textContent).toContain('尚未检测')
    expect(host.textContent).not.toContain('已验证登录')
    const button = [...host.querySelectorAll('button')].find(
      (b) => b.textContent === '检测登录状态',
    )!
    await act(async () => button.click())
    expect(api).toHaveBeenCalledWith('/accounts/check', 'POST', {
      platform: 'codeforces',
      handle: 'demo',
    })
    expect(host.textContent).toContain('已验证登录')
    expect(host.textContent).toContain('不支持用 Cookie 自动代提交')
    await act(async () =>
      root.render(<PlatformConnections revision={2} run={vi.fn()} onError={onError} />),
    )
    expect(host.textContent).not.toContain('已验证登录')
    expect(host.textContent).toContain('尚未检测')
    expect(onError).not.toHaveBeenCalled()
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
})

it('expires the displayed verification instead of leaving a stale green status', async () => {
  vi.useFakeTimers()
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === '/platforms')
      return {
        items: [{ platform: 'codeforces', accounts: [{ handle: 'demo', has_cookie: true }] }],
      } as never
    if (path === '/accounts/checks')
      return {
        items: [
          {
            platform: 'codeforces',
            handle: 'demo',
            status: 'authenticated',
            checked_at: Math.floor(Date.now() / 1000),
          },
        ],
      } as never
    throw Error('unexpected request')
  })
  try {
    await act(async () =>
      root.render(<PlatformConnections revision={1} run={vi.fn()} onError={vi.fn()} />),
    )
    expect(host.textContent).toContain('已验证登录')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300006)
    })
    expect(host.textContent).not.toContain('已验证登录')
    expect(host.textContent).toContain('尚未检测')
  } finally {
    await act(async () => root.unmount())
    host.remove()
    vi.useRealTimers()
  }
})

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { PlatformConnections } from './platform-connections'
vi.mock('./api', () => ({ api: vi.fn(), date: () => '刚刚', names: { codeforces: 'Codeforces', luogu: '洛谷' } }))
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

it('guides a blocked Codeforces session to a fixed official page and forces a fresh check after user action', async () => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  vi.mocked(api).mockReset()
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
            status: 'blocked',
            reason: 'captcha',
            browser_action_required: true,
            browser_action_url: 'https://untrusted.example/challenge',
            message: '需要完成验证',
            checked_at: Math.floor(Date.now() / 1000),
          },
        ],
      } as never
    if (path === '/accounts/check')
      return {
        platform: 'codeforces',
        handle: 'demo',
        status: 'authenticated',
        checked_at: Math.floor(Date.now() / 1000),
        message: '账号已核实',
      } as never
    throw Error('unexpected request')
  })
  try {
    await act(async () =>
      root.render(<PlatformConnections revision={1} run={vi.fn()} onError={vi.fn()} />),
    )
    expect(host.textContent).toContain('需要在官方完成验证')
    expect(host.textContent).toContain('不等于 Cookie 过期')
    expect(host.querySelector<HTMLAnchorElement>('.platform-browser-action a')?.href).toBe(
      'https://codeforces.com/enter',
    )
    expect(host.innerHTML).not.toContain('untrusted.example')
    const retry = [...host.querySelectorAll('button')].find(
      (button) => button.textContent === '已处理，重新检测',
    )!
    await act(async () => retry.click())
    expect(api).toHaveBeenCalledWith('/accounts/check', 'POST', {
      platform: 'codeforces',
      handle: 'demo',
      force_refresh: true,
    })
    expect(host.querySelector('.platform-browser-action')).toBeNull()
    expect(host.textContent).toContain('已验证登录')
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
})

it.each([
  ['expired', '', '平台会话需要重新登录', '打开官方平台重新登录 ↗'],
  ['account_mismatch', '', '请切换为需要绑定的官方账号', '打开官方平台切换账号 ↗'],
  ['blocked', 'browser_challenge', '平台要求浏览器验证，请在官方完成', '打开官方平台处理验证 ↗'],
])('distinguishes browser recovery for %s / %s', async (status, reason, title, link) => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  vi.mocked(api)
    .mockReset()
    .mockImplementation(async (path: string) => {
      if (path === '/platforms')
        return {
          items: [{ platform: 'atcoder', accounts: [{ handle: 'demo', has_cookie: true }] }],
        } as never
      return {
        items: [
          {
            platform: 'atcoder',
            handle: 'demo',
            status,
            reason,
            browser_action_required: true,
            checked_at: Math.floor(Date.now() / 1000),
            guidance: ['这是服务端给出的安全下一步'],
            upstream_status: 200,
          },
        ],
      } as never
    })
  try {
    await act(async () =>
      root.render(<PlatformConnections revision={1} run={vi.fn()} onError={vi.fn()} />),
    )
    const action = host.querySelector('.platform-browser-action')!
    expect(action.querySelector('strong')?.textContent).toBe(title)
    expect(action.querySelector('a')?.textContent?.trim()).toBe(link)
    expect(host.textContent).toContain('平台 HTTP 200')
    expect(host.textContent).toContain('这是服务端给出的安全下一步')
    if (status !== 'blocked') {
      expect(action.textContent).not.toContain('验证码')
      expect(action.textContent).not.toContain('不等于 Cookie 过期')
    } else expect(action.textContent).toContain('不一定包含验证码')
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
})

it('shows platform throttling and waiting time without asking for an unrelated CAPTCHA', async () => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  vi.mocked(api)
    .mockReset()
    .mockImplementation(async (path: string) => {
      if (path === '/platforms')
        return {
          items: [{ platform: 'codeforces', accounts: [{ handle: 'demo', has_cookie: true }] }],
        } as never
      return {
        items: [
          {
            platform: 'codeforces',
            handle: 'demo',
            status: 'blocked',
            reason: 'rate_limited',
            browser_action_required: false,
            retry_after_seconds: 60,
            upstream_status: 429,
            checked_at: Math.floor(Date.now() / 1000),
            message: '请求受限',
          },
        ],
      } as never
    })
  try {
    await act(async () =>
      root.render(<PlatformConnections revision={1} run={vi.fn()} onError={vi.fn()} />),
    )
    expect(host.textContent).toContain('请求过于频繁')
    expect(host.textContent).toContain('平台 HTTP 429')
    expect(host.textContent).toContain('请至少等待 60 秒后重试')
    expect(host.querySelector('.platform-browser-action')).toBeNull()
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
})


it.each([
  ['blocked', 'access_restricted'],
  ['blocked', 'captcha'],
  ['blocked', 'browser_challenge'],
  ['unknown', 'network_error'],
  ['authenticated', 'identity_verified'],
])('keeps Luogu on public synchronization and official submission regardless of cached %s / %s', async (status, reason) => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  const run = vi.fn()
  vi.mocked(api).mockReset().mockImplementation(async (path: string) => {
    if (path === '/platforms') return { items: [{ platform: 'luogu', accounts: [{ handle: '42', has_cookie: true }] }] } as never
    if (path === '/accounts/checks') return { items: [{
      platform: 'luogu', handle: '42', status, reason,
      browser_action_required: true, browser_action_url: 'https://untrusted.example/challenge',
      checked_at: Math.floor(Date.now() / 1000), upstream_status: 418, retry_after_seconds: 10,
      message: '旧的访问限制或登录诊断', guidance: ['请完成验证码并重新获取Cookie后重试'],
    }] } as never
    throw Error('unexpected request')
  })
  try {
    await act(async () => root.render(<PlatformConnections revision={1} run={run} onError={vi.fn()} />))
    const card = host.querySelector('article')!
    expect(card.textContent).toContain('42 · 公开资料同步')
    expect(card.textContent).toContain('洛谷官方网页交题，再同步记录')
    for (const hidden of ['访问限制', '验证码', '重新检测', '检测登录状态', 'HTTP 418', '旧的', '已验证登录']) {
      expect(card.textContent).not.toContain(hidden)
    }
    expect(card.querySelector('.platform-browser-action')).toBeNull()
    expect(card.innerHTML).not.toContain('untrusted.example')
    expect(card.querySelector<HTMLAnchorElement>('a')?.href).toBe('https://www.luogu.com.cn/')
    const synchronize = [...card.querySelectorAll('button')].find(button => button.textContent === '同步此平台信息')!
    await act(async () => synchronize.click())
    expect(run).toHaveBeenCalledWith('/platforms/luogu/collect')
    expect(vi.mocked(api).mock.calls.some(call => call[0] === '/accounts/check')).toBe(false)
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
})

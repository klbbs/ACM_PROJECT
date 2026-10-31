import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { PlatformConnections } from './platform-connections'
vi.mock('./api', () => ({
  api: vi.fn(),
  date: () => '刚刚',
  names: { codeforces: 'Codeforces', atcoder: 'AtCoder', nowcoder: '牛客', luogu: '洛谷' },
}))
import { api } from './api'

it('Nowcoder QR and Cookie import are alternatives, and a QR session needs no extra import', async () => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  vi.mocked(api)
    .mockReset()
    .mockImplementation(async (path: string) => {
      if (path === '/platforms')
        return {
          items: [
            {
              platform: 'nowcoder',
              accounts: [{ handle: '123', has_cookie: true, login_method: 'qr' }],
            },
          ],
        } as never
      return { items: [] } as never
    })
  try {
    await act(async () =>
      root.render(<PlatformConnections revision={1} run={vi.fn()} onError={vi.fn()} />),
    )
    expect(host.querySelector('.nowcoder-qr-login')).not.toBeNull()
    expect(host.textContent).toContain('无需额外填写 Cookie')
    const methods = host.querySelector('[aria-label="牛客登录方式"]')!
    const cookie = [...methods.querySelectorAll('button')].find(
      (b) => b.textContent === '导入浏览器 Cookie',
    )!
    await act(async () => cookie.click())
    expect(cookie.getAttribute('aria-pressed')).toBe('true')
    expect(host.querySelector('.nowcoder-qr-login')).toBeNull()
    expect(host.textContent).toContain('选择此方式无需扫码')
    expect(api).not.toHaveBeenCalledWith(
      '/platform-login/nowcoder/start',
      expect.anything(),
      expect.anything(),
    )
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
})

it('offers Cookie-helper guidance for Codeforces and AtCoder, Luogu login, and Nowcoder QR', async () => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  vi.mocked(api)
    .mockReset()
    .mockImplementation(async (path: string) => {
      if (path === '/platforms')
        return {
          items: ['codeforces', 'atcoder', 'nowcoder', 'luogu'].map((platform) => ({
            platform,
            accounts: [],
          })),
        } as never
      return { items: [] } as never
    })
  try {
    await act(async () =>
      root.render(
        <PlatformConnections revision={1} run={vi.fn()} onError={vi.fn()} onBound={vi.fn()} />,
      ),
    )
    const cards = [...host.querySelectorAll('.platform-action-grid > article')]
    expect(
      cards.map((card) => [
        card.querySelector('h3')?.textContent,
        !!card.querySelector('.browser-platform-login'),
      ]),
    ).toEqual([
      ['Codeforces', false],
      ['AtCoder', false],
      ['牛客', false],
      ['洛谷', true],
    ])
    for (const card of cards.slice(0, 2)) {
      expect(card.textContent).toContain('正常登录并完成验证')
      expect(card.querySelector('a[download]')?.getAttribute('href')).toBe(
        '/api/browser-userscript/acm-lab-cookie-helper.user.js',
      )
      expect(card.querySelector('a[href*="codeforces.com"], a[href*="atcoder.jp"]')).not.toBeNull()
    }
    expect(cards[2].querySelector('.nowcoder-qr-login')).not.toBeNull()
    expect(cards[3].querySelector('.browser-platform-login')).not.toBeNull()
    expect(host.textContent).toContain('Codeforces、AtCoder 使用油猴助手')
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
})

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
    expect(host.textContent).toContain('真实交题入口在工作台编辑器下方')
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
])(
  'shows Luogu check results while preserving public synchronization and official submission for %s / %s',
  async (status, reason) => {
    const host = document.createElement('div')
    document.body.append(host)
    const root = createRoot(host)
    const run = vi.fn()
    vi.mocked(api)
      .mockReset()
      .mockImplementation(async (path: string) => {
        if (path === '/platforms')
          return {
            items: [{ platform: 'luogu', accounts: [{ handle: '42', has_cookie: true }] }],
          } as never
        if (path === '/accounts/checks')
          return {
            items: [
              {
                platform: 'luogu',
                handle: '42',
                status,
                reason,
                browser_action_required: true,
                browser_action_url: 'https://untrusted.example/challenge',
                checked_at: Math.floor(Date.now() / 1000),
                upstream_status: 418,
                retry_after_seconds: 10,
                message: '旧的访问限制或登录诊断',
                guidance: ['请完成验证码并重新获取Cookie后重试'],
              },
            ],
          } as never
        throw Error('unexpected request')
      })
    try {
      await act(async () =>
        root.render(<PlatformConnections revision={1} run={run} onError={vi.fn()} />),
      )
      const card = host.querySelector('article')!
      expect(card.textContent).toContain('42 · 公开资料同步')
      expect(card.textContent).toContain('洛谷官方网页交题，再同步记录')
      const publicAccount = card.querySelector('.platform-account-check')!
      expect(card.querySelector('.browser-platform-login')).not.toBeNull()
      expect(publicAccount.textContent).toContain('旧的访问限制或登录诊断')
      expect(publicAccount.textContent).toContain('检测登录状态')
      for (const hidden of ['HTTP 418', '已验证登录', '请完成验证码并重新获取Cookie后重试']) {
        expect(publicAccount.textContent).not.toContain(hidden)
      }
      expect(card.querySelector('.platform-browser-action')).toBeNull()
      expect(card.innerHTML).not.toContain('untrusted.example')
      expect(card.querySelector<HTMLAnchorElement>('a')?.href).toBe('https://www.luogu.com.cn/')
      const synchronize = [...card.querySelectorAll('button')].find(
        (button) => button.textContent === '同步此平台信息',
      )!
      await act(async () => synchronize.click())
      expect(run).toHaveBeenCalledWith('/platforms/luogu/collect')
      expect(vi.mocked(api).mock.calls.some((call) => call[0] === '/accounts/check')).toBe(false)
    } finally {
      await act(async () => root.unmount())
      host.remove()
    }
  },
)

it('polls saved background connections and shows the verified identity claim', async () => {
  vi.useFakeTimers()
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  let reads = 0
  vi.mocked(api)
    .mockReset()
    .mockImplementation(async (path: string) => {
      if (path === '/platforms')
        return {
          items: [{ platform: 'codeforces', accounts: [{ handle: 'Owner', has_cookie: true }] }],
        } as never
      if (path === '/accounts/checks') {
        reads++
        return reads === 1
          ? ({
              items: [],
              connections: [{ platform: 'codeforces', handle: 'Owner', status: 'checking' }],
            } as never)
          : ({
              items: [
                {
                  platform: 'codeforces',
                  handle: 'Owner',
                  status: 'authenticated',
                  login_verified: true,
                  message: '登录身份已核实',
                  checked_at: Date.now() / 1000,
                  identity_claim: {
                    status: 'claimed',
                    message: '平台身份已自动关联，可用于榜单与对战；公开设置保持不变',
                  },
                },
              ],
              connections: [{ platform: 'codeforces', handle: 'Owner', status: 'checked' }],
            } as never)
      }
      throw Error('unexpected request')
    })
  try {
    await act(async () =>
      root.render(<PlatformConnections revision={1} run={vi.fn()} onError={vi.fn()} />),
    )
    const button = [...host.querySelectorAll('button')].find((b) => b.textContent === '正在检测…')!
    expect(button.disabled).toBe(true)
    expect(host.textContent).toContain('正在自动检测平台登录并关联身份')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500)
    })
    expect(host.textContent).toContain('已验证登录')
    expect(host.textContent).toContain('平台身份已自动关联')
    expect(host.textContent).not.toContain('正在自动检测')
    expect(reads).toBe(2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })
    expect(reads).toBe(2)
  } finally {
    await act(async () => root.unmount())
    vi.useRealTimers()
    host.remove()
  }
})

it('unlocks manual checking after progress polling fails and ignores old progress after editing', async () => {
  vi.useFakeTimers()
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  const onError = vi.fn()
  let failPoll = false
  vi.mocked(api)
    .mockReset()
    .mockImplementation(async (path: string) => {
      if (path === '/platforms')
        return {
          items: [{ platform: 'atcoder', accounts: [{ handle: 'demo', has_cookie: true }] }],
        } as never
      if (path === '/accounts/checks') {
        if (failPoll) throw Error('progress unavailable')
        return {
          items: [],
          connections: [{ platform: 'atcoder', handle: 'demo', status: 'queued' }],
        } as never
      }
      throw Error('unexpected request')
    })
  try {
    await act(async () =>
      root.render(<PlatformConnections revision={1} run={vi.fn()} onError={onError} />),
    )
    failPoll = true
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500)
    })
    const retry = [...host.querySelectorAll('button')].find(
      (b) => b.textContent === '检测登录状态',
    )!
    expect(retry.disabled).toBe(false)
    expect(onError).toHaveBeenCalledWith('progress unavailable')
    expect(host.textContent).toContain('可手动重新检测')
    // Re-rendering accounts cancels the prior revision's queued polling timer.
    failPoll = false
    await act(async () =>
      root.render(<PlatformConnections revision={2} run={vi.fn()} onError={onError} />),
    )
    await act(async () => root.unmount())
    const reads = vi.mocked(api).mock.calls.length
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(vi.mocked(api).mock.calls.length).toBe(reads)
  } finally {
    vi.useRealTimers()
    host.remove()
  }
})

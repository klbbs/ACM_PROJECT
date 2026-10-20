import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { SocialPage } from './social'
vi.mock('./api', () => ({ api: vi.fn(), date: () => '今天', names: { codeforces: 'Codeforces' } }))
import { api } from './api'

const own = {
  id: 'self',
  username: 'owner',
  display_name: '练习者',
  bio: '',
  public_profile: false,
  leaderboard: false,
}
function defaults(path: string) {
  if (path === '/auth/status') return { multi_user: true }
  if (path === '/social/me') return { profile: own, accounts: [], bindings: [] }
  if (path === '/social/friends') return { items: [] }
  if (path === '/social/duels') return { items: [] }
  if (path.startsWith('/social/leaderboard'))
    return {
      items: [{ id: 'other', display_name: '伙伴', count: 2, rank: 1 }],
      rule: '官方 AC 去重',
    }
  throw Error(`Unexpected request ${path}`)
}
function button(host: HTMLElement, text: string) {
  return [...host.querySelectorAll('button')].find((b) => b.textContent === text)!
}

it('shows opt-in onboarding and opens a public solved problem in the workbench without exposing markup', async () => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  const onOpenProblem = vi.fn()
  HTMLElement.prototype.scrollIntoView = vi.fn()
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path.startsWith('/social/users/'))
      return {
        profile: {
          id: 'other',
          username: 'partner',
          display_name: '伙伴',
          bio: '<script>unsafe</script>',
        },
        relationship: 'none',
        day: '2026-10-20',
        items: [
          {
            id: 'codeforces:1000-A',
            platform: 'codeforces',
            problem_id: '1000-A',
            title: '<img src=x onerror=bad()>',
            difficulty: 1200,
            tags: ['dp'],
            url: 'https://codeforces.com/problemset/problem/1000/A',
          },
        ],
      } as never
    return defaults(path) as never
  })
  try {
    await act(async () => root.render(<SocialPage onOpenProblem={onOpenProblem} />))
    expect(host.textContent).toContain('你的榜单目前仅自己掌握')
    expect(host.textContent).toContain('2 题')
    await act(async () => button(host, '伙伴').click())
    expect(host.textContent).toContain('1200')
    expect(host.textContent).toContain('dp')
    expect(host.querySelector('script')).toBeNull()
    expect(host.querySelector('img')).toBeNull()
    await act(async () => button(host, '去工作台查看').click())
    expect(onOpenProblem).toHaveBeenCalledWith('codeforces:1000-A')
    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalled()
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
})

it('recipient explicitly accepts agreed difficulty and duration; opening a duel uses canonical problem ID', async () => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  const onOpenProblem = vi.fn()
  let active = false
  const duel = {
    id: 'duel',
    sender: '伙伴',
    target: '我',
    sender_id: 'other',
    target_id: 'self',
    self_is_sender: false,
    status: 'pending',
    difficulty: 1200,
    duration_minutes: 60,
  }
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === '/social/duels/duel/action') {
      active = true
      return {} as never
    }
    if (path === '/social/duels')
      return {
        items: [
          {
            ...duel,
            status: active ? 'active' : 'pending',
            ...(active
              ? {
                  problem: {
                    id: 'codeforces:1000-A',
                    title: '官方题',
                    difficulty: 1200,
                    tags: ['dp'],
                    url: 'https://codeforces.com/problemset/problem/1000/A',
                  },
                }
              : {}),
          },
        ],
      } as never
    return defaults(path) as never
  })
  try {
    await act(async () => root.render(<SocialPage onOpenProblem={onOpenProblem} />))
    await act(async () => button(host, '好友对战').click())
    expect(host.textContent).toContain('CF 1200 · 60 分钟')
    expect(host.textContent).toContain('不抵扣每日任务')
    await act(async () => button(host, '接受并随机抽题').click())
    expect(api).toHaveBeenCalledWith('/social/duels/duel/action', 'POST', { action: 'accept' })
    await act(async () => button(host, '在工作台打开对战题').click())
    expect(onOpenProblem).toHaveBeenCalledWith('codeforces:1000-A')
    expect(host.textContent).toContain('后台不会自动代交')
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
})

it('single-user mode does not request another user data', async () => {
  const host = document.createElement('div')
  const root = createRoot(host)
  vi.mocked(api).mockClear()
  vi.mocked(api).mockResolvedValue({ multi_user: false } as never)
  try {
    await act(async () => root.render(<SocialPage onOpenProblem={vi.fn()} />))
    expect(host.textContent).toContain('需要开启多人用户模式')
    expect(api).toHaveBeenCalledTimes(1)
  } finally {
    await act(async () => root.unmount())
  }
})

import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { PlatformPasswordLogin } from './platform-password-login'
import { PlatformConnections } from './platform-connections'
vi.mock('./api', () => ({
  api: vi.fn(),
  date: () => '刚刚',
  names: { codeforces: 'Codeforces', atcoder: 'AtCoder', nowcoder: '牛客', luogu: '洛谷' },
}))
import { api } from './api'

let host: HTMLDivElement
let root: Root
async function render(platform: 'codeforces' | 'atcoder' = 'codeforces', onBound = vi.fn()) {
  await act(async () =>
    root.render(<PlatformPasswordLogin platform={platform} onBound={onBound} />),
  )
  await act(async () => host.querySelector('summary')!.click())
  return onBound
}
async function fill(name: string, value: string) {
  await act(async () => {
    const input = host.querySelector<HTMLInputElement>(`input[name="${name}"]`)!
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
async function submit() {
  await act(async () => {
    host
      .querySelector('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  })
}
beforeEach(() => {
  vi.mocked(api).mockReset()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

it('supports a Codeforces email and optional handle, clears the password, and refreshes only verified bindings', async () => {
  const onBound = await render()
  expect(host.querySelector('details')?.open).toBe(true)
  expect(host.textContent).toContain('handle 或邮箱')
  expect(host.querySelector('input[name="username"]')?.getAttribute('autocomplete')).toBe(
    'username',
  )
  expect(host.querySelector('input[name="password"]')?.getAttribute('autocomplete')).toBe(
    'current-password',
  )
  let sent: unknown
  vi.mocked(api).mockImplementationOnce(async (_path, _method, data) => {
    sent = structuredClone(data)
    return { status: 'authenticated', account: { handle: 'demo' } } as never
  })
  await fill('username', ' demo@example.com ')
  await fill('password', ' password with spaces ')
  await fill('expected_handle', ' demo ')
  await submit()
  expect(vi.mocked(api).mock.calls[0].slice(0, 2)).toEqual(['/platform-login/password', 'POST'])
  expect(sent).toEqual({
    platform: 'codeforces',
    username: 'demo@example.com',
    password: ' password with spaces ',
    expected_handle: 'demo',
  })
  expect(host.querySelector<HTMLInputElement>('input[name="password"]')!.value).toBe('')
  expect(host.textContent).toContain('官方身份验证通过，已绑定账号：demo')
  expect(onBound).toHaveBeenCalledOnce()
  expect(host.textContent).toContain('交题方式仍为官方网页交题')
})

it.each(['blocked', 'rejected', 'unknown'])(
  'clears the password and never binds a %s result',
  async (status) => {
    const onBound = await render()
    vi.mocked(api).mockResolvedValueOnce({
      status,
      message: '平台返回的安全说明',
      official_url: 'https://untrusted.example/login',
    })
    await fill('username', 'demo')
    await fill('password', 'secret')
    await submit()
    expect(host.querySelector<HTMLInputElement>('input[name="password"]')!.value).toBe('')
    expect(onBound).not.toHaveBeenCalled()
    expect(host.textContent).toContain('平台返回的安全说明')
    expect(host.textContent).toContain('官方浏览器中的验证不会自动传回本站')
    expect(host.innerHTML).not.toContain('untrusted.example')
    expect(host.querySelector('a')!.href).toBe('https://codeforces.com/enter')
    if (status === 'blocked') expect(host.textContent).toContain('当前服务器尚未验证登录')
  },
)

it('uses the fixed AtCoder entry and omits an empty expected handle', async () => {
  const onBound = await render('atcoder')
  let sent: unknown
  vi.mocked(api).mockImplementationOnce(async (_path, _method, data) => {
    sent = structuredClone(data)
    return { status: 'rejected' } as never
  })
  await fill('username', 'demo')
  await fill('password', 'secret')
  await submit()
  expect(sent).toEqual({ platform: 'atcoder', username: 'demo', password: 'secret' })
  expect(host.querySelector('a')!.href).toBe('https://atcoder.jp/login')
  expect(onBound).not.toHaveBeenCalled()
})

it('rejects blank credentials and disables repeat submits while a request is pending', async () => {
  await render()
  expect(host.querySelector('button')!.disabled).toBe(true)
  await fill('username', '   ')
  await fill('password', '   ')
  await submit()
  expect(api).not.toHaveBeenCalled()
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('不能仅包含空白')
  let finish!: (value: unknown) => void
  let sent: unknown
  vi.mocked(api).mockImplementationOnce((_path, _method, data) => {
    sent = structuredClone(data)
    return new Promise((resolve) => {
      finish = resolve
    })
  })
  await fill('username', 'demo')
  await fill('password', 'secret')
  await submit()
  expect(sent).toEqual({ platform: 'codeforces', username: 'demo', password: 'secret' })
  expect(host.querySelector('button')!.disabled).toBe(true)
  expect(host.querySelector('button')!.textContent).toContain('正在登录并验证身份')
  expect(host.querySelector<HTMLInputElement>('input[name="password"]')!.value).toBe('')
  expect([...host.querySelectorAll('input')].every((input) => input.disabled)).toBe(true)
  await submit()
  expect(api).toHaveBeenCalledOnce()
  await act(async () => finish({ status: 'unknown' }))
  expect(host.querySelector<HTMLInputElement>('input[name="username"]')!.disabled).toBe(false)
})

it('clears the password after transport failure without displaying raw exceptions', async () => {
  const onBound = await render()
  vi.mocked(api).mockRejectedValueOnce(new Error('sensitive upstream response'))
  await fill('username', 'demo')
  await fill('password', 'secret')
  await submit()
  expect(host.querySelector<HTMLInputElement>('input[name="password"]')!.value).toBe('')
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('当前服务器尚未验证登录')
  expect(host.textContent).not.toContain('sensitive upstream response')
  expect(onBound).not.toHaveBeenCalled()
})

it('keeps a verified binding visible when refreshing the account list fails', async () => {
  const onBound = vi.fn().mockRejectedValue(new Error('refresh unavailable'))
  await render('atcoder', onBound)
  vi.mocked(api).mockResolvedValueOnce({ status: 'authenticated', account: { handle: 'demo' } })
  await fill('username', 'demo')
  await fill('password', 'secret')
  await submit()
  expect(host.textContent).toContain('官方身份验证通过，已绑定账号：demo')
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('账号列表刷新失败')
  expect(host.querySelector<HTMLInputElement>('input[name="password"]')!.value).toBe('')
})

it('offers password login only on Codeforces and AtCoder cards', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/platforms')
      return {
        items: ['codeforces', 'atcoder', 'nowcoder', 'luogu'].map((platform) => ({
          platform,
          accounts: [],
        })),
      } as never
    return { items: [] } as never
  })
  await act(async () =>
    root.render(
      <PlatformConnections revision={1} run={vi.fn()} onError={vi.fn()} onBound={vi.fn()} />,
    ),
  )
  const cards = [...host.querySelectorAll('.platform-action-grid > article')]
  expect(
    cards.map((card) => [
      card.querySelector('h3')?.textContent,
      !!card.querySelector('.platform-password-login'),
    ]),
  ).toEqual([
    ['Codeforces', true],
    ['AtCoder', true],
    ['牛客', false],
    ['洛谷', false],
  ])
  expect(cards[2].querySelector('.nowcoder-qr-login')).not.toBeNull()
  expect(cards[0].querySelector('details')?.open).toBe(false)
  expect(host.textContent).toContain('洛谷目前提供公开资料同步与官方网页交题')
})

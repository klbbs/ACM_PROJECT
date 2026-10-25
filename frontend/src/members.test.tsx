import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
vi.mock('./api', () => ({ api: vi.fn(), date: () => '' }))
import { api } from './api'
import { UserLogin } from './members'

it('does not disclose the administrator identity on public login or registration', async () => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  try {
    await act(async () => root.render(<UserLogin status={{ setup_required: false }} />))
    expect(host.textContent).not.toContain('kendas')
    expect(host.querySelector<HTMLInputElement>('input[autocomplete="username"]')?.value).toBe('')
    const register = Array.from(host.querySelectorAll('button')).find(el => el.textContent?.includes('邀请码'))
    if (register) await act(async () => register.click())
    expect(host.textContent).not.toContain('kendas')
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
})

it('initial setup has empty editable username and passwords, with an optional display nickname', async () => {
  const host = document.createElement('div')
  const root = createRoot(host)
  try {
    await act(async () => root.render(<UserLogin status={{ setup_required: true }} />))
    const username = host.querySelector<HTMLInputElement>('input[autocomplete="username"]')!
    expect(username.value).toBe('')
    expect(username.readOnly).toBe(false)
    expect(username.required).toBe(true)
    expect(username.minLength).toBe(3)
    const nickname = host.querySelector<HTMLInputElement>('input[autocomplete="nickname"]')!
    expect(nickname.value).toBe('')
    expect(nickname.required).toBe(false)
    expect(nickname.maxLength).toBe(40)
    expect([...host.querySelectorAll<HTMLInputElement>('input[type="password"]')].every((input) => input.value === '')).toBe(true)
    expect(host.textContent).toContain('没有默认账号或密码')
    expect(host.textContent).not.toContain('kendas')
  } finally {
    await act(async () => root.unmount())
  }
})

it('submits the deployers chosen username and optional nickname only during initialization', async () => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  vi.mocked(api).mockClear()
  vi.mocked(api).mockRejectedValue(new Error('Synthetic request stopped before navigation'))
  const change = async (input: HTMLInputElement, value: string) => {
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
  }
  try {
    await act(async () => root.render(<UserLogin status={{ setup_required: true }} />))
    await change(host.querySelector('input[autocomplete="username"]')!, 'chosen_owner')
    await change(host.querySelector('input[autocomplete="nickname"]')!, '  训练管理员  ')
    const secretInputs = host.querySelectorAll<HTMLInputElement>('input[type="password"]')
    await change(secretInputs[0], 'Synthetic-ui-only-password')
    await change(secretInputs[1], 'Synthetic-ui-only-password')
    await change(secretInputs[2], 'synthetic-setup-token')
    await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
    expect(api).toHaveBeenCalledWith('/auth/setup', 'POST', {
      username: 'chosen_owner', display_name: '训练管理员',
      password: 'Synthetic-ui-only-password', setup_token: 'synthetic-setup-token',
    })
    expect(host.textContent).toContain('Synthetic request stopped before navigation')
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
})

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it } from 'vitest'
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

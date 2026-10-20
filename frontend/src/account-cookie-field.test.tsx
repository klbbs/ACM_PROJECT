import { afterEach, expect, it, vi } from 'vitest'
import { createRoot } from 'react-dom/client'
import { act } from 'react'
import { AccountCookieField } from './account-cookie-field'

afterEach(() => {
  document.body.innerHTML = ''
})
function mount(props: any) {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  act(() => root.render(<AccountCookieField value="" onChange={vi.fn()} {...props} />))
  return { host, root }
}
it('shows saved state and counts while keeping the edit input empty', () => {
  const { host, root } = mount({ saved: true, info: { status: 'valid', pair_count: 4 } })
  expect(host.textContent).toContain('服务器已保存 Cookie（4 项）')
  expect(host.textContent).toContain('留空保留同一账号的凭据')
  expect(host.querySelector('input')!.value).toBe('')
  expect(host.querySelector('input')!.type).toBe('password')
  act(() => root.unmount())
})
it('explains invalid retained credentials and can reveal only newly typed text', () => {
  const { host, root } = mount({
    saved: true,
    value: 'synthetic-secret=value',
    info: { status: 'invalid', message: '第 1 个字段缺少等号' },
  })
  expect(host.textContent).toContain('格式需修正')
  expect(host.textContent).toContain('第 1 个字段缺少等号')
  expect(host.textContent).not.toContain('synthetic-secret')
  act(() => host.querySelector('button')!.click())
  expect(host.querySelector('input')!.type).toBe('text')
  expect(host.querySelector('input')!.value).toBe('synthetic-secret=value')
  act(() => root.unmount())
})
it('resets visibility when leaving the current edit value', () => {
  const { host, root } = mount({ value: 'synthetic=value' })
  act(() => host.querySelector('button')!.click())
  expect(host.querySelector('input')!.type).toBe('text')
  act(() => root.render(<AccountCookieField value="" onChange={vi.fn()} />))
  act(() => root.render(<AccountCookieField value="next=value" onChange={vi.fn()} />))
  expect(host.querySelector('input')!.type).toBe('password')
  act(() => root.unmount())
})

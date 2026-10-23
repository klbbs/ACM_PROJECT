import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { AdministratorApplication, AdministratorApproval } from './administrator-requests'
import { api } from './api'
vi.mock('./api', () => ({ api: vi.fn(), date: (value: any) => String(value) }))
const roots: ReturnType<typeof createRoot>[] = []
afterEach(async () => { for (const root of roots.splice(0)) await act(async () => root.unmount()); document.body.innerHTML = ''; vi.resetAllMocks() })
async function mount(element: React.ReactNode) {
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host); roots.push(root)
  await act(async () => root.render(element)); return host
}
it('shows pending application and prevents a duplicate request', async () => {
  vi.mocked(api).mockResolvedValue({ items: [{ id: 'r1', status: 'pending', reason: 'test', created_at: 1 }] })
  const host = await mount(<AdministratorApplication onError={vi.fn()} onNotice={vi.fn()} />)
  expect(host.textContent).toContain('已有申请待审核')
  expect(host.querySelector<HTMLButtonElement>('button')?.disabled).toBe(true)
  expect(host.querySelector<HTMLTextAreaElement>('textarea')?.disabled).toBe(true)
})
it('administrator approval requires an explicit action and sends the decision', async () => {
  vi.mocked(api).mockResolvedValue({ items: [{ id: 'r1', username: 'member', reason: '维护资料', status: 'pending', created_at: 1 }] })
  const notice = vi.fn()
  const host = await mount(<AdministratorApproval onError={vi.fn()} onNotice={notice} />)
  expect(vi.mocked(api).mock.calls.every(call => call[1] !== 'POST')).toBe(true)
  const button = [...host.querySelectorAll('button')].find(item => item.textContent === '同意授予管理员权限')!
  await act(async () => button.click())
  expect(api).toHaveBeenCalledWith('/admin/administrator-requests/r1/review', 'POST', { decision: 'approved', note: '' })
  expect(notice).toHaveBeenCalledWith(expect.stringContaining('需要重新登录'))
})
it('failed approvals display an error without claiming success', async () => {
  vi.mocked(api).mockResolvedValueOnce({ items: [{ id: 'r1', username: 'member', status: 'pending' }] }).mockRejectedValueOnce(new Error('管理员身份已变更'))
  const error = vi.fn(), notice = vi.fn()
  const host = await mount(<AdministratorApproval onError={error} onNotice={notice} />)
  await act(async () => [...host.querySelectorAll('button')].find(item => item.textContent === '同意授予管理员权限')!.click())
  expect(error).toHaveBeenCalledWith('管理员身份已变更')
  expect(notice).not.toHaveBeenCalled()
})

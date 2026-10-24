import React, { act, StrictMode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from './api'
import { AIHostsAdmin } from './ai-hosts-admin'

vi.mock('./api', () => ({ api: vi.fn() }))
let host: HTMLDivElement, root: Root
const notices = { onError: vi.fn(), onNotice: vi.fn() }
const render = async () => act(async () => root.render(<AIHostsAdmin {...notices} />))
beforeEach(() => {
  vi.mocked(api).mockReset()
  vi.mocked(api).mockResolvedValue({ default_hosts: ['api.openai.com'], custom_hosts: ['gateway.example.com'] })
  notices.onError.mockReset(); notices.onNotice.mockReset()
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(async () => root.unmount()); host.remove() })

it('loads built-in and custom host lists separately', async () => {
  await render()
  expect(api).toHaveBeenCalledWith('/admin/ai-hosts')
  expect(host.textContent).toContain('api.openai.com')
  expect(host.querySelector('textarea')?.value).toBe('gateway.example.com')
})

it('normalizes the editable list and reports that approval takes effect immediately', async () => {
  await render()
  vi.mocked(api).mockResolvedValueOnce({ default_hosts: ['api.openai.com'], custom_hosts: ['gateway.example.com', 'api.other.net'] })
  await act(async () => {
    const input = host.querySelector('textarea')!
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, ' gateway.example.com\napi.other.net,')
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(api).toHaveBeenLastCalledWith('/admin/ai-hosts', 'PUT', { hosts: ['gateway.example.com', 'api.other.net'] })
  expect(notices.onNotice).toHaveBeenCalledWith('AI 中转站域名白名单已保存并即时生效')
  expect(host.querySelector('textarea')?.value).toBe('gateway.example.com\napi.other.net')
})

it('keeps editing available after a rejected save and surfaces the error', async () => {
  await render()
  vi.mocked(api).mockRejectedValueOnce(new Error('域名格式无效'))
  await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(notices.onError).toHaveBeenCalledWith('域名格式无效')
  expect(host.querySelector('textarea')?.value).toBe('gateway.example.com')
})

it('blocks saving after a failed initial read and reloads the saved configuration on retry', async () => {
  vi.mocked(api).mockRejectedValueOnce(new Error('读取服务暂不可用'))
  await render()
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('读取服务暂不可用')
  expect(host.querySelector('textarea')?.disabled).toBe(true)
  expect(host.querySelector<HTMLButtonElement>('button.primary')?.disabled).toBe(true)
  await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(api).toHaveBeenCalledTimes(1)
  await act(async () => host.querySelector<HTMLButtonElement>('button[type="button"]')!.click())
  expect(api).toHaveBeenCalledTimes(2)
  expect(host.querySelector('[role="alert"]')).toBeNull()
  expect(host.querySelector('textarea')?.value).toBe('gateway.example.com')
  expect(host.querySelector<HTMLButtonElement>('button.primary')?.disabled).toBe(false)
})

it('prevents repeated submissions synchronously before React renders saving state', async () => {
  await render()
  let finish!: (value: unknown) => void
  vi.mocked(api).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  await act(async () => {
    const form = host.querySelector('form')!
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  })
  expect(api).toHaveBeenCalledTimes(2)
  expect(host.querySelector<HTMLButtonElement>('button.primary')?.disabled).toBe(true)
  await act(async () => finish({ default_hosts: [], custom_hosts: ['gateway.example.com'] }))
  expect(notices.onNotice).toHaveBeenCalledTimes(1)
})

it.each(['resolve', 'reject'])('ignores a stale initial read after strict-mode cleanup: %s', async outcome => {
  let finish!: (value: unknown) => void
  let fail!: (error: Error) => void
  vi.mocked(api).mockImplementationOnce(() => new Promise((resolve, reject) => { finish = resolve; fail = reject }))
  await act(async () => root.render(<StrictMode><AIHostsAdmin {...notices} /></StrictMode>))
  expect(api).toHaveBeenCalledTimes(2)
  await act(async () => {
    if (outcome === 'resolve') finish({ default_hosts: [], custom_hosts: ['stale.example.com'] })
    else fail(new Error('stale failure'))
  })
  expect(host.querySelector('textarea')?.value).toBe('gateway.example.com')
  expect(notices.onError).not.toHaveBeenCalled()
  expect(host.querySelector<HTMLButtonElement>('button.primary')?.disabled).toBe(false)
})

it.each(['resolve', 'reject'])('ignores an old save after leaving and reopening the panel: %s', async outcome => {
  await render()
  let finish!: (value: unknown) => void
  let fail!: (error: Error) => void
  vi.mocked(api).mockImplementationOnce(() => new Promise((resolve, reject) => { finish = resolve; fail = reject }))
  await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  await act(async () => root.unmount())
  root = createRoot(host)
  await render()
  await act(async () => {
    if (outcome === 'resolve') finish({ default_hosts: [], custom_hosts: ['stale.example.com'] })
    else fail(new Error('stale failure'))
  })
  expect(host.querySelector('textarea')?.value).toBe('gateway.example.com')
  expect(notices.onError).not.toHaveBeenCalled()
  expect(notices.onNotice).not.toHaveBeenCalled()
})

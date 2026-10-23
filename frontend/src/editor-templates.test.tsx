import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { EditorTemplates } from './editor-templates'
import { api } from './api'
vi.mock('./api', () => ({ api: vi.fn() }))
const roots: ReturnType<typeof createRoot>[] = []
afterEach(async () => {
  for (const root of roots.splice(0)) await act(async () => root.unmount())
  document.body.innerHTML = ''
  vi.resetAllMocks()
})
async function mount() {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  roots.push(root)
  const changed = vi.fn()
  await act(async () => root.render(<EditorTemplates onChanged={changed} />))
  return { host, changed }
}
async function input(host: HTMLElement, label: string, value: string) {
  const node = host.querySelector<HTMLInputElement | HTMLTextAreaElement>(
    `[aria-label="${label}"]`,
  )!
  const prototype =
    node.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  await act(async () => {
    Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(node, value)
    node.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const button = (host: HTMLElement, text: string) =>
  [...host.querySelectorAll('button')].find((node) => node.textContent === text)!
const existing = {
  id: 'template-001',
  title: '快速输入',
  trigger: '快读',
  language: 'cpp' as const,
  body: 'read(${x});',
}

it('loads existing private templates and supports editing and deleting them', async () => {
  vi.mocked(api).mockResolvedValueOnce({ revision: 3, items: [existing] })
  const { host, changed } = await mount()
  expect(changed).toHaveBeenCalledWith([existing])
  await act(async () => button(host, '编辑 快速输入').click())
  await input(host, '模板标题', '更好的快读')
  vi.mocked(api).mockResolvedValueOnce({
    revision: 4,
    items: [{ ...existing, title: '更好的快读' }],
  })
  await act(async () => button(host, '保存模板修改').click())
  expect(api).toHaveBeenCalledWith('/workbench/code-templates', 'PUT', {
    revision: 3,
    items: [{ ...existing, title: '更好的快读' }],
  })
  vi.mocked(api).mockResolvedValueOnce({ revision: 5, items: [] })
  await act(async () => button(host, '删除 更好的快读').click())
  expect(api).toHaveBeenCalledWith('/workbench/code-templates', 'PUT', { revision: 4, items: [] })
  expect(host.textContent).toContain('还没有个人模板')
})

it('preserves all new-template form fields when save fails', async () => {
  vi.mocked(api).mockResolvedValueOnce({ revision: 0, items: [] })
  const { host, changed } = await mount()
  await input(host, '模板标题', '树状数组')
  await input(host, '模板触发词', '树状数组')
  await input(host, '模板代码', 'int ${name};')
  vi.mocked(api).mockRejectedValueOnce(new Error('网络连接中断'))
  await act(async () => button(host, '添加并保存模板').click())
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('网络连接中断')
  expect(host.querySelector<HTMLInputElement>('[aria-label="模板标题"]')?.value).toBe('树状数组')
  expect(host.querySelector<HTMLTextAreaElement>('[aria-label="模板代码"]')?.value).toBe(
    'int ${name};',
  )
  expect(changed).toHaveBeenCalledTimes(1)
})

it('uses latest revision after conflicts while retaining the current edited body', async () => {
  vi.mocked(api).mockResolvedValueOnce({ revision: 1, items: [existing] })
  const { host } = await mount()
  await act(async () => button(host, '编辑 快速输入').click())
  await input(host, '模板代码', 'my_private_read(${x});')
  vi.mocked(api).mockRejectedValueOnce(Object.assign(new Error('模板已更新'), { status: 409 }))
  await act(async () => button(host, '保存模板修改').click())
  expect(host.querySelector<HTMLFieldSetElement>('fieldset')?.disabled).toBe(true)
  vi.mocked(api).mockResolvedValueOnce({ revision: 2, items: [existing] })
  await act(async () => button(host, '读取最新版本（保留当前表单）').click())
  expect(host.querySelector<HTMLTextAreaElement>('[aria-label="模板代码"]')?.value).toBe(
    'my_private_read(${x});',
  )
  vi.mocked(api).mockResolvedValueOnce({
    revision: 3,
    items: [{ ...existing, body: 'my_private_read(${x});' }],
  })
  await act(async () => button(host, '保存模板修改').click())
  expect(api).toHaveBeenLastCalledWith('/workbench/code-templates', 'PUT', {
    revision: 2,
    items: [{ ...existing, body: 'my_private_read(${x});' }],
  })
})

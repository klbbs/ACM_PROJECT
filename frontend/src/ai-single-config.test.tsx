import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  api: apiMock,
}))

const { App } = await import('./main')
Object.defineProperty(window, 'scrollTo', { value: () => {}, writable: true })

const overview = {
  stats: { solved: 0, accepted: 0, submissions: 0, all_submissions: 0, streak: 0, active_days: 0 },
  platforms: [],
  topics: [],
  recent: [],
  trend: [],
  verdicts: {},
  coverage: [],
  actual_time_evidence: [],
}

const savedConfig = () => ({
  base_url: 'https://api.openai.com/v1',
  protocol: 'responses',
  vendor: 'openai',
  model: 'saved-default-model',
  channel: 'api',
  codex_model: 'gpt-5-codex',
  has_key: true,
  codex_enabled: true,
  codex_device_only: false,
  ready: true,
  active_model: 'saved-default-model',
})

let ai: ReturnType<typeof savedConfig>
let host: HTMLDivElement
let root: Root

const routes: Record<string, () => unknown> = {
  '/auth/status': () => ({
    authenticated: true,
    multi_user: false,
    user: { username: 'local', role: 'member' },
  }),
  '/ai/settings': () => ai,
  '/ai/presets': () => ({ items: [] }),
  '/jobs': () => ({ items: [] }),
  '/reports': () => ({ items: [] }),
  '/announcements': () => ({ items: [], unread_ids: [] }),
  '/training': () => ({
    goal_rating: 2000,
    minimum: 1000,
    maximum: 1600,
    min_contest: 0,
    published_after: null,
    preferred_tags: [],
    avoid_tags: [],
    prefer_recent: true,
  }),
  '/accounts': () => ({ items: [] }),
  '/platforms': () => ({ items: [] }),
  '/accounts/checks': () => ({ items: [] }),
}
apiMock.mockImplementation(async (path: string) => {
  if (path.startsWith('/overview')) return overview
  if (path.startsWith('/reports')) return { items: [] }
  const route = routes[path.split('?')[0]]
  return route ? route() : {}
})

const settle = () => act(async () => {})
const render = async () => {
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  await act(async () => root.render(<App />))
  await settle()
}
const click = async (element: Element | null | undefined) => {
  expect(element).toBeTruthy()
  await act(async () => (element as HTMLElement).click())
  await settle()
}
const navButton = (name: string) =>
  Array.from(host.querySelectorAll('nav button, button')).find(
    (button) => button.getAttribute('aria-label') === name,
  )
const aiStatus = () => host.querySelector('[aria-label="当前 AI 配置"]')
const buttonByText = (text: string) =>
  Array.from(host.querySelectorAll('button')).find((button) =>
    (button.textContent || '').includes(text),
  )
const posted = (path: string) => apiMock.mock.calls.filter(([target]) => target === path).at(-1)

beforeEach(() => {
  apiMock.mockClear()
  ai = savedConfig()
})
afterEach(async () => {
  await act(async () => root?.unmount())
  host?.remove()
})

it('loads the saved default model on page load with no channel or model picker', async () => {
  await render()
  await click(navButton('训练总览'))

  expect(aiStatus()?.textContent).toBe('API · saved-default-model')
  expect(host.querySelector('select[aria-label="选择模型"]')).toBeNull()
  expect(host.querySelector('select[aria-label="AI 通道"]')).toBeNull()
  expect(host.querySelector('[aria-label="读取可用模型"]')).toBeNull()
  expect((buttonByText('开始分析') as HTMLButtonElement).disabled).toBe(false)

  await click(buttonByText('开始分析'))
  expect(posted('/analysis')?.[2]).toMatchObject({
    provider: 'api',
    model: 'saved-default-model',
  })
})

it('follows the saved configuration when settings change or the key is cleared', async () => {
  await render()
  await click(navButton('训练总览'))

  ai = { ...savedConfig(), model: 'switched-model', active_model: 'switched-model' }
  await act(async () => window.dispatchEvent(new CustomEvent('acm:ai-config-changed')))
  await settle()
  expect(aiStatus()?.textContent).toBe('API · switched-model')

  ai = { ...ai, has_key: false, ready: false }
  await act(async () => window.dispatchEvent(new CustomEvent('acm:ai-config-changed')))
  await settle()
  expect(aiStatus()?.textContent).toBe('API · switched-model（缺少 Key）')
  expect((buttonByText('开始分析') as HTMLButtonElement).disabled).toBe(true)
})

it('uses the official Codex default chosen once in settings without a feature picker', async () => {
  ai = { ...savedConfig(), channel: 'codex', active_model: 'gpt-5-codex' }
  await render()
  await click(navButton('训练总览'))

  expect(aiStatus()?.textContent).toBe('官方 Codex · gpt-5-codex')
  expect(host.querySelector('select[aria-label="选择模型"]')).toBeNull()

  await click(buttonByText('开始分析'))
  expect(posted('/analysis')?.[2]).toMatchObject({ provider: 'codex', model: 'gpt-5-codex' })
})

it('keeps the channel and model dropdowns inside connections and settings only', async () => {
  await render()
  await click(navButton('连接与设置'))

  expect(host.querySelector('select[aria-label="默认模型 ID"]')).toBeTruthy()
  expect(host.querySelector('select[aria-label="官方默认模型"]')).toBeTruthy()
  expect(host.querySelector('input[aria-label="使用 API Key 通道"]')).toBeTruthy()
  expect(host.querySelector('input[aria-label="使用官方 Codex 通道"]')).toBeTruthy()
  expect(host.querySelector('[aria-label="读取可用模型"]')).toBeNull()
})

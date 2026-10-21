import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { TranslationPanel } from './translation-panel'
vi.mock('./api', () => ({ api: vi.fn(), date: () => 'fixture-date' }))
vi.mock('./statement-view', () => ({
  StatementView: ({ statement }: any) => <p>{statement.sections?.[0]}</p>,
}))
import { api } from './api'

let host: HTMLDivElement, root: Root
const statement = { title: 'Fixture statement', sections: ['Original statement'], samples: [] }
const props = {
  problemId: 'codeforces:4-A',
  statement,
  provider: 'api',
  model: 'fixture-model',
  revision: 0,
  jobs: [],
  run: vi.fn(),
  onError: vi.fn(),
  children: <p>Original content</p>,
}
const data = () => ({
  items: [],
  services: { google: { configured: true }, bing: { configured: true } },
  shareable: true,
  can_review: false,
})
async function render(overrides: Record<string, unknown> = {}) {
  await act(async () => root.render(<TranslationPanel {...props} {...overrides} />))
}
function button(text: string) {
  return [...host.querySelectorAll('button')].find((item) => item.textContent?.includes(text))!
}
async function click(text: string) {
  await act(async () => button(text).click())
}
async function selectEngine(engine: string) {
  await act(async () => {
    const select = host.querySelector<HTMLSelectElement>('.translation-form select')!
    select.value = engine
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
}
beforeEach(() => {
  vi.mocked(api).mockReset()
  props.run.mockReset()
  props.onError.mockReset()
  vi.mocked(api).mockResolvedValue(data())
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

it('explains the AI transfer beside an immediately usable button and only generates after a click', async () => {
  await render()
  expect(host.querySelector('input[type="checkbox"]')).toBeNull()
  expect(host.textContent).toContain('将本题题面发送给所选 AI，可能分段调用并计费')
  expect(button('翻译题面（AI）').disabled).toBe(false)
  await render({ revision: 1 })
  expect(props.run).not.toHaveBeenCalled()
  expect(vi.mocked(api).mock.calls.every(([, method]) => method === undefined)).toBe(true)
  await click('翻译题面（AI）')
  expect(props.run).toHaveBeenCalledOnce()
  expect(props.run).toHaveBeenCalledWith('/workbench/problem/codeforces%3A4-A/translate', {
    engine: 'ai',
    provider: 'api',
    model: 'fixture-model',
    consent: true,
  })
})

it.each(['google', 'bing'])(
  'switching to configured %s does not call it until explicit translation, and needs no AI model',
  async (engine) => {
    await render({ model: '' })
    expect(button('翻译题面（AI）').disabled).toBe(true)
    await selectEngine(engine)
    expect(props.run).not.toHaveBeenCalled()
    expect(host.textContent).toContain('官方服务，可能按服务规则计费；不会调用 AI')
    const generate = [...host.querySelectorAll('button')].find((item) =>
      item.textContent?.startsWith('翻译题面'),
    )!
    expect(generate.disabled).toBe(false)
    await act(async () => generate.click())
    expect(props.run).toHaveBeenCalledWith('/workbench/problem/codeforces%3A4-A/translate', {
      engine,
      provider: 'api',
      model: '',
      consent: true,
    })
  },
)

it('keeps unconfigured services and missing AI models from generating, with accurate guidance', async () => {
  vi.mocked(api).mockResolvedValue({
    ...data(),
    services: { google: { configured: false } },
    links: { google: 'https://translate.google.com/' },
  })
  await render({ model: '' })
  expect(button('翻译题面（AI）').disabled).toBe(true)
  expect(host.textContent).toContain('尚未选择模型')
  await selectEngine('google')
  expect(host.textContent).toContain('服务器尚未配置此翻译服务')
  expect(
    [...host.querySelectorAll('button')].some((item) => item.textContent?.startsWith('翻译题面')),
  ).toBe(false)
  expect(host.querySelector('a')?.href).toBe('https://translate.google.com/')
  expect(props.run).not.toHaveBeenCalled()
})

it('disables another generation while a translation job is active', async () => {
  await render({ jobs: [{ kind: 'translation', status: 'running' }] })
  expect(button('翻译题面（AI）').disabled).toBe(true)
  await click('翻译题面（AI）')
  expect(props.run).not.toHaveBeenCalled()
})

it('reading a private result never uploads it, and sharing still requires the upload action', async () => {
  const item = {
    id: 'private-translation',
    engine: 'ai',
    model: 'fixture-model',
    scope: 'private',
    status: 'private',
    shareable: true,
    statement: { title: 'Translated title', sections: ['Translated text'], samples: [] },
  }
  let uploaded = false
  vi.mocked(api).mockImplementation(async (path, method) => {
    if (method === 'POST') {
      expect(path).toBe('/translations/private-translation/upload')
      uploaded = true
      return {} as never
    }
    return { ...data(), items: [{ ...item, status: uploaded ? 'pending' : 'private' }] } as never
  })
  await render()
  await act(async () => {
    const select = host.querySelector<HTMLSelectElement>('[aria-label="阅读原题或译文"]')!
    select.value = item.id
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
  expect(uploaded).toBe(false)
  expect(props.run).not.toHaveBeenCalled()
  await click('上传翻译供审核')
  expect(uploaded).toBe(true)
  expect(host.textContent).toContain('等待管理员审核；通过前不会公开给其他用户')
  expect(
    [...host.querySelectorAll('button')].some((item) =>
      item.textContent?.includes('上传翻译供审核'),
    ),
  ).toBe(false)
})

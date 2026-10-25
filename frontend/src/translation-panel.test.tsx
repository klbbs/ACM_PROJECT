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
  vi.useRealTimers()
  vi.restoreAllMocks()
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
  expect(host.textContent).toContain('尚未配置默认模型')
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

it('shows the failed batch beside translation controls and clears it for another problem', async () => {
  props.run.mockResolvedValue({ id: 'my-translation', status: 'queued' })
  await render()
  await click('翻译题面（AI）')
  await render({ jobs: [{ id: 'my-translation', kind: 'translation', status: 'failed',
    error: '题面翻译第 2/4 批失败：AI 响应被截断' }] })
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('第 2/4 批失败')
  expect(host.textContent).toContain('原题和已有译文仍可阅读')
  await render({ problemId: 'codeforces:5-A' })
  expect(host.querySelector('[role="alert"]')).toBeNull()
})

it('handles queue rejection returned by the shared run helper', async () => {
  props.run.mockResolvedValue({ error: '当前用户任务已满，请等待完成' })
  await render()
  await click('翻译题面（AI）')
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('任务已满')
  expect(props.onError).toHaveBeenCalledWith('当前用户任务已满，请等待完成')
})

it('selects the requested shared result rather than an older completed job', async () => {
  const item = (id: string) => ({ id, engine: 'ai', model: 'fixture-model', scope: 'shared',
    status: 'approved', statement: { title: id, sections: [id], samples: [] } })
  vi.mocked(api).mockResolvedValue({ ...data(), items: [item('old-result'), item('reused-result')] })
  props.run.mockResolvedValue({ id: 'current-job' })
  await render()
  await click('翻译题面（AI）')
  await render({ jobs: [
    { id: 'current-job', kind: 'translation', status: 'completed', result: { translation_id: 'reused-result' } },
    { id: 'old-job', kind: 'translation', status: 'completed', result: { translation_id: 'old-result' } },
  ] })
  expect(host.querySelector<HTMLSelectElement>('[aria-label="阅读原题或译文"]')?.value).toBe('reused-result')
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

const sharedItem = (id: string) => ({
  id, engine: 'ai', model: 'fixture-model', scope: 'shared', status: 'approved',
  statement: { title: id, sections: [`Translated ${id}`], samples: [] },
})
async function selectReading(id: string) {
  await act(async () => {
    const select = host.querySelector<HTMLSelectElement>('[aria-label="阅读原题或译文"]')!
    select.value = id
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
}
function reading() {
  return host.querySelector<HTMLSelectElement>('[aria-label="阅读原题或译文"]')!
}
function deferred() {
  let resolve!: (result: any) => void
  const promise = new Promise<any>((done) => { resolve = done })
  return { promise, resolve }
}

it('refreshes approved shared translations on demand without changing the reader or calling AI', async () => {
  vi.mocked(api).mockResolvedValueOnce({ ...data(), items: [sharedItem('first')] })
  await render()
  await selectReading('first')
  vi.mocked(api).mockResolvedValue({ ...data(), items: [sharedItem('first'), sharedItem('approved-later')] })
  await click('刷新共享译文')
  expect([...reading().options].some((option) => option.value === 'approved-later')).toBe(true)
  expect(reading().value).toBe('first')
  await selectReading('')
  await click('刷新共享译文')
  expect(reading().value).toBe('')
  expect(host.textContent).toContain('Original content')
  expect(props.run).not.toHaveBeenCalled()
  expect(vi.mocked(api).mock.calls.every(([, method]) => method === undefined)).toBe(true)
})

it('preserves the selected translation when the workbench recreates an unchanged statement object', async () => {
  vi.mocked(api).mockResolvedValue({ ...data(), items: [sharedItem('selected')] })
  await render()
  await selectReading('selected')
  await render({ statement: JSON.parse(JSON.stringify(statement)), revision: 1 })
  expect(reading().value).toBe('selected')
})

it('preserves a requested generation across equivalent statement snapshots', async () => {
  props.run.mockResolvedValue({ id: 'requested-job' })
  await render()
  await click('翻译题面（AI）')
  vi.mocked(api).mockResolvedValue({ ...data(), items: [sharedItem('exact-result')] })
  await render({ statement: JSON.parse(JSON.stringify(statement)), revision: 1, jobs: [
    { id: 'requested-job', kind: 'translation', status: 'completed', result: { translation_id: 'exact-result' } },
  ] })
  expect(reading().value).toBe('exact-result')
  expect(host.textContent).toContain('翻译已完成')
})

it('keeps a readable translation after refresh failure and permits an explicit retry', async () => {
  vi.mocked(api).mockResolvedValue({ ...data(), items: [sharedItem('saved')] })
  await render()
  await selectReading('saved')
  vi.mocked(api).mockRejectedValueOnce(new Error('网络连接中断'))
  await click('刷新共享译文')
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('网络连接中断')
  expect(host.textContent).toContain('点击“刷新共享译文”重试')
  expect(reading().value).toBe('saved')
  expect(host.textContent).toContain('Translated saved')
  expect(button('刷新共享译文').disabled).toBe(false)
  expect(props.onError).not.toHaveBeenCalled()
  await click('刷新共享译文')
  expect(host.querySelector('[role="alert"]')).toBeNull()
  expect(reading().value).toBe('saved')
})

it('refreshes visible pages every 30 seconds and on focus or becoming visible, using only GET', async () => {
  vi.useFakeTimers()
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
  await render()
  expect(api).toHaveBeenCalledTimes(1)
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
  expect(api).toHaveBeenCalledTimes(2)
  visibility.mockReturnValue('hidden')
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
  expect(api).toHaveBeenCalledTimes(2)
  await act(async () => window.dispatchEvent(new Event('focus')))
  expect(api).toHaveBeenCalledTimes(2)
  visibility.mockReturnValue('visible')
  await act(async () => document.dispatchEvent(new Event('visibilitychange')))
  expect(api).toHaveBeenCalledTimes(3)
  await act(async () => window.dispatchEvent(new Event('focus')))
  expect(api).toHaveBeenCalledTimes(4)
  expect(props.run).not.toHaveBeenCalled()
  expect(vi.mocked(api).mock.calls.every(([, method]) => method === undefined)).toBe(true)
  await act(async () => root.unmount())
  await act(async () => {
    window.dispatchEvent(new Event('focus'))
    await vi.advanceTimersByTimeAsync(60_000)
  })
  expect(api).toHaveBeenCalledTimes(4)
  root = createRoot(host)
})

it.each(['problem', 'statement'])('ignores old asynchronous results after switching %s', async (change) => {
  const old = deferred()
  vi.mocked(api).mockReturnValueOnce(old.promise)
  await render()
  vi.mocked(api).mockResolvedValue({ ...data(), items: [sharedItem('current-version')] })
  await render(change === 'problem'
    ? { problemId: 'atcoder:abc001_a' }
    : { statement: { ...statement, sections: ['Changed source statement'] } })
  await act(async () => old.resolve({ ...data(), items: [sharedItem('stale-version')] }))
  expect([...reading().options].map((option) => option.value)).toEqual(['', 'current-version'])
})

it('keeps the most recent refresh when a slower previous response arrives later', async () => {
  await render()
  const old = deferred()
  vi.mocked(api).mockReturnValueOnce(old.promise)
  await act(async () => window.dispatchEvent(new Event('focus')))
  vi.mocked(api).mockResolvedValueOnce({ ...data(), items: [sharedItem('latest')] })
  await act(async () => window.dispatchEvent(new Event('focus')))
  await act(async () => old.resolve({ ...data(), items: [sharedItem('stale')] }))
  expect([...reading().options].map((option) => option.value)).toEqual(['', 'latest'])
})

it('refreshes the completed job result even without a global revision change, without guessing by model', async () => {
  props.run.mockResolvedValue({ id: 'requested-job' })
  await render()
  await click('翻译题面（AI）')
  vi.mocked(api).mockResolvedValue({ ...data(), items: [sharedItem('unrelated-same-model')] })
  await click('刷新共享译文')
  expect(reading().value).toBe('')
  vi.mocked(api).mockResolvedValue({ ...data(), items: [sharedItem('unrelated-same-model'), sharedItem('exact-result')] })
  await render({ jobs: [
    { id: 'old-job', kind: 'translation', status: 'completed', result: { translation_id: 'unrelated-same-model' } },
    { id: 'requested-job', kind: 'translation', status: 'completed', result: { translation_id: 'exact-result' } },
  ] })
  expect(reading().value).toBe('exact-result')
  expect(host.textContent).toContain('翻译已完成')
})

it('can retry fetching the exact completed result after the completion refresh fails', async () => {
  props.run.mockResolvedValue({ id: 'requested-job' })
  await render()
  await click('翻译题面（AI）')
  vi.mocked(api).mockRejectedValueOnce(new Error('刷新暂时失败'))
  await render({ jobs: [
    { id: 'requested-job', kind: 'translation', status: 'completed', result: { translation_id: 'exact-result' } },
  ] })
  expect(reading().value).toBe('')
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('刷新暂时失败')
  vi.mocked(api).mockResolvedValue({ ...data(), items: [sharedItem('exact-result')] })
  await click('刷新共享译文')
  expect(reading().value).toBe('exact-result')
  expect(props.run).toHaveBeenCalledOnce()
})

it('does not apply an old generation response or job result to another problem', async () => {
  const generating = deferred()
  props.run.mockReturnValueOnce(generating.promise)
  await render()
  await click('翻译题面（AI）')
  await render({ problemId: 'atcoder:abc001_a' })
  await act(async () => generating.resolve({ id: 'old-generation' }))
  await render({ problemId: 'atcoder:abc001_a', jobs: [
    { id: 'old-generation', kind: 'translation', status: 'completed', result: { translation_id: 'wrong-problem' } },
  ] })
  expect(reading().value).toBe('')
  expect(host.textContent).not.toContain('翻译已完成')
  expect(button('翻译题面（AI）').disabled).toBe(false)
})

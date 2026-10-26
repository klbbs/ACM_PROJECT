import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { TrainingCandidates } from './training-candidates'
const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('./api', () => ({ api: apiMock }))
let host: HTMLDivElement, root: Root
const coverage = (topic = 'dp', count = 8) => ({
  topic_id: topic,
  eligible_count: count,
  template_count: 0,
  notice: '这是有范围的本地题库缓存，不代表平台全部题目。',
  platforms: [
    {
      platform: 'codeforces',
      verified: true,
      cached_count: 80,
      topic_count: 20,
      eligible_count: count,
      template_count: 0,
      unknown_topic_count: 4,
      conditions: [{ code: 'date_unknown', count: 3, message: '缺少可靠发布日期' }],
      discovery: { status: 'failed', message: '专题列表暂时不可访问，保留旧缓存' },
    },
    {
      platform: 'atcoder',
      verified: false,
      cached_count: 60,
      topic_count: 10,
      eligible_count: 0,
      template_count: 0,
      unknown_topic_count: 0,
      conditions: [],
    },
  ],
})
const props = { topicId: 'dp', topicTitle: '动态规划' }
const render = async (extra = {}) =>
  act(async () => root.render(<TrainingCandidates {...props} {...extra} />))
const button = (text: string) =>
  Array.from(host.querySelectorAll('button')).find((item) => item.textContent?.includes(text))!
const click = async (text: string) => act(async () => button(text).click())
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}
beforeEach(() => {
  apiMock.mockReset()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.useRealTimers()
})

it('does not fetch, start discovery, or invoke AI on mount or input changes', async () => {
  await render()
  await render({ topicId: 'bfs', minimum: 1000, maximum: 1600 })
  expect(apiMock).not.toHaveBeenCalled()
  expect(host.textContent).toContain('不调用 AI')
  expect(host.textContent).toContain('本人已验证登录平台')
})
it('explains actual cached matching and filter reasons per platform with no false global-empty claim', async () => {
  apiMock.mockResolvedValue(coverage())
  await render({ minimum: 1000, maximum: 1800 })
  await click('查看本专题候选')
  expect(apiMock).toHaveBeenCalledExactlyOnceWith(
    '/training/candidates?topic_id=dp&cf_minimum=1000&cf_maximum=1800',
  )
  expect(host.textContent).toContain('缓存 80 题 · 专题匹配 20 题 · 可选 8 题')
  expect(host.textContent).toContain('4 题缺少可靠专题标签')
  expect(host.textContent).toContain('缺少可靠发布日期（3 题）')
  expect(host.textContent).toContain('未验证，不参与选题')
  expect(host.textContent).toContain('当前缓存尚无符合条件的模板题')
  expect(host.textContent).toContain('不代表平台全部题目')
  expect(host.textContent).toContain('专题列表暂时不可访问')
  expect(host.querySelector('a')?.getAttribute('href')).toBe('#settings')
})
it('guards duplicate read clicks and drops old responses after switching topics', async () => {
  const pending = deferred<ReturnType<typeof coverage>>()
  apiMock.mockReturnValue(pending.promise)
  await render()
  await act(async () => {
    button('查看本专题候选').click()
    button('查看本专题候选').click()
  })
  expect(apiMock).toHaveBeenCalledTimes(1)
  await render({ topicId: 'bfs', topicTitle: '广搜' })
  await act(async () => pending.resolve(coverage('dp', 999)))
  expect(host.textContent).not.toContain('999')
  expect(button('查看本专题候选').disabled).toBe(false)
})
it('shows failed reads and allows an explicit retry without paid AI work', async () => {
  apiMock.mockRejectedValueOnce(new Error('读取超时')).mockResolvedValueOnce(coverage())
  await render()
  await click('查看本专题候选')
  expect(host.querySelector('[role=alert]')?.textContent).toContain('读取超时')
  await click('查看本专题候选')
  expect(host.querySelector('[role=alert]')).toBeNull()
  expect(host.textContent).toContain('可选 8 题')
  expect(apiMock.mock.calls.every((call) => !call[1])).toBe(true)
})
it('explains public discovery states in Chinese including empty responses and unrequested platforms', async () => {
  const result = coverage()
  result.platforms[0].discovery = { status: 'empty', message: '本次公开列表未返回符合条件的题目' }
  result.platforms[1] = {
    ...result.platforms[1],
    discovery: { status: 'not_requested', message: '' },
  }
  apiMock.mockResolvedValue(result)
  await render()
  await click('查看本专题候选')
  expect(host.textContent).toContain('专题补抓：本次无结果')
  expect(host.textContent).toContain('尚未查询')
  expect(host.textContent).not.toContain('not_requested')
})
it('runs public discovery only on click, polls the exact job, then reloads local evidence without selecting problems', async () => {
  vi.useFakeTimers()
  apiMock.mockImplementation((path, method) => {
    if (method === 'POST') return Promise.resolve({ id: 'selected', status: 'queued' })
    if (path === '/jobs')
      return Promise.resolve({
        items: [
          { id: 'other-user-looking-job', status: 'completed' },
          {
            id: 'selected',
            status: 'running',
            result: { completed_platforms: 1, total_platforms: 2 },
          },
        ],
      })
    return Promise.resolve(coverage())
  })
  await render()
  await act(async () => {
    button('补抓本专题题库').click()
    button('补抓本专题题库').click()
  })
  expect(apiMock.mock.calls.filter((call) => call[1] === 'POST')).toEqual([
    ['/training/catalog/refresh', 'POST', { topic_id: 'dp' }],
  ])
  expect(host.textContent).toContain('1/2 个平台已处理')
  expect(host.textContent).not.toContain('重新读取本专题候选')
  apiMock.mockImplementation((path) =>
    Promise.resolve(
      path === '/jobs'
        ? {
            items: [
              {
                id: 'selected',
                status: 'partial',
                result: {
                  platforms: [
                    { platform: 'luogu', status: 'updated', count: 12 },
                    { platform: 'nowcoder', status: 'failed', message: '列表暂时不可访问' },
                  ],
                },
              },
            ],
          }
        : coverage(),
    ),
  )
  await act(async () => vi.advanceTimersByTimeAsync(1800))
  expect(host.textContent).toContain('部分成功')
  expect(host.textContent).toContain('读取 12 题')
  expect(host.textContent).toContain('列表暂时不可访问')
  expect(host.textContent).toContain('已重新读取本专题候选')
  expect(host.textContent).toContain('当前缓存可选 8 题')
  expect(button('补抓本专题题库').disabled).toBe(false)
})
it('stops polling on account revision and never displays an earlier account response', async () => {
  vi.useFakeTimers()
  const pending = deferred<{ items: { id: string; status: string }[] }>()
  apiMock.mockImplementation((path, method) =>
    method === 'POST'
      ? Promise.resolve({ id: 'old', status: 'queued' })
      : path === '/jobs'
        ? pending.promise
        : Promise.resolve(coverage('dp', 2)),
  )
  await render()
  await click('补抓本专题题库')
  await render({ revision: 2 })
  await act(async () => pending.resolve({ items: [{ id: 'old', status: 'completed' }] }))
  await act(async () => vi.advanceTimersByTimeAsync(10000))
  expect(apiMock).toHaveBeenCalledTimes(3)
  expect(host.textContent).not.toContain('本次补抓：')
  expect(host.textContent).toContain('当前缓存可选 2 题')
})
it('preserves a failed refresh reason and lets users explicitly retry', async () => {
  apiMock.mockRejectedValueOnce(new Error('同类任务正在执行，请等待完成'))
  await render()
  await click('补抓本专题题库')
  expect(host.querySelector('[role=alert]')?.textContent).toContain('同类任务正在执行')
  expect(button('补抓本专题题库').disabled).toBe(false)
  expect(apiMock).toHaveBeenCalledTimes(1)
})
it('disables checks until a topic and valid independent rating bounds are selected', async () => {
  await render({ topicId: '' })
  expect(button('查看本专题候选').disabled).toBe(true)
  await render({ valid: false })
  expect(button('补抓本专题题库').disabled).toBe(true)
  expect(host.textContent).toContain('有效的 CF rating')
  expect(apiMock).not.toHaveBeenCalled()
})

it('discloses bounded paid method checks and disables them when the saved default model is unavailable', async () => {
  await render({ aiReady: false })
  expect(button('AI 核查细类候选').disabled).toBe(true)
  expect(host.textContent).toContain('读取最多 6 道候选官方题面')
  expect(host.textContent).toContain('可能产生额外费用')
  expect(host.textContent).toContain('不会改变 AC / 签到')
  expect(host.textContent).toContain('不会把 AI 核查通过当作模板')
  expect(host.querySelector('a[href="#settings"]')).not.toBeNull()
  expect(apiMock).not.toHaveBeenCalled()
})
it('checks method suitability once on explicit click using only topic and bounds, then restores isolated persisted counts', async () => {
  const summary = { checked_count: 6, accepted_count: 2, uncertain_count: 3, rejected_count: 1 }
  const queued = deferred<{ id: string; status: string }>()
  apiMock.mockImplementation((path: string, method?: string) =>
    method === 'POST'
      ? queued.promise
      : path === '/jobs'
        ? Promise.resolve({ items: [{ id: 'method-job', status: 'completed', result: summary }] })
        : Promise.resolve({ ...coverage(), method_check_summary: summary }),
  )
  await render({ minimum: 1000, maximum: 1800 })
  await act(async () => {
    button('AI 核查细类候选').click()
    button('AI 核查细类候选').click()
  })
  expect(apiMock.mock.calls.filter((call) => call[1] === 'POST')).toEqual([
    ['/training/method-check', 'POST', { topic_id: 'dp', cf_minimum: 1000, cf_maximum: 1800 }],
  ])
  expect(button('AI 正在核查方法').disabled).toBe(true)
  expect(button('补抓本专题题库').disabled).toBe(true)
  await act(async () => queued.resolve({ id: 'method-job', status: 'queued' }))
  expect(host.textContent).toContain(
    '已核查 6 题 · 方法可行 2 题 · 无法确定 3 题 · 未通过核查 1 题',
  )
  expect(host.textContent).toContain('当前缓存可选 8 题')
  await render({ minimum: 1000, maximum: 1800, revision: 1 })
  expect(host.textContent).toContain('方法可行 2 题')
  expect(apiMock.mock.calls.filter((call) => call[1] === 'POST')).toHaveLength(1)
})
it('preserves method-check failure and settings guidance without retrying the paid request automatically', async () => {
  apiMock.mockRejectedValueOnce(new Error('默认 AI 模型未配置，请在连接与设置保存'))
  await render()
  await click('AI 核查细类候选')
  expect(host.querySelector('[role=alert]')?.textContent).toContain('默认 AI 模型未配置')
  expect(host.querySelector('[role=alert] a')?.getAttribute('href')).toBe('#settings')
  expect(button('AI 核查细类候选').disabled).toBe(false)
  expect(apiMock).toHaveBeenCalledTimes(1)
})
it('drops a late paid-method receipt after topic changes and does not poll or replace current evidence', async () => {
  const pending = deferred<{ id: string; status: string }>()
  apiMock.mockReturnValue(pending.promise)
  await render()
  await click('AI 核查细类候选')
  await render({ topicId: 'bfs' })
  await act(async () => pending.resolve({ id: 'old-method-job', status: 'queued' }))
  expect(apiMock).toHaveBeenCalledTimes(1)
  expect(host.textContent).not.toContain('本次 AI 核查')
  expect(button('AI 核查细类候选').disabled).toBe(false)
})

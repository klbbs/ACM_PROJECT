import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { SpecialistTrainingPage } from './specialist-training'
const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('./api', () => ({ api: apiMock, names: { codeforces: 'Codeforces' } }))
let host: HTMLDivElement, root: Root
const props = {
  provider: 'api',
  model: 'fixture',
  aiPicker: null,
  revision: 0,
  onError: vi.fn(),
  onNotice: vi.fn(),
  onOpenProblem: vi.fn(),
}
const topics = {
  groups: [
    {
      id: 'graph',
      title: '图论',
      topics: [
        {
          id: 'shortest',
          title: '最短路',
          description: '从 Dijkstra 模板开始',
          source_url: 'https://oi-wiki.org/graph/shortest-path/',
        },
      ],
    },
  ],
}
const plan = {
  id: 'p1',
  topic_id: 'shortest',
  topic_title: '最短路',
  title: '最短路进阶路线',
  created_at: 100,
  items: [0, 1].map((position) => ({
    position,
    problem_id: `codeforces:1-${position}`,
    title: `训练题${position}`,
    platform: 'codeforces',
    url: 'https://codeforces.com/problemset/problem/1/A',
    difficulty: 1200 + position * 200,
    stage: position ? 'application' : 'template',
    reason: '练习状态转移',
    completed: true,
  })),
}
const source = {
  attempt_id: 'a99',
  submission_id: '123',
  platform: 'codeforces',
  account: 'alice',
  language: 'C++20',
  verdict: 'AC',
  source_available: true,
  disabled_reason: '',
}
const assessment = {
  result: {
    summary: '**复杂度合理**',
    dimensions: [
      {
        category: '边界处理',
        finding: '注意空图',
        evidence: '第 12 行处理源点',
        suggestion: '检查 n=1',
        confidence: 'medium',
      },
    ],
    strengths: ['命名清晰'],
    next_steps: ['练习稀疏图'],
    overall_confidence: 'medium',
  },
  submission: source,
}
const review = (extra = {}) => ({
  completed: true,
  sources: [source],
  assessment: null,
  messages: [],
  ...extra,
})
const button = (text: string) =>
  Array.from(host.querySelectorAll('button')).find((value) => value.textContent?.includes(text))!
const tick = async () =>
  act(async () => {
    await Promise.resolve()
  })
const render = async (extra = {}) =>
  act(async () => {
    root.render(<SpecialistTrainingPage {...props} {...extra} />)
  })
const click = async (text: string) =>
  act(async () => {
    button(text).click()
  })
function deferred() {
  let resolve!: (value: any) => void
  const promise = new Promise((done) => {
    resolve = done
  })
  return { promise, resolve }
}
beforeEach(() => {
  apiMock.mockReset()
  Object.values(props)
    .filter((value) => typeof value === 'function')
    .forEach((value: any) => value.mockClear?.())
  apiMock.mockImplementation((path: string) =>
    path === '/training/topics'
      ? Promise.resolve(topics)
      : path === '/training/plans'
        ? Promise.resolve({ plans: [plan] })
        : Promise.resolve(review()),
  )
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

it('restores saved progression and opens the canonical problem in the workbench without AI calls', async () => {
  await render()
  expect(host.textContent).toContain('最短路进阶路线')
  expect(host.textContent).toContain('模板入门')
  expect(host.textContent).toContain('综合应用')
  await click('前往工作台')
  expect(props.onOpenProblem).toHaveBeenCalledWith('codeforces:1-0')
  expect(apiMock.mock.calls.every((call) => !call[1])).toBe(true)
})
it('requires a configured model, then preserves old plans on failed generation', async () => {
  await render({ model: '' })
  expect(button('生成专项题单').disabled).toBe(true)
  await render()
  expect(button('生成专项题单').disabled).toBe(false)
  apiMock.mockRejectedValueOnce(new Error('模型输出不符合递进约束'))
  await click('生成专项题单')
  expect(host.textContent).toContain('最短路进阶路线')
  expect(host.querySelector('[role=alert]')?.textContent).toContain('已有题单保留')
  expect(apiMock).toHaveBeenLastCalledWith('/training/plans', 'POST', {
    topic_id: 'shortest',
    count: 4,
    mode: 'learn',
    provider: 'api',
    model: 'fixture',
  })
})
it('evaluates the selected official attempt only on clicking and prevents duplicate clicks', async () => {
  await render()
  await click('提交评估与讨论')
  expect(button('递交 AI 评估').disabled).toBe(false)
  expect(apiMock.mock.calls.every((call) => !call[1])).toBe(true)
  const pending = deferred()
  apiMock.mockReturnValueOnce(pending.promise)
  await act(async () => {
    button('递交 AI 评估').click()
    button('递交 AI 评估').click()
  })
  expect(apiMock.mock.calls.filter((call) => call[1] === 'POST')).toEqual([
    [
      '/training/plans/p1/items/0/assessment',
      'POST',
      { attempt_id: 'a99', provider: 'api', model: 'fixture' },
    ],
  ])
  await act(async () => pending.resolve(review({ assessment })))
  expect(host.textContent).toContain('评估对应提交 #123')
  expect(host.querySelector('.specialist-assessment strong')?.textContent).toBe('复杂度合理')
})
it('explains missing source and incomplete AC instead of reading editor drafts', async () => {
  await render()
  apiMock.mockResolvedValueOnce(
    review({
      completed: false,
      sources: [{ ...source, source_available: false, disabled_reason: '平台无法读取源码' }],
    }),
  )
  await click('提交评估与讨论')
  expect(button('递交 AI 评估').disabled).toBe(true)
  expect(host.textContent).toContain('本题尚未完成 AC')
  await click('提交评估与讨论')
  apiMock.mockResolvedValueOnce(
    review({
      sources: [{ ...source, source_available: false, disabled_reason: '平台无法读取源码' }],
    }),
  )
  await click('提交评估与讨论')
  expect(host.textContent).toContain('平台无法读取源码')
  expect(button('递交 AI 评估').disabled).toBe(true)
})
it('allows verified official-source retrieval without requiring a pasted draft', async () => {
  await render()
  apiMock.mockResolvedValueOnce(
    review({
      sources: [
        {
          ...source,
          attempt_id: null,
          source_available: false,
          can_fetch_source: true,
          disabled_reason: '递交AI时会尝试读取并核验该次官方源码',
        },
      ],
    }),
  )
  await click('提交评估与讨论')
  expect(button('递交 AI 评估').disabled).toBe(false)
  apiMock.mockResolvedValueOnce(review({ assessment }))
  await click('递交 AI 评估')
  expect(apiMock).toHaveBeenLastCalledWith('/training/plans/p1/items/0/assessment', 'POST', {
    submission_id: '123',
    handle: 'alice',
    provider: 'api',
    model: 'fixture',
  })
})
it('preserves completed assessment and messages on chat failure, and discards late answers after switching items', async () => {
  await render()
  apiMock.mockResolvedValueOnce(
    review({ assessment, messages: [{ role: 'assistant', content: '原有讨论' }] }),
  )
  await click('提交评估与讨论')
  apiMock.mockRejectedValueOnce(new Error('AI 网络异常'))
  await click('探讨其他思路')
  expect(host.textContent).toContain('原有讨论')
  expect(host.textContent).toContain('复杂度合理')
  expect(host.querySelector('[role=alert]')?.textContent).toContain('AI 网络异常')
  const pending = deferred()
  apiMock.mockReturnValueOnce(pending.promise)
  await click('探讨其他思路')
  const expand = Array.from(host.querySelectorAll('button')).filter((value) =>
    value.textContent?.includes('提交评估与讨论'),
  )
  apiMock.mockResolvedValueOnce(review())
  await act(async () => expand[1].click())
  await act(async () =>
    pending.resolve(
      review({ assessment, messages: [{ role: 'assistant', content: '上一题延迟答案' }] }),
    ),
  )
  expect(host.textContent).not.toContain('上一题延迟答案')
  expect(host.textContent).not.toContain('原有讨论')
})
it('shows initial loading errors and retries recovering saved plans without another generation request', async () => {
  apiMock.mockRejectedValueOnce(new Error('暂时无法读取'))
  await render()
  expect(host.querySelector('[role=alert]')?.textContent).toContain('暂时无法读取')
  await click('刷新训练')
  await tick()
  expect(host.textContent).toContain('最短路进阶路线')
  expect(apiMock.mock.calls.every((call) => !call[1])).toBe(true)
})

const change = async (selector: string, value: string) =>
  act(async () => {
    const element = host.querySelector(selector) as HTMLInputElement
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(element, value)
    element.dispatchEvent(new Event('input', { bubbles: true }))
    element.dispatchEvent(new Event('change', { bubbles: true }))
  })
it('offers four learning stages and a free quantity, without static consent checkboxes', async () => {
  await render()
  expect(host.querySelectorAll('input[type=checkbox]')).toHaveLength(0)
  expect(
    Array.from(host.querySelectorAll('input[type=radio]')).map(
      (entry) => (entry as HTMLInputElement).value,
    ),
  ).toEqual(['learn', 'consolidate', 'variation', 'strengthen'])
  await act(async () => (host.querySelector('input[value=variation]') as HTMLInputElement).click())
  await change('input[aria-label="专项题量"]', '12')
  apiMock.mockResolvedValueOnce({ ...plan, mode: 'variation', mode_title: '变式' })
  await click('生成专项题单')
  expect(apiMock).toHaveBeenLastCalledWith('/training/plans', 'POST', {
    topic_id: 'shortest',
    count: 12,
    mode: 'variation',
    provider: 'api',
    model: 'fixture',
  })
  expect(host.querySelector('.specialist-route-title')?.textContent).toContain('变式')
  await change('input[aria-label="专项题量"]', '')
  expect(button('生成专项题单').disabled).toBe(true)
  await change('input[aria-label="专项题量"]', '51')
  expect(button('生成专项题单').disabled).toBe(true)
})
it('searches topic names, paths and official English tags across categories while preserving selection', async () => {
  apiMock.mockResolvedValueOnce({
    groups: [
      ...topics.groups,
      {
        id: 'cf',
        title: 'Codeforces 官方标签',
        topics: [
          {
            id: 'cf-binary-search',
            title: '二分查找',
            description: '根据官方分类练习二分',
            path: ['算法基础', '二分'],
            keywords: ['binary search'],
            source_url: 'https://codeforces.com/problemset?tags=binary+search',
          },
        ],
      },
    ],
  })
  await render()
  await change('input[aria-label="搜索训练专题"]', 'binary search')
  const select = host.querySelector('select[aria-label="训练专题"]') as HTMLSelectElement
  expect(Array.from(select.options).map((entry) => entry.value)).toEqual([
    'shortest',
    'cf-binary-search',
  ])
  expect(select.value).toBe('shortest')
  await act(async () => {
    select.value = 'cf-binary-search'
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
  expect(select.value).toBe('cf-binary-search')
  expect((host.querySelector('select[aria-label="专题分类"]') as HTMLSelectElement).value).toBe(
    'cf',
  )
  await change('input[aria-label="搜索训练专题"]', 'no match')
  expect(select.value).toBe('cf-binary-search')
  expect(host.textContent).toContain('找到 0 个匹配专题')
})
it('restores old plans as learn but displays true non-template stages in other modes', async () => {
  apiMock.mockImplementation((path: string) =>
    Promise.resolve(
      path === '/training/topics'
        ? topics
        : {
            plans: [
              { ...plan, mode: 'strengthen', items: [{ ...plan.items[0], stage: 'strengthen' }] },
            ],
          },
    ),
  )
  await render()
  expect(host.querySelector('.specialist-route-title')?.textContent).toContain('加强')
  expect(host.querySelector('.specialist-step')?.textContent).toContain('加强练习')
  expect(host.querySelector('.specialist-step')?.textContent).not.toContain('模板入门')
})

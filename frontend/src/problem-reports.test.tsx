import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ProblemReports } from './problem-reports'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('./api', () => ({ api: apiMock, date: (value: number) => `时间 ${value}` }))
const pid = 'codeforces:4-A',
  rid = 'f'.repeat(32)
const assessment = {
  rating: 1840.5,
  confidence: 'medium',
  evidence: ['规模与算法证据'],
  limitations: ['仅AI估计'],
}
const report = {
  id: rid,
  problem_id: pid,
  model: 'synthetic',
  created_at: 100,
  report: {
    problem_id: pid,
    difficulty_assessment: assessment,
    suitable: true,
    problem_explanation: {
      title: '测试题',
      description: '给定整数',
      input_format: 'n',
      output_format: '结果',
      notes: [],
    },
    topic_fit: '训练价值',
    summary: '**公开报告摘要**',
    constraints: ['n范围'],
    approach: ['思路材料'],
    correctness: ['正确性依据'],
    complexity: 'O(n)',
    sample_walkthrough: ['样例依据'],
    pitfalls: ['边界'],
    alternative_approaches: ['另一方法'],
    limitations: ['AI推测，非官方题解'],
  },
}
let host: HTMLDivElement, root: Root
const button = (text: string) =>
  Array.from(document.querySelectorAll('button')).find(
    (e) => e.textContent?.includes(text) || e.getAttribute('aria-label') === text,
  )!
const click = async (text: string) =>
  act(async () => {
    button(text).click()
  })
const render = async (problemId = pid) =>
  act(async () => {
    root.render(<ProblemReports problemId={problemId} />)
  })
const list = (problemId = pid) => ({
  problem_id: problemId,
  items: [
    {
      id: rid,
      problem_id: problemId,
      model: 'synthetic',
      created_at: 100,
      difficulty_assessment: assessment,
    },
  ],
})
function deferred() {
  let resolve!: (v: unknown) => void
  const promise = new Promise((done) => {
    resolve = done
  })
  return { resolve, promise }
}

beforeEach(() => {
  apiMock.mockReset()
  apiMock.mockImplementation((path: string) =>
    Promise.resolve(path.includes(`/problem-reports/${rid}`) ? report : list()),
  )
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true,
    value: vi.fn(function (this: HTMLDialogElement) {
      this.open = true
    }),
  })
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value: vi.fn(function (this: HTMLDialogElement) {
      this.open = false
    }),
  })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.restoreAllMocks()
  delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).showModal
  delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).close
})

it('fetches only metadata before explicit spoiler confirmation and cancellation never fetches report', async () => {
  await render()
  expect(apiMock).toHaveBeenCalledTimes(1)
  expect(host.textContent).toContain('1840.5')
  expect(document.body.textContent).not.toContain('公开报告摘要')
  await click('共享题解报告')
  expect(document.body.textContent).toContain('此报告为ai生成的代码解析报告，你确定要现在看吗？')
  await click('取消')
  expect(apiMock).toHaveBeenCalledTimes(1)
})

it('shows full saved report after confirmation without AI request', async () => {
  await render()
  await click('共享题解报告')
  await act(async () => {
    button('确定查看').click()
    button('确定查看').click()
  })
  expect(apiMock.mock.calls[1][0]).toBe(
    `/problem-reports/${rid}?problem_id=${encodeURIComponent(pid)}`,
  )
  expect(document.body.querySelector('strong')?.textContent).toBe('公开报告摘要')
  for (const value of ['正确性依据', '样例依据', '另一方法', '边界', '1840.5'])
    expect(document.body.textContent).toContain(value)
  expect(apiMock.mock.calls.every((call) => call.length === 1)).toBe(true)
})

it('prevents duplicate fetches and retries after a failure', async () => {
  await render()
  const pending = deferred()
  apiMock.mockReturnValueOnce(pending.promise)
  await click('共享题解报告')
  await click('确定查看')
  expect(document.body.querySelector('[role=status]')).not.toBeNull()
  expect(apiMock).toHaveBeenCalledTimes(2)
  await act(async () => pending.resolve(Promise.reject(new Error('读取失败'))))
  expect(document.body.querySelector('[role=alert]')?.textContent).toContain('读取失败')
  await click('重试读取')
  expect(document.body.textContent).toContain('公开报告摘要')
})

it('rejects mismatched report rather than displaying another problem', async () => {
  await render()
  apiMock.mockResolvedValueOnce({ ...report, problem_id: 'codeforces:7-A' })
  await click('共享题解报告')
  await click('确定查看')
  expect(document.body.querySelector('[role=alert]')?.textContent).toContain('不匹配')
  expect(document.body.textContent).not.toContain('公开报告摘要')
})

it('drops in-flight content when switching problems', async () => {
  await render()
  const pending = deferred()
  apiMock.mockReturnValueOnce(pending.promise)
  await click('共享题解报告')
  await click('确定查看')
  apiMock.mockResolvedValueOnce(list('codeforces:7-A'))
  await render('codeforces:7-A')
  await act(async () => pending.resolve(report))
  expect(document.body.querySelector('dialog')).toBeNull()
  expect(document.body.textContent).not.toContain('公开报告摘要')
})

it('handles empty and failed directories with manual refresh', async () => {
  apiMock.mockRejectedValueOnce(new Error('目录失败'))
  await render()
  expect(host.textContent).toContain('目录失败')
  apiMock.mockResolvedValueOnce({ problem_id: pid, items: [] })
  await click('刷新共享报告目录')
  expect(host.textContent).toContain('暂无与当前题面对应的共享报告')
  expect(button('共享题解报告').disabled).toBe(true)
})

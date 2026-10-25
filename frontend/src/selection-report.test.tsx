// Written for the requested follow-up checks. Not executed during implementation.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { SelectionReportButton } from './selection-report'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('./api', () => ({ api: apiMock, date: (value: number) => `时间 ${value}` }))

const props = { reportId: 'report-1', problemId: 'codeforces:1-A', title: '训练题' }
const report = {
  id: 'report-1',
  problem_id: 'codeforces:1-A',
  created_at: 100,
  provider: 'api',
  model: 'fixture-model',
  result: {
    problem_id: 'codeforces:1-A',
    suitable: true,
    selection_fit: '适合基础训练',
    problem_summary: '**题意摘要**',
    constraints: ['核对上界'],
    approach: ['先理解状态定义'],
    implementation_notes: ['注意整数范围'],
    complexity: 'O(n)',
    limitations: ['AI 推测，未运行代码'],
  },
}
let host: HTMLDivElement
let root: Root
let previousShowModal: PropertyDescriptor | undefined
let previousClose: PropertyDescriptor | undefined
const dialog = () => document.querySelector('.selection-report-dialog') as HTMLDialogElement | null
const button = (text: string) =>
  Array.from(document.querySelectorAll('button')).find(
    (element) => element.textContent?.includes(text) || element.getAttribute('aria-label') === text,
  )!
const click = async (text: string) => act(async () => { button(text).click() })
const render = async (extra = {}) => act(async () => {
  root.render(<SelectionReportButton {...props} {...extra} />)
})
function deferred() {
  let resolve!: (value: unknown) => void
  const promise = new Promise((done) => { resolve = done })
  return { resolve, promise }
}

beforeEach(() => {
  apiMock.mockReset()
  apiMock.mockResolvedValue(report)
  previousShowModal = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'showModal')
  previousClose = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'close')
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true,
    value: vi.fn(function (this: HTMLDialogElement) { this.open = true }),
  })
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value: vi.fn(function (this: HTMLDialogElement) { this.open = false }),
  })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  if (previousShowModal) Object.defineProperty(HTMLDialogElement.prototype, 'showModal', previousShowModal)
  else delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).showModal
  if (previousClose) Object.defineProperty(HTMLDialogElement.prototype, 'close', previousClose)
  else delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).close
})

it('requires the exact custom confirmation and cancellation never reads a report', async () => {
  await render()
  expect(apiMock).not.toHaveBeenCalled()
  await click('查看 AI 报告')
  expect(dialog()?.textContent).toContain('此报告为ai生成的代码解析报告，你确定要现在看吗？')
  expect(apiMock).not.toHaveBeenCalled()
  await click('取消')
  expect(dialog()).toBeNull()
  expect(apiMock).not.toHaveBeenCalled()
})
it('reads only the chosen saved report after confirmation and renders Markdown safely', async () => {
  apiMock.mockResolvedValueOnce({
    ...report,
    result: { ...report.result, approach: ['<script>alert(1)</script>\n\n**状态定义**'] },
  })
  await render()
  await click('查看 AI 报告')
  await click('确定查看')
  expect(apiMock.mock.calls).toEqual([['/selection-reports/report-1']])
  expect(dialog()?.querySelector('strong')?.textContent).toBe('题意摘要')
  expect(dialog()?.querySelector('script')).toBeNull()
  expect(dialog()?.textContent).toContain('并非官方解释')
  expect(dialog()?.textContent).toContain('未运行代码')
  expect(dialog()?.textContent).toContain('不会调用 AI 重新生成')
})
it('explains unavailable legacy reports instead of borrowing another report', async () => {
  await render({ reportId: undefined })
  expect(button('查看 AI 报告').disabled).toBe(true)
  expect(host.textContent).toContain('此题单生成于报告功能上线前，暂无报告；重新AI选题后可查看')
  expect(apiMock).not.toHaveBeenCalled()
})
it.each([
  { ...report, id: 'other-report' },
  { ...report, problem_id: 'luogu:P1000' },
  { ...report, result: { ...report.result, problem_id: 'luogu:P1000' } },
])('rejects a mismatched report or problem identity', async (response) => {
  apiMock.mockResolvedValueOnce(response)
  await render()
  await click('查看 AI 报告')
  await click('确定查看')
  expect(dialog()?.querySelector('[role=alert]')?.textContent).toContain('报告与当前题目不匹配')
  expect(dialog()?.textContent).not.toContain('题意摘要')
})
it('preserves the confirmed report identity on a failed GET and explicit retry', async () => {
  apiMock.mockRejectedValueOnce(new Error('网络暂时不可用'))
  await render()
  await click('查看 AI 报告')
  await click('确定查看')
  expect(dialog()?.querySelector('[role=alert]')?.textContent).toContain('网络暂时不可用')
  expect(apiMock).toHaveBeenCalledTimes(1)
  await click('重试读取')
  expect(apiMock.mock.calls).toEqual([
    ['/selection-reports/report-1'], ['/selection-reports/report-1'],
  ])
  expect(dialog()?.querySelector('[role=alert]')).toBeNull()
  expect(dialog()?.textContent).toContain('题意摘要')
})
it('prevents duplicate reads while showing loading, and ignores a cancelled response', async () => {
  const pending = deferred()
  apiMock.mockReturnValueOnce(pending.promise)
  await render()
  await click('查看 AI 报告')
  const confirm = button('确定查看')
  await act(async () => { confirm.click(); confirm.click() })
  expect(apiMock).toHaveBeenCalledTimes(1)
  expect(dialog()?.querySelector('[role=status]')?.textContent).toContain('正在读取已保存报告')
  await click('关闭 AI 报告')
  await act(async () => pending.resolve(report))
  expect(dialog()).toBeNull()
})
it('discards a delayed response when the card changes to a different problem', async () => {
  const pending = deferred()
  apiMock.mockReturnValueOnce(pending.promise)
  await render()
  await click('查看 AI 报告')
  await click('确定查看')
  await render({ reportId: 'report-2', problemId: 'luogu:P1000', title: '另一题' })
  await act(async () => pending.resolve(report))
  expect(dialog()).toBeNull()
  await click('查看 AI 报告')
  expect(dialog()?.textContent).toContain('另一题')
  expect(dialog()?.textContent).not.toContain('题意摘要')
  expect(apiMock).toHaveBeenCalledTimes(1)
})
it('does not restore a report after its account surface has unmounted', async () => {
  const pending = deferred()
  apiMock.mockReturnValueOnce(pending.promise)
  await render()
  await click('查看 AI 报告')
  await click('确定查看')
  await act(async () => root.render(null))
  await act(async () => pending.resolve(report))
  expect(dialog()).toBeNull()
})

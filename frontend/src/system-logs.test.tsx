import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { SystemLogs } from './system-logs'
import { api } from './api'
vi.mock('./api', () => ({ api: vi.fn(), date: (value: number) => `time-${value}` }))

let host: HTMLDivElement, root: Root
const cursor = 'a'.repeat(32)
const entry = (id = 'first', message = '平台登录状态检测暂时失败') => ({
  id, message, timestamp: 1792700000, level: 'WARNING', category: 'platform',
  event: 'platform.check.retry', request_id: 'b'.repeat(32),
  details: { platform: 'nowcoder', upstream_status: 503, reason: 'temporarily_unavailable' } as Record<string, unknown>,
})
const page = (items = [entry()], overrides: Record<string, unknown> = {}) => ({
  items, next_cursor: null, has_more: false,
  storage: { available: true, max_bytes: 2 * 1024 * 1024, backup_count: 3, file_count: 1 },
  ...overrides,
})
function deferred() {
  let resolve!: (result: any) => void
  const promise = new Promise<any>((done) => { resolve = done })
  return { promise, resolve }
}
const render = async (strict = false) => act(async () => {
  root.render(strict ? <React.StrictMode><SystemLogs /></React.StrictMode> : <SystemLogs />)
})
function button(text: string) {
  return [...host.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.includes(text))!
}
const click = async (text: string) => act(async () => button(text).click())
async function select(index: number, value: string) {
  await act(async () => {
    const input = host.querySelectorAll('select')[index]
    input.value = value
    input.dispatchEvent(new Event('change', { bubbles: true }))
  })
}
async function search(value: string) {
  await act(async () => {
    const input = host.querySelector('input')!
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
}
const requested = () => new URL(vi.mocked(api).mock.lastCall![0], 'https://fixture.local').searchParams
beforeEach(() => {
  vi.mocked(api).mockReset()
  vi.mocked(api).mockResolvedValue(page())
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

it('reads only the admin endpoint and shows events, safe details and rotation space without automatic polling', async () => {
  vi.useFakeTimers()
  await render()
  expect(api).toHaveBeenCalledWith('/admin/logs?limit=50')
  expect(host.textContent).toContain('后台运行日志')
  expect(host.textContent).toContain('平台登录状态检测暂时失败')
  expect(host.textContent).toContain('警告')
  expect(host.textContent).toContain('平台响应状态')
  expect(host.textContent).toContain('503')
  expect(host.textContent).toContain('b'.repeat(32))
  expect(host.textContent).toContain('单文件上限 2 MB')
  expect(host.textContent).toContain('保留 3 份备份')
  expect(host.textContent).toContain('轮转空间预算约 8 MB')
  await act(async () => vi.advanceTimersByTimeAsync(120_000))
  expect(api).toHaveBeenCalledTimes(1)
  vi.useRealTimers()
  await click('刷新日志')
  expect(api).toHaveBeenCalledTimes(2)
  expect(vi.mocked(api).mock.calls.every(([, method]) => method === undefined)).toBe(true)
})

it('filters by level, category and an explicitly submitted keyword and resets cursor pagination', async () => {
  vi.mocked(api).mockResolvedValue(page([entry()], { next_cursor: cursor, has_more: true }))
  await render()
  await click('加载更多日志')
  expect(requested().get('before')).toBe(cursor)
  await select(0, 'ERROR')
  expect(requested().get('level')).toBe('ERROR')
  expect(requested().has('before')).toBe(false)
  await select(1, 'task')
  expect(requested().get('category')).toBe('task')
  await search('  daily roll & retry  ')
  expect(requested().get('q')).toBe('daily roll & retry')
  expect(requested().get('limit')).toBe('50')
  expect(requested().has('before')).toBe(false)
})

it('appends older records without duplicates and refresh replaces the accumulated page', async () => {
  vi.mocked(api)
    .mockResolvedValueOnce(page([entry()], { next_cursor: cursor, has_more: true }))
    .mockResolvedValueOnce(page([entry(), entry('older', '更早的任务完成事件')]))
    .mockResolvedValueOnce(page([entry('newest', '最新系统事件')]))
  await render()
  await click('加载更多日志')
  expect(host.querySelectorAll('.system-log-entry')).toHaveLength(2)
  expect(host.textContent).toContain('更早的任务完成事件')
  expect([...host.querySelectorAll('button')].some((item) => item.textContent?.includes('加载更多日志'))).toBe(false)
  await click('刷新日志')
  expect(host.querySelectorAll('.system-log-entry')).toHaveLength(1)
  expect(host.textContent).toContain('最新系统事件')
  expect(host.textContent).not.toContain('更早的任务完成事件')
})

it('keeps existing results on failed refresh and retries explicitly', async () => {
  await render()
  vi.mocked(api).mockRejectedValueOnce(new Error('暂时无法连接日志服务'))
  await click('刷新日志')
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('已有结果已保留')
  expect(host.textContent).toContain('平台登录状态检测暂时失败')
  await click('重试读取日志')
  expect(host.querySelector('[role="alert"]')).toBeNull()
  expect(host.querySelectorAll('.system-log-entry')).toHaveLength(1)
})

it('preserves a failed pagination cursor for retry without replacing or duplicating previous records', async () => {
  vi.mocked(api).mockResolvedValueOnce(page([entry()], { next_cursor: cursor, has_more: true }))
  await render()
  vi.mocked(api).mockRejectedValueOnce(new Error('读取旧日志超时'))
  await click('加载更多日志')
  expect(host.querySelectorAll('.system-log-entry')).toHaveLength(1)
  vi.mocked(api).mockResolvedValueOnce(page([entry('older')]))
  await click('重试读取日志')
  expect(requested().get('before')).toBe(cursor)
  expect(host.querySelectorAll('.system-log-entry')).toHaveLength(2)
})

it('does not let a late old filter response replace the current filter results', async () => {
  const old = deferred()
  vi.mocked(api).mockReturnValueOnce(old.promise)
  await render()
  vi.mocked(api).mockResolvedValueOnce(page([entry('error', '新的错误筛选结果')]))
  await select(0, 'ERROR')
  await act(async () => old.resolve(page([entry('old', '旧筛选结果')], { next_cursor: cursor, has_more: true })))
  expect(host.textContent).toContain('新的错误筛选结果')
  expect(host.textContent).not.toContain('旧筛选结果')
  expect([...host.querySelectorAll('button')].some((item) => item.textContent?.includes('加载更多日志'))).toBe(false)
})

it('marks preserved results as stale and does not page them after a failed new filter', async () => {
  vi.mocked(api).mockResolvedValueOnce(page([entry()], { next_cursor: cursor, has_more: true }))
  await render()
  vi.mocked(api).mockRejectedValueOnce(new Error('新筛选读取失败'))
  await select(0, 'ERROR')
  expect(host.textContent).toContain('当前保留上次筛选的结果')
  expect(host.textContent).toContain('平台登录状态检测暂时失败')
  expect([...host.querySelectorAll('button')].some((item) => item.textContent?.includes('加载更多日志'))).toBe(false)
  vi.mocked(api).mockResolvedValueOnce(page([]))
  await click('重试读取日志')
  expect(requested().get('level')).toBe('ERROR')
  expect(requested().has('before')).toBe(false)
  expect(host.textContent).toContain('没有符合筛选条件')
})

it('shows an empty state and safe storage warnings without server paths or unknown details', async () => {
  vi.mocked(api).mockResolvedValueOnce(page([{
    ...entry(), request_id: 'not-a-request-id',
    details: { status: 503, password: 'do-not-render', body: 'private-code', cookie: 'private-cookie', path: '/srv/private/logs' },
  }], { cursor_expired: true, storage: {
    available: false, error: 'permission denied /srv/private/logs', max_bytes: 1024, backup_count: 2, file_count: 0,
  } }))
  await render()
  expect(host.textContent).toContain('日志存储或写入异常')
  expect(host.textContent).toContain('分页对应的日志已轮转')
  expect(host.textContent).not.toContain('/srv/private/logs')
  expect(host.textContent).not.toContain('not-a-request-id')
  for (const text of ['do-not-render', 'private-code', 'private-cookie']) expect(host.textContent).not.toContain(text)
  vi.mocked(api).mockResolvedValueOnce(page([]))
  await click('刷新日志')
  expect(host.textContent).toContain('没有符合筛选条件的运行日志')
})

it('ignores duplicate load-more clicks while a read is pending', async () => {
  vi.mocked(api).mockResolvedValueOnce(page([entry()], { next_cursor: cursor, has_more: true }))
  await render()
  const pending = deferred()
  vi.mocked(api).mockReturnValueOnce(pending.promise)
  await click('加载更多日志')
  const pendingButton = button('正在加载更多')
  expect(pendingButton.disabled).toBe(true)
  await act(async () => pendingButton.click())
  expect(api).toHaveBeenCalledTimes(2)
  await act(async () => pending.resolve(page([entry('older')])))
  expect(host.querySelectorAll('.system-log-entry')).toHaveLength(2)
})

it('keeps no previous-user log data after remount and a denied admin request', async () => {
  await render()
  expect(host.textContent).toContain('平台登录状态检测暂时失败')
  await act(async () => root.unmount())
  root = createRoot(host)
  vi.mocked(api).mockRejectedValueOnce(new Error('仅管理员可查看'))
  await render()
  expect(host.textContent).not.toContain('平台登录状态检测暂时失败')
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('仅管理员可查看')
})

it('loads successfully under StrictMode without accepting the discarded first request', async () => {
  const first = deferred()
  vi.mocked(api).mockReturnValueOnce(first.promise).mockResolvedValueOnce(page([entry('strict', '当前挂载结果')]))
  await render(true)
  await act(async () => first.resolve(page([entry('old', '已卸载请求结果')])))
  expect(host.textContent).toContain('当前挂载结果')
  expect(host.textContent).not.toContain('已卸载请求结果')
})

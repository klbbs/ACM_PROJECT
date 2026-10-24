import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from './api'
import { useRecommendationReport } from './recommendation-report'

vi.mock('./api', () => ({ api: vi.fn() }))
const mockedApi = vi.mocked(api)
const roots: ReturnType<typeof createRoot>[] = []
afterEach(() => {
  act(() => roots.splice(0).forEach((root) => root.unmount()))
  document.body.innerHTML = ''
  vi.resetAllMocks()
})
const saved = (id = 'saved-recommendation') => ({
  id,
  kind: 'recommend',
  data: { selected_problems: [{ id: 'codeforces:2264C', title: '已保存的题目' }] },
})
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}
function mount(account: string | null = 'alice') {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  roots.push(root)
  const error = vi.fn()
  function Harness({ account, revision }: { account: string | null; revision: number }) {
    const report = useRecommendationReport(account, revision, error)
    return <output>{report ? JSON.stringify(report) : 'empty'}</output>
  }
  const update = async (account: string | null, revision = 0) => {
    await act(async () => root.render(<Harness account={account} revision={revision} />))
  }
  return { host, error, update, initial: () => update(account) }
}

it('recovers saved recommendations separately from newer analysis reports on every new visit', async () => {
  mockedApi.mockImplementation(async (path) =>
    path.startsWith('/reports?')
      ? {
          items: [
            { id: 'new-analysis', kind: 'analysis' },
            { id: 'saved', kind: 'recommend' },
          ],
        }
      : saved(),
  )
  const first = mount()
  await first.initial()
  expect(mockedApi).toHaveBeenCalledWith('/reports?kind=recommend')
  expect(mockedApi).toHaveBeenCalledWith('/reports/saved')
  expect(mockedApi).not.toHaveBeenCalledWith('/reports/new-analysis')
  expect(first.host.textContent).toContain('已保存的题目')
  const revisit = mount()
  await revisit.initial()
  expect(revisit.host.textContent).toContain('已保存的题目')
})

it('keeps the saved report during refresh failures and retries on the next refresh', async () => {
  mockedApi.mockImplementation(async (path) =>
    path.startsWith('/reports?') ? { items: [{ id: 'saved', kind: 'recommend' }] } : saved(),
  )
  const view = mount()
  await view.initial()
  mockedApi.mockRejectedValueOnce(new Error('网络连接失败'))
  await view.update('alice', 1)
  expect(view.host.textContent).toContain('已保存的题目')
  expect(view.error).toHaveBeenCalledWith('读取已保存的 AI 选题失败：网络连接失败')
  await view.update('alice', 2)
  expect(view.host.textContent).toContain('已保存的题目')
})

it('discards earlier refresh responses that arrive after the newest report', async () => {
  const late = deferred<any>()
  mockedApi.mockImplementation(async (path) => {
    if (path === '/reports?kind=recommend') return { items: [{ id: 'saved', kind: 'recommend' }] }
    return late.promise
  })
  const view = mount()
  await view.initial()
  mockedApi.mockImplementation(async (path) =>
    path.startsWith('/reports?') ? { items: [{ id: 'new', kind: 'recommend' }] } : saved('new'),
  )
  await view.update('alice', 1)
  await act(async () => late.resolve(saved('old')))
  expect(view.host.textContent).toContain('"id":"new"')
  expect(view.host.textContent).not.toContain('"id":"old"')
})

it('never displays another account report while loading or after a stale response', async () => {
  const late = deferred<any>()
  mockedApi.mockImplementation(async (path) =>
    path.startsWith('/reports?')
      ? { items: [{ id: 'alice-report', kind: 'recommend' }] }
      : saved('alice-report'),
  )
  const view = mount()
  await view.initial()
  mockedApi.mockImplementation(async () => late.promise)
  await view.update('bob')
  expect(view.host.textContent).toBe('empty')
  await view.update(null)
  await act(async () => late.resolve({ items: [{ id: 'alice-report', kind: 'recommend' }] }))
  expect(view.host.textContent).toBe('empty')
  mockedApi.mockImplementation(async (path) =>
    path.startsWith('/reports?')
      ? { items: [{ id: 'bob-report', kind: 'recommend' }] }
      : saved('bob-report'),
  )
  await view.update('bob')
  expect(view.host.textContent).toContain('bob-report')
  expect(view.host.textContent).not.toContain('alice-report')
})

it('clears deleted reports and ignores a detail response of the wrong report kind', async () => {
  mockedApi.mockImplementation(async (path) =>
    path.startsWith('/reports?') ? { items: [{ id: 'saved', kind: 'recommend' }] } : saved(),
  )
  const view = mount()
  await view.initial()
  mockedApi.mockResolvedValueOnce({ items: [] })
  await view.update('alice', 1)
  expect(view.host.textContent).toBe('empty')
  mockedApi.mockImplementation(async (path) =>
    path.startsWith('/reports?')
      ? { items: [{ id: 'wrong', kind: 'recommend' }] }
      : { id: 'wrong', kind: 'analysis' },
  )
  await view.update('alice', 2)
  expect(view.host.textContent).toBe('empty')
})

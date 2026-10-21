import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { ProblemRecordEditor, ProblemRecordsList } from './problem-records'
import { api, ApiError } from './api'
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<object>()), api: vi.fn() }))
const request = vi.mocked(api)
const initial = {
  problem_id: 'codeforces:4-A',
  title: 'Watermelon',
  url: 'https://codeforces.com/problemset/problem/4/A',
  platform: 'codeforces',
  favorite: false,
  note: 'server note',
  revision: 1,
  updated_at: 1,
}
afterEach(() => {
  vi.resetAllMocks()
  vi.useRealTimers()
})
async function render(
  element: React.ReactNode,
  check: (host: HTMLDivElement) => void | Promise<void>,
) {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  try {
    await act(async () => root.render(element))
    await check(host)
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
}
async function change(element: HTMLTextAreaElement | HTMLInputElement, value: string) {
  await act(async () => {
    const proto =
      element instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(element, value)
    element.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const button = (host: HTMLElement, text: string) =>
  Array.from(host.querySelectorAll('button')).find((item) => item.textContent === text)!

it('preserves unsaved notes while toggling favorite, then saves manually with the new revision', async () => {
  request.mockResolvedValueOnce(initial)
  request.mockResolvedValueOnce({ ...initial, favorite: true, revision: 2 })
  request.mockResolvedValueOnce({ ...initial, favorite: true, note: 'my new note', revision: 3 })
  await render(<ProblemRecordEditor problemId="codeforces:4-A" />, async (host) => {
    const input = host.querySelector('textarea')!
    await change(input, 'my new note')
    await act(async () => button(host, '收藏本题').click())
    expect(input.value).toBe('my new note')
    expect(request.mock.calls[1][2]).toEqual({ revision: 1, favorite: true })
    expect(host.textContent).toContain('已加入收藏')
    await act(async () => button(host, '保存备注').click())
    expect(request.mock.calls[2][2]).toEqual({ revision: 2, note: 'my new note' })
    expect(host.textContent).toContain('备注已保存')
    expect(button(host, '保存备注').disabled).toBe(true)
  })
})

it('keeps user input on failure and blocks navigation until saved or discarded', async () => {
  request.mockResolvedValueOnce(initial).mockRejectedValueOnce(new Error('连接失败'))
  await render(<ProblemRecordEditor problemId="codeforces:4-A" />, async (host) => {
    await change(host.querySelector('textarea')!, 'unsaved')
    await act(async () => button(host, '保存备注').click())
    expect(host.querySelector('[role=alert]')?.textContent).toContain('连接失败')
    expect(host.querySelector('textarea')!.value).toBe('unsaved')
    for (const name of ['acm:before-navigate', 'acm:before-problem-change']) {
      const event = new Event(name, { cancelable: true })
      await act(async () => {
        expect(window.dispatchEvent(event)).toBe(false)
      })
    }
    await act(async () => button(host, '放弃本次修改').click())
    expect(window.dispatchEvent(new Event('acm:before-problem-change', { cancelable: true }))).toBe(
      true,
    )
  })
})

it('does not overwrite notes on a conflict or reload without confirmation', async () => {
  request.mockResolvedValueOnce(initial).mockRejectedValueOnce(new ApiError('记录已更新', 409))
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  await render(<ProblemRecordEditor problemId="codeforces:4-A" />, async (host) => {
    await change(host.querySelector('textarea')!, 'local input')
    await act(async () => button(host, '保存备注').click())
    expect(host.textContent).toContain('记录已更新')
    expect(button(host, '保存备注').disabled).toBe(true)
    await act(async () => button(host, '重新载入记录（替换当前输入）').click())
    expect(confirm).toHaveBeenCalledOnce()
    expect(host.querySelector('textarea')!.value).toBe('local input')
    expect(request).toHaveBeenCalledTimes(2)
  })
  confirm.mockRestore()
})

it('renders a searchable private list as plain text and opens selected problem', async () => {
  vi.useFakeTimers()
  request.mockResolvedValue({
    items: [
      {
        ...initial,
        favorite: true,
        note: '<img src=x onerror=bad()>',
        title: '<script>bad()</script>',
      },
    ],
    total: 1,
  })
  const open = vi.fn()
  await render(<ProblemRecordsList onOpenProblem={open} />, async (host) => {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(150)
    })
    expect(host.querySelector('img,script')).toBeNull()
    expect(host.textContent).toContain('<img src=x onerror=bad()>')
    await act(async () => button(host, '去工作台').click())
    expect(open).toHaveBeenCalledWith('codeforces:4-A')
    await change(host.querySelector('input:not([type=checkbox])')!, 'Water')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(150)
    })
    expect(request.mock.calls.at(-1)?.[0]).toContain('search=Water')
  })
})

it('blocks navigation while a bookmark request is in flight, then releases the guard', async () => {
  let finish!: (value: typeof initial) => void
  request.mockResolvedValueOnce(initial).mockReturnValueOnce(
    new Promise((resolve) => {
      finish = resolve
    }),
  )
  await render(<ProblemRecordEditor problemId="codeforces:4-A" />, async (host) => {
    await act(async () => button(host, '收藏本题').click())
    const event = new Event('acm:before-problem-change', { cancelable: true })
    await act(async () => {
      expect(window.dispatchEvent(event)).toBe(false)
    })
    expect(host.textContent).toContain('正在保存题目记录')
    await act(async () => finish({ ...initial, favorite: true, revision: 2 }))
    expect(window.dispatchEvent(new Event('acm:before-problem-change', { cancelable: true }))).toBe(
      true,
    )
  })
})

it('ignores a stale read after a different problem has been opened', async () => {
  let finish!: (value: typeof initial) => void
  request.mockReturnValueOnce(
    new Promise((resolve) => {
      finish = resolve
    }),
  )
  request.mockResolvedValueOnce({
    ...initial,
    problem_id: 'luogu:P1001',
    title: 'A+B',
    note: 'new problem note',
  })
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  try {
    await act(async () => root.render(<ProblemRecordEditor problemId="codeforces:4-A" />))
    await act(async () => root.render(<ProblemRecordEditor problemId="luogu:P1001" />))
    await act(async () => finish(initial))
    expect(host.querySelector('textarea')!.value).toBe('new problem note')
    expect(host.textContent).toContain('A+B')
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
})

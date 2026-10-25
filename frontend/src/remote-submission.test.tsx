import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { api, ApiError } from './api'
import { RemoteSubmissionPanel, type RemoteSubmissionPanelProps } from './remote-submission'
vi.mock('./api', async (original) => ({ ...(await original<object>()), api: vi.fn() }))
const request = vi.mocked(api)
const compilers = {
  available: true,
  items: [
    { id: '54', name: 'GNU G++17 7.3.0' },
    { id: '89', name: 'GNU G++20 13.2 (64 bit)' },
    { id: '91', name: 'Python 3.12.1' },
  ],
}
const queued = {
  id: 'remote-1',
  problem_id: 'codeforces:4-A',
  handle: 'demo',
  status: 'queued',
  revision: 7,
  compiler_name: 'GNU G++20 13.2 (64 bit)',
  created_at: 100,
}
function props(changes: Partial<RemoteSubmissionPanelProps> = {}): RemoteSubmissionPanelProps {
  return {
    problemId: 'codeforces:4-A',
    handles: ['demo'],
    language: 'cpp20',
    code: 'int main(){}',
    save: vi.fn().mockResolvedValue(7),
    onError: vi.fn(),
    onChanged: vi.fn(),
    ...changes,
  }
}
afterEach(() => {
  vi.resetAllMocks()
  vi.useRealTimers()
})
async function render(
  values: RemoteSubmissionPanelProps,
  check: (host: HTMLDivElement, root: Root) => void | Promise<void>,
) {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  try {
    await act(async () => root.render(<RemoteSubmissionPanel {...values} />))
    await check(host, root)
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
}
const button = (host: HTMLElement, text: string) =>
  Array.from(host.querySelectorAll('button')).find((item) => item.textContent === text)!
async function click(host: HTMLElement, text: string) {
  await act(async () => button(host, text).click())
}
async function select(host: HTMLElement, label: string, value: string) {
  await act(async () => {
    const input = host.querySelector(`select[aria-label="${label}"]`) as HTMLSelectElement
    input.value = value
    input.dispatchEvent(new Event('change', { bubbles: true }))
  })
}
function standardApi(create: (data: any) => unknown = () => queued) {
  request.mockImplementation(async (path, method, data) => {
    if (path.startsWith('/remote-submissions?')) return { items: [] }
    if (path === '/remote-submissions/options') return compilers
    if (path === '/remote-submissions' && method === 'POST') return create(data)
    throw new Error(`Unexpected API ${path}`)
  })
}
it('requires an account and actual login/compiler check before sending', async () => {
  standardApi()
  await render(props({ handles: [] }), (host) => {
    expect(button(host, '提交到平台').disabled).toBe(true)
    expect(button(host, '读取平台编译器').disabled).toBe(true)
    expect(host.textContent).toContain('尚未配置此平台账号')
    expect(host.querySelector('a[href="#settings"]')).not.toBeNull()
  })
  await render(props(), async (host) => {
    expect(button(host, '提交到平台').disabled).toBe(true)
    expect(host.textContent).toContain('填写账号不代表已经登录')
    await click(host, '读取平台编译器')
    expect(button(host, '提交到平台').disabled).toBe(false)
    expect(request).toHaveBeenCalledWith('/remote-submissions/options', 'POST', {
      problem_id: 'codeforces:4-A',
      handle: 'demo',
    })
  })
})
it('selects actual C++20 and never silently falls back to C++17', async () => {
  standardApi()
  await render(props(), async (host) => {
    await click(host, '读取平台编译器')
    const picker = host.querySelector('select[aria-label="实际平台编译器"]') as HTMLSelectElement
    expect(picker.value).toBe('89')
    expect(picker.textContent).toContain('GNU G++20 13.2 (64 bit)')
    expect(picker.textContent).not.toContain('Python')
    await select(host, '实际平台编译器', '54')
    expect(button(host, '提交到平台').disabled).toBe(true)
    expect(host.textContent).toContain('请选择支持 C++20')
  })
  request.mockImplementation(async (path) =>
    path.startsWith('/remote-submissions?')
      ? { items: [] }
      : { available: true, items: [{ id: '54', name: 'GNU G++17' }] },
  )
  await render(props(), async (host) => {
    await click(host, '读取平台编译器')
    expect(
      (host.querySelector('select[aria-label="实际平台编译器"]') as HTMLSelectElement).value,
    ).toBe('')
    expect(button(host, '提交到平台').disabled).toBe(true)
  })
})
it('awaits saved revision, sends no source, and prevents double clicking', async () => {
  standardApi()
  let saved!: (revision: number) => void
  const values = props({
    save: vi.fn(
      () =>
        new Promise<number | null>((resolve) => {
          saved = resolve
        }),
    ),
  })
  await render(values, async (host) => {
    await click(host, '读取平台编译器')
    await act(async () => {
      button(host, '提交到平台').click()
      button(host, '提交到平台')?.click()
    })
    expect(values.save).toHaveBeenCalledOnce()
    expect(request.mock.calls.filter((call) => call[0] === '/remote-submissions')).toHaveLength(0)
    await act(async () => saved(7))
    const creates = request.mock.calls.filter((call) => call[0] === '/remote-submissions')
    expect(creates).toHaveLength(1)
    expect(creates[0][2]).toEqual({
      problem_id: 'codeforces:4-A',
      handle: 'demo',
      revision: 7,
      compiler_id: '89',
      request_key: expect.any(String),
    })
    expect(creates[0][2]).not.toHaveProperty('code')
    expect(host.textContent).toContain('已排队，等待提交')
    expect(host.textContent).toContain('已保存版本 #7')
    expect(values.onChanged).toHaveBeenCalled()
    expect(button(host, '提交到平台').disabled).toBe(true)
  })
})
it('reuses the key and original revision after transport failure without resaving', async () => {
  let attempts = 0
  standardApi(() => {
    if (++attempts === 1) throw new Error('网络中断')
    return queued
  })
  const values = props()
  await render(values, async (host) => {
    await click(host, '读取平台编译器')
    await click(host, '提交到平台')
    expect(host.textContent).toContain('发送结果尚未确认')
    await act(async () => {
      expect(
        window.dispatchEvent(new Event('acm:before-problem-change', { cancelable: true })),
      ).toBe(false)
    })
    await click(host, '查询本次提交')
    const creates = request.mock.calls.filter((call) => call[0] === '/remote-submissions')
    expect(creates).toHaveLength(2)
    expect(creates[0][2]).toEqual(creates[1][2])
    expect(values.save).toHaveBeenCalledOnce()
    expect(host.textContent).not.toContain('查询本次提交')
  })
})
it('never sends on failed save and exposes real authentication errors', async () => {
  standardApi()
  const values = props({ save: vi.fn().mockResolvedValue(null) })
  await render(values, async (host) => {
    await click(host, '读取平台编译器')
    await click(host, '提交到平台')
    expect(host.textContent).toContain('本次未发送到平台')
    expect(request.mock.calls.filter((call) => call[0] === '/remote-submissions')).toHaveLength(0)
    expect(values.onError).toHaveBeenCalled()
  })
  request.mockImplementation(async (path) => {
    if (path.startsWith('/remote-submissions?')) return { items: [] }
    throw new ApiError('Cookie 已失效，请重新绑定并检测登录', 400)
  })
  await render(props(), async (host) => {
    await click(host, '读取平台编译器')
    expect(host.textContent).toContain('Cookie 已失效')
    expect(button(host, '提交到平台').disabled).toBe(true)
  })
})
it('drops compiler results for a previously selected account', async () => {
  let finish!: (value: typeof compilers) => void
  request.mockImplementation(async (path, _method, data: any) => {
    if (path.startsWith('/remote-submissions?')) return { items: [] }
    if (data.handle === 'demo')
      return new Promise((resolve) => {
        finish = resolve
      })
    return { available: true, items: [{ id: 'new', name: 'GNU G++20 new account' }] }
  })
  await render(props({ handles: ['demo', 'other'] }), async (host) => {
    await click(host, '读取平台编译器')
    await select(host, '代提交平台账号', 'other')
    await click(host, '读取平台编译器')
    await act(async () => finish(compilers))
    const picker = host.querySelector('select[aria-label="实际平台编译器"]') as HTMLSelectElement
    expect(picker.value).toBe('new')
    expect(picker.textContent).not.toContain('13.2')
  })
})
it('restores pending, polls by GET and stops after AC', async () => {
  vi.useFakeTimers()
  let reads = 0
  request.mockImplementation(async (path) => {
    if (path.startsWith('/remote-submissions?'))
      return {
        items: [
          {
            ...queued,
            status: ++reads === 1 ? 'judging' : 'completed',
            verdict: reads === 1 ? null : 'AC',
          },
        ],
      }
    throw new Error('must only read')
  })
  const values = props()
  await render(values, async (host) => {
    expect(host.textContent).toContain('官方判题中')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })
    expect(host.textContent).toContain('AC · 通过！')
    expect(host.querySelector('.is-ac')).not.toBeNull()
    expect(values.onChanged).toHaveBeenCalled()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9000)
    })
    expect(reads).toBe(2)
    expect(request.mock.calls.every((call) => call[1] === undefined)).toBe(true)
  })
})
it('keeps polling after temporary list failure and shows WA in red', async () => {
  vi.useFakeTimers()
  let reads = 0
  request.mockImplementation(async () => {
    if (++reads === 2) throw new Error('temporary error')
    return { items: [{ ...queued, status: reads >= 3 ? 'completed' : 'judging', verdict: 'WA' }] }
  })
  await render(props(), async (host) => {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000)
    })
    expect(host.textContent).toContain('WA · 请查看判题结果')
    expect(host.querySelector('.is-error')).not.toBeNull()
    expect(reads).toBe(3)
  })
})
it('automatically refreshes an old judging result and shows the official verdict', async () => {
  vi.useFakeTimers()
  const staleJudging = {
    ...queued,
    status: 'judging',
    submission_id: '12345',
    updated_at: Math.floor(Date.now() / 1000) - 30,
  }
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/remote-submissions?')) return { items: [staleJudging] }
    if (path === '/remote-submissions/remote-1/refresh' && method === 'POST')
      return { ...staleJudging, status: 'completed', verdict: 'AC', updated_at: Math.floor(Date.now() / 1000) }
    throw new Error(`Unexpected API ${path}`)
  })
  await render(props(), async (host) => {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })
    expect(host.textContent).toContain('AC · 通过！')
    expect(request).toHaveBeenCalledWith('/remote-submissions/remote-1/refresh', 'POST', {})
    expect(request.mock.calls.some(([path, method]) => path === '/remote-submissions' && method === 'POST')).toBe(false)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9000)
    })
    expect(request.mock.calls.filter(([path]) => path.endsWith('/refresh'))).toHaveLength(1)
  })
})
it('requires official confirmation to dismiss uncertain sends and never resubmits', async () => {
  const row = {
    ...queued,
    status: 'uncertain',
    official_url: 'https://codeforces.com/submissions/demo',
  }
  request.mockImplementation(async (path, _method, data) => {
    if (path.startsWith('/remote-submissions?')) return { items: [row] }
    if (path.endsWith('/refresh')) return row
    if (path.endsWith('/dismiss')) {
      expect(data).toEqual({ confirmed_not_submitted: true })
      return { ...row, status: 'failed', message: '已解除本次提交' }
    }
    throw new Error('unexpected code send')
  })
  await render(props(), async (host) => {
    expect(host.textContent).toContain('发送结果未确认（不会自动重交）')
    expect(button(host, '解除本次提交').disabled).toBe(true)
    await click(host, '刷新判题')
    expect(request.mock.calls.at(-1)?.[0]).toBe('/remote-submissions/remote-1/refresh')
    expect(button(host, '提交到平台').disabled).toBe(true)
    await act(async () => (host.querySelector('input[type=checkbox]') as HTMLInputElement).click())
    expect(button(host, '解除本次提交').disabled).toBe(false)
    await click(host, '解除本次提交')
    expect(host.textContent).toContain('已解除本次提交')
    expect(request.mock.calls.some((call) => call[0] === '/remote-submissions')).toBe(false)
  })
})
it('renders status as text and rejects unsafe official links', async () => {
  request.mockResolvedValue({
    items: [
      {
        ...queued,
        status: 'blocked',
        message: '<img src=x onerror=bad()>',
        official_url: 'javascript:alert(1)',
      },
    ],
  })
  await render(props(), (host) => {
    expect(host.querySelector('img,script')).toBeNull()
    expect(host.textContent).toContain('<img src=x onerror=bad()>')
    expect(host.textContent).toContain('需要登录或验证')
    expect(host.querySelector('a[target="_blank"]')).toBeNull()
  })
})
it('ignores stale list reads after a different problem opens', async () => {
  let finish!: (value: unknown) => void
  request.mockImplementation(async (path) =>
    path.includes('codeforces%3A4-A')
      ? new Promise((resolve) => {
          finish = resolve
        })
      : { items: [] },
  )
  const values = props()
  await render(values, async (host, root) => {
    await act(async () =>
      root.render(<RemoteSubmissionPanel {...values} problemId="luogu:P1001" />),
    )
    await act(async () => finish({ items: [queued] }))
    expect(host.textContent).not.toContain('已排队')
    expect(host.textContent).toContain('提交到 洛谷')
  })
})

it('avoids IOI library compilers and C++98 while choosing a modern normal compiler', async () => {
  request.mockImplementation(async (path) =>
    path.startsWith('/remote-submissions?')
      ? { items: [] }
      : {
          available: true,
          items: [
            { id: 'old', name: 'GNU G++98' },
            { id: 'ioi', name: 'C++ IOI-Style(GNU++20) (GCC14.2.0)' },
            { id: 'modern', name: 'C++23 (GCC15.2.0)' },
          ],
        },
  )
  await render(props(), async (host) => {
    await click(host, '读取平台编译器')
    const picker = host.querySelector('select[aria-label="实际平台编译器"]') as HTMLSelectElement
    expect(picker.value).toBe('modern')
    expect(picker.textContent).not.toContain('IOI-Style')
    await select(host, '实际平台编译器', 'old')
    expect(button(host, '提交到平台').disabled).toBe(true)
  })
})

it('selects modern Java without falling back to Java8', async () => {
  request.mockImplementation(async (path) =>
    path.startsWith('/remote-submissions?')
      ? { items: [] }
      : {
          available: true,
          items: [
            { id: 'java8', name: 'Java8 (OpenJDK8)' },
            { id: 'java21', name: 'Java (OpenJDK 21)' },
          ],
        },
  )
  await render(props({ language: 'java17' }), async (host) => {
    await click(host, '读取平台编译器')
    expect(
      (host.querySelector('select[aria-label="实际平台编译器"]') as HTMLSelectElement).value,
    ).toBe('java21')
  })
})

it('explains unsupported platforms without pretending a saved handle enables submission', async () => {
  standardApi()
  for (const problemId of ['luogu:P1001', 'nowcoder:1000']) {
    await render(props({ problemId }), (host) => {
      expect(host.textContent).toContain('暂不支持本站代提交')
      expect(button(host, '提交到平台').disabled).toBe(true)
      expect(button(host, '读取平台编译器').disabled).toBe(true)
    })
  }
  expect(request.mock.calls.some((call) => call[0] === '/remote-submissions/options')).toBe(false)
})

it('releases an explicitly rejected create intent so login settings remain accessible', async () => {
  standardApi(() => {
    throw new ApiError('请重新检测登录', 400)
  })
  await render(props(), async (host) => {
    await click(host, '读取平台编译器')
    await click(host, '提交到平台')
    expect(host.textContent).toContain('请重新检测登录')
    expect(host.textContent).not.toContain('查询本次提交')
    expect(window.dispatchEvent(new Event('acm:before-navigate', { cancelable: true }))).toBe(true)
  })
})

it('shows the actual other-problem blocker and does not claim a rejected request was sent', async () => {
  let rejected = false
  const old = { ...queued, id: 'old-task', problem_id: 'codeforces:5-A', status: 'uncertain' }
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/remote-submissions?')) return { items: [], blocking: rejected ? [old] : [] }
    if (path === '/remote-submissions/options') return compilers
    if (path === '/remote-submissions' && method === 'POST') {
      rejected = true
      throw new ApiError('此平台账号已有提交正在处理或发送结果未确认', 409)
    }
    throw new Error(`Unexpected API ${path}`)
  })
  const values = props()
  await render(values, async (host) => {
    await click(host, '读取平台编译器')
    await click(host, '提交到平台')
    expect(host.textContent).toContain('codeforces:5-A')
    expect(host.textContent).toContain('另一道题的旧提交')
    expect(host.textContent).not.toContain('正在确认账号')
    expect(host.textContent).not.toContain('查询本次提交')
    expect(host.querySelector('a[href="#settings"]')).toBeNull()
    expect(host.querySelector('a[href="https://codeforces.com/submissions/demo"]')).not.toBeNull()
    expect(button(host, '提交到平台').disabled).toBe(true)
    expect(values.save).toHaveBeenCalledOnce()
    expect(window.dispatchEvent(new Event('acm:before-navigate', { cancelable: true }))).toBe(true)
  })
})

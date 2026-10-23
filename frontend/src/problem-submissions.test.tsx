import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { api, ApiError } from './api'
import {
  ProblemSubmissions,
  allowedRecordUrl,
  type ProblemSubmissionsProps,
} from './problem-submissions'
vi.mock('./api', async (original) => ({ ...(await original<object>()), api: vi.fn() }))
const request = vi.mocked(api)

const wa = {
  submission_id: '12345',
  account: 'demo',
  verdict: 'WA',
  submitted_at: 1792640000,
  language: 'Python 3',
  url: 'https://codeforces.com/contest/4/submission/12345',
  attempt_id: null,
  has_local_code: false,
  code_verified: false,
}
const ac = {
  submission_id: '12346',
  account: 'demo',
  verdict: 'AC',
  submitted_at: 1792640600,
  language: 'GNU G++20',
  url: 'https://codeforces.com/contest/4/submission/12346',
  attempt_id: 'att-2',
  has_local_code: true,
  code_verified: false,
}
const altItem = { ...wa, submission_id: '77777', account: 'alt', verdict: 'AC' }

function listing(changes: Record<string, any> = {}) {
  return {
    problem_id: 'codeforces:4-A',
    handles: ['demo', 'alt'],
    sync_status: null,
    items: [wa, ac],
    ...changes,
  }
}

function props(changes: Partial<ProblemSubmissionsProps> = {}): ProblemSubmissionsProps {
  return {
    problemId: 'codeforces:4-A',
    handles: ['demo'],
    editorCode: '',
    active: false,
    revision: 0,
    aiReady: true,
    coach: vi.fn().mockResolvedValue({ id: 'review-job' }),
    loadCode: vi.fn().mockResolvedValue(undefined),
    onChanged: vi.fn().mockResolvedValue(undefined),
    onError: vi.fn(),
    ...changes,
  }
}

afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.resetAllMocks()
})

async function render(
  values: ProblemSubmissionsProps,
  check: (host: HTMLDivElement, root: Root) => void | Promise<void>,
) {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  try {
    await act(async () => root.render(<ProblemSubmissions {...values} />))
    await check(host, root)
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
}

const button = (host: HTMLElement, text: string) =>
  Array.from(host.querySelectorAll('button')).find((item) =>
    item.textContent?.includes(text),
  ) as HTMLButtonElement | undefined
async function click(host: HTMLElement, text: string) {
  const target = button(host, text)
  if (!target) throw new Error(`button ${text} not found`)
  await act(async () => target.click())
}
async function pick(host: HTMLElement, value: string) {
  await act(async () => {
    const input = host.querySelector('select[aria-label="同步账号"]') as HTMLSelectElement
    input.value = value
    input.dispatchEvent(new Event('change', { bubbles: true }))
  })
}
async function tick(ms = 1300) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
  await act(async () => {
    await Promise.resolve()
  })
}
/** Flush the chained listing/adoption effects that follow one mocked response. */
async function settle(rounds = 10) {
  await act(async () => {
    for (let index = 0; index < rounds; index++) await Promise.resolve()
  })
}

it('only trusts official https record links', () => {
  expect(allowedRecordUrl('codeforces:4-A', 'https://codeforces.com/contest/4/submission/1')).toBe(
    'https://codeforces.com/contest/4/submission/1',
  )
  expect(allowedRecordUrl('codeforces:4-A', 'http://codeforces.com/contest/4/submission/1')).toBe(
    undefined,
  )
  expect(allowedRecordUrl('codeforces:4-A', 'https://codeforces.com.evil.test/x')).toBeUndefined()
  expect(allowedRecordUrl('codeforces:4-A', 'https://atcoder.jp/x')).toBeUndefined()
  expect(
    allowedRecordUrl('atcoder:abc001_1', 'https://atcoder.jp/contests/abc001/submissions/1'),
  ).toBe('https://atcoder.jp/contests/abc001/submissions/1')
  expect(allowedRecordUrl('nowcoder:1', 'https://ac.nowcoder.com/x')).toBe(
    'https://ac.nowcoder.com/x',
  )
})

it('shows clear disabled reasons for AC, judging and missing source', async () => {
  request.mockImplementation(async (path) => {
    if (path.startsWith('/problem-submissions/'))
      return listing({ items: [
        { ...ac, submission_id: 'ac-row', analysis_available: false },
        { ...wa, submission_id: 'pending-row', verdict: 'TESTING', analysis_available: false },
        { ...wa, submission_id: 'source-row', attempt_id: 'att-source', analysis_available: false,
          analysis_disabled_reason: '尚未读取并验证本次提交源码；请先获取本次代码' },
      ] })
    throw new Error(`Unexpected API ${path}`)
  })
  await render(props({ aiReady: true }), async (host) => {
    const records = Array.from(host.querySelectorAll('article.problem-submission'))
    const analyses = records.map((record) => record.querySelector('button[title]') as HTMLButtonElement)
    expect(analyses.every((item) => item.disabled)).toBe(true)
    expect(records[0].textContent).toContain('AC 记录无需错因分析')
    expect(records[1].textContent).toContain('判题中，结束后才能分析')
    expect(records[2].textContent).toContain('先获取本次代码')
  })
})

it('explains when the AI model is not configured', async () => {
  request.mockImplementation(async (path) => {
    if (path.startsWith('/problem-submissions/'))
      return listing({ items: [{ ...wa, attempt_id: 'att-model', analysis_available: true }] })
    throw new Error(`Unexpected API ${path}`)
  })
  await render(props({ aiReady: false }), async (host) => {
    const record = host.querySelector('article.problem-submission')!
    expect(button(host, 'AI 分析')!.disabled).toBe(true)
    expect(record.textContent).toContain('尚未配置 AI 模型')
  })
})

it('allows retry after an AI job fails', async () => {
  vi.useFakeTimers()
  const coach = vi.fn().mockResolvedValue({ id: 'review-job' })
  request.mockImplementation(async (path) => {
    if (path.startsWith('/problem-submissions/'))
      return listing({ items: [{ ...wa, attempt_id: 'att-wa', code_verified: true, analysis_available: true }] })
    if (path === '/jobs') return { items: [{ id: 'review-job', status: 'failed', error: 'AI 暂时不可用' }] }
    throw new Error(`Unexpected API ${path}`)
  })
  await render(props({ coach }), async (host) => {
    await click(host, 'AI 分析')
    await tick(1200)
    expect(host.textContent).toContain('AI 暂时不可用')
    expect(button(host, '重试 AI 分析')).toBeTruthy()
    await click(host, '重试 AI 分析')
    expect(coach).toHaveBeenCalledTimes(2)
  })
})

it('synchronizes the current problem with no code, revision or submission id', async () => {
  vi.useFakeTimers()
  const values = props()
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/problem-submissions/') && method !== 'POST') return listing()
    if (path === '/problem-submissions/codeforces%3A4-A/sync' && method === 'POST')
      return { id: 'sync-1', status: 'queued' }
    if (path === '/jobs')
      return {
        items: [
          {
            id: 'sync-1',
            kind: 'wb_problem_submissions',
            status: 'completed',
            result: {
              problem_id: 'codeforces:4-A',
              handle: 'demo',
              count: 2,
              complete: true,
              coverage: '本场比赛最近 600 条提交（仅保留当前题目）',
              warnings: [],
              message: '已同步本题提交记录',
            },
          },
        ],
      }
    throw new Error(`Unexpected API ${path}`)
  })
  await render(values, async (host) => {
    expect(host.textContent).toContain('本题提交记录')
    await click(host, '同步本题提交')
    expect(request).toHaveBeenCalledWith('/problem-submissions/codeforces%3A4-A/sync', 'POST', {
      handle: 'demo',
    })
    const sent = request.mock.calls.find((call) => call[1] === 'POST')![2] as Record<string, any>
    expect(sent).toEqual({ handle: 'demo' })
    expect(sent).not.toHaveProperty('code')
    expect(sent).not.toHaveProperty('revision')
    expect(sent).not.toHaveProperty('submission_id')
    expect(host.textContent).toContain('正在同步…')
    await tick()
    expect(host.textContent).toContain('已同步 2 条本题提交')
    expect(host.textContent).toContain('查询范围：本场比赛最近 600 条提交')
    expect(values.onChanged).toHaveBeenCalled()
    expect(host.querySelector('input[aria-label="本次平台提交号"]')).toBeNull()
  })
})

it('shows judge records without any local code and only when synchronized', async () => {
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/problem-submissions/') && method !== 'POST')
      return listing({ items: [wa, ac] })
    throw new Error(`Unexpected API ${path}`)
  })
  await render(props(), (host) => {
    expect(host.textContent).toContain('#12345')
    expect(host.textContent).toContain('Python 3')
    expect(host.textContent).toContain('WA')
    expect(host.textContent).toContain('AC')
    expect(host.querySelectorAll('article.problem-submission')).toHaveLength(2)
    expect(host.querySelector('article.problem-submission.is-ac')).not.toBeNull()
    expect(host.querySelector('article.problem-submission.is-error')).not.toBeNull()
    expect(
      host.querySelector('a[href="https://codeforces.com/contest/4/submission/12345"]'),
    ).not.toBeNull()
    // Records are readable without fetching or loading any source.
    expect(host.querySelector('pre')).toBeNull()
  })
})

it('explains an empty range instead of pretending the sync failed', async () => {
  vi.useFakeTimers()
  const values = props()
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/problem-submissions/') && method !== 'POST')
      return listing({ items: [] })
    if (path.endsWith('/sync')) return { id: 'sync-2', status: 'queued' }
    if (path === '/jobs')
      return {
        items: [
          {
            id: 'sync-2',
            status: 'completed',
            result: {
              count: 0,
              complete: true,
              coverage: '公开提交列表最近 3 页',
              message: '在本次查询范围内没有找到本题提交；已保留历史记录',
            },
          },
        ],
      }
    throw new Error(`Unexpected API ${path}`)
  })
  await render(values, async (host) => {
    expect(host.textContent).toContain('还没有本题的提交记录')
    await click(host, '同步本题提交')
    await tick()
    expect(host.textContent).toContain('已同步')
    expect(host.textContent).toContain('没有找到本题提交')
    expect(host.textContent).not.toContain('同步失败')
    expect(values.onChanged).toHaveBeenCalled()
  })
})

it('keeps previous records and reports partial success', async () => {
  vi.useFakeTimers()
  const values = props()
  let syncs = 0
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/problem-submissions/') && method !== 'POST')
      return listing({ items: syncs ? [wa, ac] : [wa] })
    if (path.endsWith('/sync')) {
      syncs++
      return { id: 'sync-3', status: 'queued' }
    }
    if (path === '/jobs')
      return {
        items: [
          {
            id: 'sync-3',
            status: 'completed',
            result: {
              count: 1,
              complete: false,
              coverage: 'CF 本场最近 200 条',
              warnings: ['后续分页读取失败'],
              message: '本次查询未完整完成，已保留可读取记录和历史记录',
              error: '平台返回 429，已停止继续读取',
            },
          },
        ],
      }
    throw new Error(`Unexpected API ${path}`)
  })
  await render(values, async (host) => {
    expect(host.textContent).toContain('#12345')
    await click(host, '同步本题提交')
    await tick()
    expect(host.textContent).toContain('部分成功')
    expect(host.textContent).toContain('原有记录已保留')
    expect(host.textContent).toContain('平台返回 429')
    expect(host.textContent).toContain('#12346')
  })
})

it('keeps judge records when the source is unavailable', async () => {
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/problem-submissions/') && method !== 'POST') return listing()
    if (path.endsWith('/source') && method === 'POST')
      throw new ApiError('判题记录保留；官方源码暂不可读：需要有效的登录会话', 400)
    throw new Error(`Unexpected API ${path}`)
  })
  const values = props()
  await render(values, async (host) => {
    await click(host, '获取本次代码')
    expect(request).toHaveBeenCalledWith('/problem-submissions/codeforces%3A4-A/source', 'POST', {
      handle: 'demo',
      submission_id: '12345',
    })
    expect(host.textContent).toContain('判题已同步，源码暂不可读')
    expect(host.textContent).toContain('需要有效的登录会话')
    expect(host.textContent).toContain('#12345')
    expect(host.querySelector('pre')).toBeNull()
    expect(values.onChanged).not.toHaveBeenCalled()
  })
})

it('analyzes the linked submission directly without loading it into the editor', async () => {
  vi.useFakeTimers()
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/problem-submissions/') && method !== 'POST')
      return listing({ items: [wa, { ...ac, verdict: 'WA', attempt_id: 'att-wa', code_verified: true, analysis_available: true }] })
    if (path === '/jobs') return { items: [{ id: 'review-job', status: 'completed' }] }
    throw new Error(`Unexpected API ${path}`)
  })
  const values = props()
  await render(values, async (host, root) => {
    const second = Array.from(host.querySelectorAll('article.problem-submission'))[1] as HTMLElement
    await click(second, 'AI 分析')
    await tick(1200)
    expect(values.loadCode).not.toHaveBeenCalled()
    expect(values.coach).toHaveBeenCalledWith('/workbench/attempt/att-wa/submission-review')
  })
})

it('requires explicit confirmation before replacing a non-empty draft', async () => {
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/problem-submissions/') && method !== 'POST')
      return listing({ items: [{ ...ac, attempt_id: 'att-ac', has_local_code: true }] })
    if (path === '/workbench/attempt/att-ac')
      return { id: 'att-ac', code: 'int main(){}', language: 'cpp17', code_verified: false }
    throw new Error(`Unexpected API ${path}`)
  })
  const values = props({ editorCode: '// my draft' })
  await render(values, async (host) => {
    await act(async () => {
      ;(host.querySelector('button.secondary') as HTMLButtonElement).click()
    })
    const load = button(host, '载入编辑器')!
    expect(load.disabled).toBe(true)
    expect(host.textContent).toContain('确认用这次提交的代码替换编辑器当前草稿')
    const confirm = host.querySelector('.problem-submission-confirm input') as HTMLInputElement
    await act(async () => {
      confirm.click()
    })
    expect(load.disabled).toBe(false)
    await click(host, '载入编辑器')
    expect(values.loadCode).toHaveBeenCalledWith('int main(){}', 'cpp17', expect.any(Function))
    expect(host.textContent).toContain('已载入编辑器')
  })
})

it('loads directly into an empty editor and surfaces a refused replacement', async () => {
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/problem-submissions/') && method !== 'POST')
      return listing({ items: [{ ...ac, attempt_id: 'att-ac' }] })
    if (path === '/workbench/attempt/att-ac')
      return { id: 'att-ac', code: 'int main(){}', language: 'cpp17', code_verified: true }
    throw new Error(`Unexpected API ${path}`)
  })
  const loadCode = vi.fn().mockRejectedValue(new Error('当前草稿尚未保存成功'))
  const values = props({ loadCode })
  await render(values, async (host) => {
    await act(async () => {
      ;(host.querySelector('button.secondary') as HTMLButtonElement).click()
    })
    expect(button(host, '载入编辑器')!.disabled).toBe(false)
    expect(host.textContent).toContain('代码来自官方页面并核对过本人身份')
    await click(host, '载入编辑器')
    expect(loadCode).toHaveBeenCalledOnce()
    expect(host.textContent).toContain('未载入编辑器：当前草稿尚未保存成功')
  })
})

it('never starts the same synchronization twice', async () => {
  let release!: () => void
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/problem-submissions/') && method !== 'POST') return listing()
    if (path.endsWith('/sync')) {
      await gate
      return { id: 'sync-4', status: 'queued' }
    }
    throw new Error(`Unexpected API ${path}`)
  })
  const values = props()
  await render(values, async (host) => {
    const target = button(host, '同步本题提交')!
    await act(async () => {
      target.click()
      target.click()
    })
    await act(async () => {
      target.click()
    })
    expect(request.mock.calls.filter((call) => String(call[0]).endsWith('/sync'))).toHaveLength(1)
    await act(async () => release())
  })
})

it('a late completion for the previous account cannot overwrite the new one', async () => {
  vi.useFakeTimers()
  const values = props({ handles: ['demo', 'alt'] })
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/problem-submissions/') && method !== 'POST')
      return path.includes('handle=alt')
        ? listing({ items: [altItem] })
        : listing({ items: [wa, ac] })
    if (path.endsWith('/sync')) return { id: 'sync-5', status: 'queued' }
    if (path === '/jobs')
      return {
        items: [
          {
            id: 'sync-5',
            status: 'completed',
            result: { count: 2, complete: true, coverage: '本场最近 600 条' },
          },
        ],
      }
    throw new Error(`Unexpected API ${path}`)
  })
  await render(values, async (host) => {
    await click(host, '同步本题提交')
    await pick(host, 'alt')
    expect(host.textContent).toContain('#77777')
    expect(host.textContent).not.toContain('#12345')
    await tick()
    expect(host.textContent).not.toContain('已同步 2 条本题提交')
    expect(host.textContent).toContain('#77777')
    expect(request).toHaveBeenCalledWith(
      '/problem-submissions/codeforces%3A4-A?handle=alt',
    )
  })
})

it('reports platform cooldown and blocks repeat clicks', async () => {
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/problem-submissions/') && method !== 'POST') return listing()
    if (path.endsWith('/sync'))
      throw new ApiError('请等待 20 秒后再次同步本题记录', 429, 20)
    throw new Error(`Unexpected API ${path}`)
  })
  const values = props()
  await render(values, async (host) => {
    await click(host, '同步本题提交')
    expect(host.textContent).toContain('请等待 20 秒后再次同步本题记录（20 秒后可重试）')
    expect(button(host, '同步本题提交')!.disabled).toBe(true)
    expect(host.textContent).toContain('20 秒后可重试')
    expect(values.onError).toHaveBeenCalledWith('请等待 20 秒后再次同步本题记录')
    // Existing records stay readable during the cooldown.
    expect(host.textContent).toContain('#12345')
  })
})

it('highlights the record opened from the submission list', async () => {
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/problem-submissions/') && method !== 'POST') return listing()
    throw new Error(`Unexpected API ${path}`)
  })
  await render(
    props({ selected: { submission_id: 12345, account: 'demo', request_id: 1 } }),
    (host) => {
      const rows = host.querySelectorAll('article.problem-submission')
      expect(rows[0].className).toContain('history-selected')
      expect(rows[1].className).not.toContain('history-selected')
    },
  )
})

it('requires an account before synchronization can start', async () => {
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/problem-submissions/') && method !== 'POST')
      return listing({ items: [], handles: [] })
    throw new Error(`Unexpected API ${path}`)
  })
  await render(props({ handles: [] }), (host) => {
    expect(button(host, '同步本题提交')!.disabled).toBe(true)
    expect(host.textContent).toContain('尚未配置 Codeforces 账号')
    expect(host.querySelector('a[href="#settings"]')).not.toBeNull()
  })
})

it('renders a failed job as a kept-record error, not as success', async () => {
  vi.useFakeTimers()
  const values = props()
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/problem-submissions/') && method !== 'POST') return listing()
    if (path.endsWith('/sync')) return { id: 'sync-6', status: 'queued' }
    if (path === '/jobs')
      return { items: [{ id: 'sync-6', status: 'failed', error: '平台登录已失效', result: null }] }
    throw new Error(`Unexpected API ${path}`)
  })
  await render(values, async (host) => {
    await click(host, '同步本题提交')
    await tick()
    expect(host.textContent).toContain('平台登录已失效')
    expect(host.textContent).not.toContain('已同步')
    expect(host.textContent).toContain('#12345')
  })
})

it('adopts an account discovered by the listing and keeps the chosen one', async () => {
  const ghost = { ...wa, submission_id: '99999', account: 'ghost' }
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/problem-submissions/') && method !== 'POST') {
      if (!path.includes('handle=')) return listing({ items: [ghost] })
      if (path.includes('handle=alt')) return listing({ items: [altItem] })
      return listing({ items: [wa] })
    }
    throw new Error(`Unexpected API ${path}`)
  })
  await render(props({ handles: [] }), async (host, root) => {
    await settle()
    const picker = host.querySelector('select[aria-label="同步账号"]') as HTMLSelectElement
    // The listing knows the account even though the workbench payload did not yet.
    expect(picker.value).toBe('demo')
    expect(button(host, '同步本题提交')!.disabled).toBe(false)
    expect(request).toHaveBeenCalledWith('/problem-submissions/codeforces%3A4-A')
    expect(request).toHaveBeenCalledWith('/problem-submissions/codeforces%3A4-A?handle=demo')
    // Rows read without an account must not be shown as this account's records.
    expect(host.textContent).not.toContain('#99999')
    expect(host.textContent).toContain('#12345')
    await pick(host, 'alt')
    expect(picker.value).toBe('alt')
    expect(host.textContent).toContain('#77777')
    expect(host.textContent).not.toContain('#12345')
    // A parent refresh keeps the user's choice instead of falling back.
    await act(async () => root.render(<ProblemSubmissions {...props({ handles: [], revision: 1 })} />))
    await settle()
    expect((host.querySelector('select[aria-label="同步账号"]') as HTMLSelectElement).value).toBe(
      'alt',
    )
    expect(host.textContent).toContain('#77777')
  })
})

it('a first source read makes that record reviewable without any reload', async () => {
  vi.useFakeTimers()
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/problem-submissions/') && method !== 'POST')
      // Deliberately stale: the record still has no attempt on every reload.
      return listing({ items: [wa] })
    if (path.endsWith('/source') && method === 'POST')
      return {
        problem_id: 'codeforces:4-A',
        submission_id: '12345',
        handle: 'demo',
        code: 'print(0)',
        language: 'python3',
        attempt_id: 'att-new',
        code_verified: true,
        provenance: 'official',
      }
    if (path === '/jobs') return { items: [{ id: 'review-job', status: 'completed' }] }
    throw new Error(`Unexpected API ${path}`)
  })
  const values = props()
  await render(values, async (host, root) => {
    expect(button(host, 'AI 分析')).toBeTruthy()
    await click(host, '获取本次代码')
    const card = host.querySelector('article.problem-submission') as HTMLElement
    expect(card.querySelector('pre')!.textContent).toBe('print(0)')
    // The attempt id from the first read is usable immediately.
    expect(button(host, 'AI 分析')!.disabled).toBe(false)
    await act(async () => root.render(<ProblemSubmissions {...values} revision={1} />))
    await settle()
    expect(button(host, 'AI 分析')).toBeTruthy()
    await click(host, 'AI 分析')
    await tick(1200)
    expect(values.coach).toHaveBeenCalledWith('/workbench/attempt/att-new/submission-review')
  })
})

it('late code reads for the previous account never reach the new selection', async () => {
  // The same submission id under another account is exactly what a shared cache
  // key would leak, so the guard must use the account, not only the problem.
  const sameIdAlt = { ...wa, account: 'alt' }
  const listingFor = (path: string) =>
    listing({ items: path.includes('handle=alt') ? [sameIdAlt] : [wa] })
  let release!: (value: any) => void
  const gate = new Promise((resolve) => {
    release = resolve
  })
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/problem-submissions/') && method !== 'POST') return listingFor(path)
    if (path.endsWith('/source') && method === 'POST') return gate
    throw new Error(`Unexpected API ${path}`)
  })
  await render(props({ handles: ['demo', 'alt'] }), async (host) => {
    await click(host, '获取本次代码')
    expect(host.textContent).toContain('正在读取代码…')
    await pick(host, 'alt')
    expect(host.textContent).toContain('alt')
    await act(async () =>
      release({
        code: 'print("demo account code")',
        language: 'python3',
        attempt_id: 'att-demo',
        code_verified: true,
      }),
    )
    await settle()
    expect(host.textContent).not.toContain('demo account code')
    expect(button(host, '分析本次错因（不提供答案）')).toBeUndefined()
    // The stale request must not leave the new account's button spinning.
    expect(host.textContent).not.toContain('正在读取代码…')
    expect(button(host, '获取本次代码')!.disabled).toBe(false)
  })

  // The rejection path is guarded the same way: no error from the old account.
  let fail!: (error: Error) => void
  const failing = new Promise((_resolve, reject) => {
    fail = reject
  })
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/problem-submissions/') && method !== 'POST') return listingFor(path)
    if (path.endsWith('/source') && method === 'POST') return failing
    throw new Error(`Unexpected API ${path}`)
  })
  await render(props({ handles: ['demo', 'alt'] }), async (host) => {
    await click(host, '获取本次代码')
    await pick(host, 'alt')
    await act(async () => fail(new Error('旧账号登录已失效')))
    await settle()
    expect(host.textContent).not.toContain('旧账号登录已失效')
    expect(host.textContent).not.toContain('源码暂不可读')
  })
})

it('loading into the editor stays bound to the account that started it', async () => {
  // Both accounts report the same submission id, and both views hold code, so an
  // unguarded late load would claim the new account's row.
  const sameIdAlt = { ...wa, account: 'alt', attempt_id: 'att-alt', has_local_code: true }
  request.mockImplementation(async (path, method) => {
    if (path.startsWith('/problem-submissions/') && method !== 'POST')
      return listing({
        items: path.includes('handle=alt')
          ? [sameIdAlt]
          : [{ ...wa, attempt_id: 'att-ac', has_local_code: true }],
      })
    if (path === '/workbench/attempt/att-ac')
      return { id: 'att-ac', code: 'int main(){}', language: 'cpp17', code_verified: false }
    if (path === '/workbench/attempt/att-alt')
      return { id: 'att-alt', code: 'print(1)', language: 'python3', code_verified: false }
    throw new Error(`Unexpected API ${path}`)
  })
  let release!: () => void
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  const loadCode = vi.fn<ProblemSubmissionsProps['loadCode']>(() => gate)
  const values = props({ handles: ['demo', 'alt'], loadCode })
  await render(values, async (host) => {
    await act(async () => {
      ;(host.querySelector('button.secondary') as HTMLButtonElement).click()
    })
    await click(host, '载入编辑器')
    expect(loadCode).toHaveBeenCalledOnce()
    const stillCurrent = loadCode.mock.calls[0][2]!
    expect(stillCurrent()).toBe(true)
    await pick(host, 'alt')
    expect(stillCurrent()).toBe(false)
    // Open the new account's code so a stale message would be visible on its row.
    await act(async () => {
      ;(host.querySelector('button.secondary') as HTMLButtonElement).click()
    })
    expect(host.querySelector('pre')!.textContent).toBe('print(1)')
    await act(async () => release())
    await settle()
    // The parent receives the guard so it can recheck after awaiting a save;
    // this view must not claim the old operation for the new selection either.
    expect(host.textContent).not.toContain('已载入编辑器')
    expect(host.textContent).not.toContain('未载入编辑器')
  })
})

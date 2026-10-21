import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { describe, expect, it, vi } from 'vitest'
import { parseSubmissionId, SubmissionForm } from './submission-history'

vi.mock('./api', () => ({
  api: vi.fn(),
  date: () => '',
  names: { codeforces: 'Codeforces', atcoder: 'AtCoder', luogu: '洛谷', nowcoder: '牛客' },
}))

describe('official submission identifiers', () => {
  it.each([
    ['codeforces', ' 123 ', 123],
    ['codeforces', 'https://codeforces.com/contest/4/submission/123', 123],
    ['codeforces', 'https://codeforces.com/problemset/submission/4/123', 123],
    ['codeforces', 'https://codeforces.com/gym/100001/submission/123', 123],
    ['atcoder', 'https://atcoder.jp/contests/abc001/submissions/123', 123],
    ['luogu', 'R123', 123],
    ['luogu', 'https://www.luogu.com.cn/record/123', 123],
    ['nowcoder', 'https://ac.nowcoder.com/acm/contest/view-submission?submissionId=123', 123],
  ])('accepts %s official record %s', (platform, input, expected) => {
    expect(parseSubmissionId(String(input), String(platform))).toBe(expected)
  })
  it.each([
    ['codeforces', 'https://evil.invalid/contest/4/submission/123'],
    ['codeforces', 'https://codeforces.com@evil.invalid/contest/4/submission/123'],
    ['codeforces', 'https://atcoder.jp/contests/abc001/submissions/123'],
    ['codeforces', 'https://codeforces.com/contest/4/problem/123'],
    ['luogu', 'https://www.luogu.com.cn/record/123?token=abc'],
    [
      'nowcoder',
      'https://ac.nowcoder.com/acm/contest/view-submission?submissionId=123&submissionId=124',
    ],
    ['atcoder', 'R123'],
    ['codeforces', '0'],
    ['codeforces', '1000000000001'],
    ['luogu', '1e6'],
  ])('rejects %s invalid record %s', (platform, input) => {
    expect(parseSubmissionId(input, platform)).toBeNull()
  })
})

function fill(host: HTMLElement, selector: string, value: string) {
  const input = host.querySelector(selector) as HTMLInputElement
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

it('refreshes configured accounts and explains missing prerequisites instead of silently disabling', async () => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  const submit = vi.fn(),
    refresh = vi.fn(async () => {}),
    sync = vi.fn(async () => {})
  const render = (handles: string[]) =>
    root.render(
      <SubmissionForm
        handles={handles}
        platform="luogu"
        code="submitted code"
        language="cpp20"
        active={false}
        onSubmit={submit}
        onRefreshAccounts={refresh}
        onSyncPlatform={sync}
      />,
    )
  try {
    await act(async () => render([]))
    expect(host.textContent).toContain('尚未读取到此平台账号')
    await act(async () =>
      [...host.querySelectorAll('button')].find((b) => b.textContent === '刷新已配置账号')!.click(),
    )
    expect(refresh).toHaveBeenCalledOnce()
    expect(submit).not.toHaveBeenCalled()
    await act(async () => render(['1428433']))
    expect((host.querySelector('select') as HTMLSelectElement).value).toBe('1428433')
    await act(async () =>
      [...host.querySelectorAll('button')]
        .find((b) => b.textContent === '同步此平台提交记录')!
        .click(),
    )
    expect(sync).toHaveBeenCalledOnce()
    await act(async () => fill(host, 'input[aria-label="本次平台提交号"]', 'R123'))
    await act(async () =>
      (host.querySelector('input[type="checkbox"]') as HTMLInputElement).click(),
    )
    expect(host.textContent).toContain('将核验 洛谷 提交 #123')
    await act(async () =>
      host
        .querySelector('form')!
        .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })),
    )
    expect(submit).toHaveBeenCalledWith({
      handle: '1428433',
      submission_id: 123,
      code_matches_submission: true,
    })
    await act(async () =>
      fill(host, 'input[aria-label="本次平台提交号"]', 'https://evil.invalid/record/124'),
    )
    expect(host.textContent).toContain('提交格式无效')
    expect((host.querySelector('input[type="checkbox"]') as HTMLInputElement).checked).toBe(false)
    expect((host.querySelector('button:not([type])') as HTMLButtonElement).disabled).toBe(true)
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
})

it('shows asynchronous association failure and allows retry without losing the form', async () => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  const submit = vi.fn(async () => {
    throw new Error('提交题号不匹配')
  })
  try {
    await act(async () =>
      root.render(
        <SubmissionForm
          handles={['demo']}
          code="x"
          language="cpp20"
          active={false}
          onSubmit={submit}
        />,
      ),
    )
    await act(async () =>
      fill(
        host,
        'input[aria-label="本次平台提交号"]',
        'https://codeforces.com/contest/4/submission/123',
      ),
    )
    await act(async () =>
      (host.querySelector('input[type="checkbox"]') as HTMLInputElement).click(),
    )
    await act(async () =>
      host
        .querySelector('form')!
        .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })),
    )
    expect(host.textContent).toContain('提交题号不匹配')
    expect((host.querySelector('button:not([type])') as HTMLButtonElement).disabled).toBe(false)
    expect(submit).toHaveBeenCalledOnce()
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
})

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { it, expect, vi } from 'vitest'
import { SelfTestPanel } from './self-test-panel'
vi.mock('./api', () => ({ api: vi.fn() }))
import { api } from './api'

it.each([
  [false, 'FAIL · 与预期输出不一致', '.selftest-fail'],
  [true, 'PASS · 与预期输出一致', '.selftest-pass'],
  [null, '运行成功 · 尚未对照预期输出', '.selftest-pass'],
])(
  'renders self-test result %s without claiming a platform AC',
  async (matched, label, selector) => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    vi.mocked(api).mockImplementation(async (path: string) =>
      path.endsWith('capabilities')
        ? {
            enabled: true,
            languages: { cpp20: { id: 100, name: 'C++20 GCC12' } },
            message: 'sandbox',
          }
        : ({
            status_id: 3,
            stdout: '2',
            stderr: '',
            compile_output: '',
            time: '.01',
            memory: 1,
            matched,
            truncated: false,
          } as never),
    )
    try {
      await act(async () =>
        root.render(<SelfTestPanel code="int main(){}" language="cpp20" samples={[]} />),
      )
      const consent = host.querySelectorAll<HTMLInputElement>('input[type=checkbox]')[1]
      await act(async () => consent.click())
      await act(async () => host.querySelector<HTMLButtonElement>('button')!.click())
      expect(host.textContent).toContain(label)
      expect(host.querySelector(selector)).not.toBeNull()
      expect(host.querySelector('style')?.textContent).toContain('prefers-reduced-motion')
    } finally {
      await act(async () => root.unmount())
      host.remove()
    }
  },
)

it('opens the console immediately and never sends code before explicit consent', async () => {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  vi.mocked(api).mockReset()
  vi.mocked(api).mockResolvedValue({
    enabled: true,
    languages: { cpp20: { id: 100, name: 'C++20' } },
    message: 'sandbox',
  } as never)
  try {
    await act(async () =>
      root.render(
        <SelfTestPanel
          code="int main(){}"
          language="cpp20"
          samples={[{ input: '1', output: '2' }]}
        />,
      ),
    )
    expect(host.querySelector('details')?.open).toBe(true)
    expect(host.querySelector('textarea[aria-label="自测输入"]')).not.toBeNull()
    expect(host.querySelector<HTMLButtonElement>('button')?.disabled).toBe(true)
    expect(vi.mocked(api).mock.calls.map((c) => c[0])).toEqual([
      '/workbench/self-test/capabilities',
    ])
    const select = host.querySelector<HTMLSelectElement>('select')!
    await act(async () => {
      select.value = '0'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(host.querySelector<HTMLTextAreaElement>('[aria-label="自测输入"]')?.value).toBe('1')
    expect(host.querySelector<HTMLTextAreaElement>('[aria-label="自测预期输出"]')?.value).toBe('2')
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
})

it('rejects a late result after source changes while the console stays open', async () => {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  let finish: (v: unknown) => void = () => {}
  vi.mocked(api).mockReset()
  vi.mocked(api).mockImplementation(async (path: string) =>
    path.endsWith('capabilities')
      ? {
          enabled: true,
          languages: { cpp20: { id: 100, name: 'C++20' } },
          message: 'sandbox',
        }
      : ((await new Promise<unknown>((resolve) => {
          finish = resolve
        })) as never),
  )
  try {
    await act(async () =>
      root.render(<SelfTestPanel code="int main(){}" language="cpp20" samples={[]} />),
    )
    await act(async () =>
      host.querySelectorAll<HTMLInputElement>('input[type=checkbox]')[1].click(),
    )
    await act(async () => host.querySelector<HTMLButtonElement>('button')!.click())
    await act(async () =>
      root.render(<SelfTestPanel code="int main(){return 1;}" language="cpp20" samples={[]} />),
    )
    await act(async () =>
      finish({
        status_id: 3,
        stdout: 'old',
        stderr: '',
        compile_output: '',
        time: '.01',
        memory: 1,
        matched: true,
        truncated: false,
      }),
    )
    expect(host.querySelector('.wb-selftest-result')).toBeNull()
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('已变更')
    expect(host.querySelector('details')?.open).toBe(true)
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
})

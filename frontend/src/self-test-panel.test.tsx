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
])('renders self-test result %s without claiming a platform AC', async (matched, label, selector) => {
  const host = document.createElement('div'); document.body.appendChild(host)
  const root = createRoot(host)
  vi.mocked(api).mockImplementation(async (path: string) => path.endsWith('capabilities') ? {
    enabled: true, languages: { cpp20: { id: 100, name: 'C++20 GCC12' } }, message: 'sandbox',
  } : { status_id: 3, stdout: '2', stderr: '', compile_output: '', time: '.01', memory: 1, matched, truncated: false } as never)
  try {
    await act(async () => root.render(<SelfTestPanel code="int main(){}" language="cpp20" samples={[]} />))
    const consent = host.querySelectorAll<HTMLInputElement>('input[type=checkbox]')[1]
    await act(async () => consent.click())
    await act(async () => host.querySelector<HTMLButtonElement>('button')!.click())
    expect(host.textContent).toContain(label)
    expect(host.querySelector(selector)).not.toBeNull()
    expect(host.querySelector('style')?.textContent).toContain('prefers-reduced-motion')
  } finally { await act(async () => root.unmount()); host.remove() }
})

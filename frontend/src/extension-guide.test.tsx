import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { it, expect, vi } from 'vitest'
import { ExtensionGuide } from './extension-guide'
vi.mock('./api', () => ({ api: vi.fn() }))
import { api } from './api'

it.each([true, false])(
  'copies installable script source with manual fallback when clipboard success is %s',
  async (copied) => {
    const host = document.createElement('div')
    document.body.append(host)
    const root = createRoot(host)
    vi.mocked(api)
      .mockReset()
      .mockResolvedValue({ source: '// ==UserScript==\n// synthetic source' } as never)
    const writeText = copied
      ? vi.fn().mockResolvedValue(undefined)
      : vi.fn().mockRejectedValue(Error('denied'))
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    try {
      await act(async () => root.render(<ExtensionGuide />))
      expect(host.textContent).toContain('HttpOnly')
      expect(
        host.querySelector('a[href="https://www.tampermonkey.net/?browser=firefox"]')?.textContent,
      ).toContain('Firefox Beta')
      expect(host.querySelector('a[href="https://codeforces.com/settings/general"]')).not.toBeNull()
      expect(host.textContent).toContain('只有一个管理器')
      expect(host.textContent).toContain('Firefox 容器标签页、隐私窗口和首方隔离不支持脚本复制')
      expect(host.querySelectorAll('a[href$=".zip"]')).toHaveLength(0)
      expect(api).not.toHaveBeenCalled()
      await act(async () => host.querySelector<HTMLButtonElement>('button')!.click())
      expect(api).toHaveBeenCalledWith('/browser-userscript')
      expect(writeText).toHaveBeenCalledWith('// ==UserScript==\n// synthetic source')
      expect(host.querySelector<HTMLTextAreaElement>('textarea')?.value).toContain(
        'synthetic source',
      )
      expect(host.querySelector('[role="status"]')?.textContent).toContain(
        copied ? '脚本源码已复制' : '浏览器未允许自动复制',
      )
    } finally {
      await act(async () => root.unmount())
      host.remove()
    }
  },
)

it('shows a failed source request explicitly without pretending the script was copied', async () => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  vi.mocked(api).mockReset().mockRejectedValue(Error('需要重新登录'))
  try {
    await act(async () => root.render(<ExtensionGuide />))
    await act(async () => host.querySelector<HTMLButtonElement>('button')!.click())
    expect(host.querySelector('[role="alert"]')?.textContent).toBe('需要重新登录')
    expect(host.querySelector('textarea')).toBeNull()
    expect(host.querySelector<HTMLButtonElement>('button')?.disabled).toBe(false)
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
})

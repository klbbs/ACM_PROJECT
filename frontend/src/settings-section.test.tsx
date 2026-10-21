import { act, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { SettingsSection, revealSettingsSection } from './settings-section'

const roots: ReturnType<typeof createRoot>[] = []
afterEach(() => {
  act(() => roots.splice(0).forEach((root) => root.unmount()))
  document.body.innerHTML = ''
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})
function render(element: React.ReactNode) {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  roots.push(root)
  act(() => root.render(element))
  return { host, root }
}

it('collapses a configuration without unmounting or clearing its unsaved input', () => {
  function Draft() {
    const [value, setValue] = useState('unsaved draft')
    return <input aria-label="draft" value={value} onChange={(e) => setValue(e.target.value)} />
  }
  const { host } = render(
    <SettingsSection id="connection" title="平台连接" description="登录与同步" defaultOpen>
      <Draft />
    </SettingsSection>,
  )
  const disclosure = host.querySelector('details')!
  const input = host.querySelector('input')!
  expect(disclosure.open).toBe(true)
  act(() => {
    disclosure.open = false
    disclosure.dispatchEvent(new Event('toggle'))
  })
  expect(host.querySelector('input')).toBe(input)
  act(() => {
    disclosure.open = true
    disclosure.dispatchEvent(new Event('toggle'))
  })
  expect(input.value).toBe('unsaved draft')
})

it('shortcut opens all containing disclosures before scrolling and respects reduced motion', () => {
  vi.stubGlobal('requestAnimationFrame', (fn: FrameRequestCallback) => {
    fn(0)
    return 1
  })
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({ matches: true })),
  )
  const scroll = vi.fn()
  const { host, root } = render(
    <SettingsSection id="platform" title="平台连接" description="登录与同步">
      <details id="manual">
        <summary>手动配置</summary>
        <input defaultValue="draft" />
      </details>
    </SettingsSection>,
  )
  const manual = host.querySelector('#manual') as HTMLDetailsElement
  manual.scrollIntoView = scroll
  act(() => revealSettingsSection('manual'))
  expect(manual.open).toBe(true)
  expect(host.querySelector('details')!.open).toBe(true)
  expect(scroll).toHaveBeenCalledWith({ block: 'start', behavior: 'instant' })
  act(() =>
    root.render(
      <SettingsSection id="platform" title="平台连接" description="updated">
        <details id="manual">
          <summary>手动配置</summary>
          <input defaultValue="draft" />
        </details>
      </SettingsSection>,
    ),
  )
  expect(host.querySelector('details')!.open).toBe(true)
})

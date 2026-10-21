import { act, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { RetainedPage, usePageMotion } from './page-motion'

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

it('mounts settings on first visit and retains the same draft through feature switches', () => {
  const mount = vi.fn(),
    unmount = vi.fn()
  function Draft() {
    useEffect(() => {
      mount()
      return unmount
    }, [])
    return <input type="password" defaultValue="synthetic-private-draft" />
  }
  const { host, root } = render(
    <RetainedPage active={false}>
      <Draft />
    </RetainedPage>,
  )
  expect(mount).not.toHaveBeenCalled()
  act(() =>
    root.render(
      <RetainedPage active>
        <Draft />
      </RetainedPage>,
    ),
  )
  const input = host.querySelector('input')!
  act(() =>
    root.render(
      <RetainedPage active={false}>
        <Draft />
      </RetainedPage>,
    ),
  )
  expect(host.firstElementChild!.hasAttribute('hidden')).toBe(true)
  expect(unmount).not.toHaveBeenCalled()
  act(() =>
    root.render(
      <RetainedPage active>
        <Draft />
      </RetainedPage>,
    ),
  )
  expect(host.querySelector('input')).toBe(input)
  expect(input.value).toBe('synthetic-private-draft')
  expect(mount).toHaveBeenCalledOnce()
})

it('cancels the previous motion on rapid navigation without replacing the surface', () => {
  const cancel = vi.fn(),
    animate = vi.fn(() => ({ cancel }))
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({ matches: false })),
  )
  Object.defineProperty(HTMLElement.prototype, 'animate', { configurable: true, value: animate })
  function Surface({ page }: { page: string }) {
    return (
      <div ref={usePageMotion(page)}>
        <input defaultValue="draft" />
      </div>
    )
  }
  const { host, root } = render(<Surface page="home" />)
  const input = host.querySelector('input')
  act(() => root.render(<Surface page="settings" />))
  expect(animate).toHaveBeenCalledTimes(2)
  expect(cancel).toHaveBeenCalledOnce()
  expect(host.querySelector('input')).toBe(input)
  delete (HTMLElement.prototype as any).animate
})

it('does not start page animation when reduced motion is requested', () => {
  const animate = vi.fn()
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({ matches: true })),
  )
  Object.defineProperty(HTMLElement.prototype, 'animate', { configurable: true, value: animate })
  function Surface() {
    return <div ref={usePageMotion('settings')} />
  }
  render(<Surface />)
  expect(animate).not.toHaveBeenCalled()
  delete (HTMLElement.prototype as any).animate
})

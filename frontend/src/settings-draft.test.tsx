import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it } from 'vitest'
import { useSettingsDraft } from './settings-draft'

const roots: ReturnType<typeof createRoot>[] = []
afterEach(() => {
  act(() => roots.splice(0).forEach((root) => root.unmount()))
  document.body.innerHTML = ''
})
function mount() {
  let draft!: ReturnType<typeof useSettingsDraft<{ url: string; model: string }>>
  function Harness() {
    draft = useSettingsDraft({ url: 'saved-address', model: 'saved-model' })
    return <output>{JSON.stringify(draft.value)}</output>
  }
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  roots.push(root)
  act(() => root.render(<Harness />))
  return { current: () => draft }
}

it('preserves an edited endpoint and model against a background reload', () => {
  const { current } = mount()
  const request = current().beginRefresh()
  act(() => current().setValue({ url: 'new-provider', model: 'new-model' }))
  act(() => {
    expect(current().refresh({ url: 'saved-address', model: 'saved-model' }, request)).toBe(false)
  })
  expect(current().value).toEqual({ url: 'new-provider', model: 'new-model' })
})

it('typing only a new key also protects its matching endpoint from a background refresh', () => {
  const { current } = mount()
  const request = current().beginRefresh()
  act(() => current().markDirty())
  act(() => {
    expect(current().refresh({ url: 'another-address', model: 'another-model' }, request)).toBe(
      false,
    )
  })
  expect(current().value.url).toBe('saved-address')
})

it('a successful save clears dirty state and invalidates older in-flight reads', () => {
  const { current } = mount()
  act(() => current().setValue({ url: 'new-provider', model: 'new-model' }))
  const oldRead = current().beginRefresh()
  const savedVersion = current().getVersion()
  act(() => {
    expect(current().commit({ url: 'new-provider', model: 'saved-new-model' }, savedVersion)).toBe(
      true,
    )
  })
  act(() => {
    expect(current().refresh({ url: 'old-address', model: 'old-model' }, oldRead)).toBe(false)
  })
  const freshRead = current().beginRefresh()
  act(() => {
    expect(current().refresh({ url: 'new-provider', model: 'new-server-model' }, freshRead)).toBe(
      true,
    )
  })
  expect(current().value.model).toBe('new-server-model')
})

it('an older save cannot overwrite or mark newer edits clean', () => {
  const { current } = mount()
  act(() => current().setValue({ url: 'first-address', model: 'first-model' }))
  const savedVersion = current().getVersion()
  act(() => current().setValue({ url: 'newer-address', model: 'newer-model' }))
  act(() => {
    expect(current().commit({ url: 'first-address', model: 'first-model' }, savedVersion)).toBe(
      false,
    )
  })
  const request = current().beginRefresh()
  act(() => {
    expect(current().refresh({ url: 'first-address', model: 'first-model' }, request)).toBe(false)
  })
  expect(current().value.url).toBe('newer-address')
})

it('applies only the newest background request when responses arrive out of order', () => {
  const { current } = mount()
  const older = current().beginRefresh(),
    newer = current().beginRefresh()
  act(() => current().refresh({ url: 'newest-address', model: 'newest-model' }, newer))
  act(() => {
    expect(current().refresh({ url: 'old-address', model: 'old-model' }, older)).toBe(false)
  })
  expect(current().value.url).toBe('newest-address')
})

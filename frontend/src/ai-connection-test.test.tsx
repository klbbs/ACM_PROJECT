import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AIConnectionTest } from './ai-connection-test'

let host: HTMLDivElement, root: Root
const save = vi.fn(), run = vi.fn(), onError = vi.fn()
const render = async (disabled = false) => act(async () => {
  root.render(<AIConnectionTest disabled={disabled} save={save} run={run} onError={onError} />)
})
beforeEach(() => {
  save.mockReset().mockResolvedValue(true); run.mockReset().mockResolvedValue({}); onError.mockReset()
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(async () => root.unmount()); host.remove() })
it('never tests an old configuration if saving the current form is rejected', async () => {
  save.mockResolvedValue(false)
  await render()
  await act(async () => host.querySelector('button')!.click())
  expect(save).toHaveBeenCalledOnce()
  expect(run).not.toHaveBeenCalled()
})
it('waits for saving before testing and prevents duplicate clicks', async () => {
  let resolve!: (value: boolean) => void
  save.mockImplementation(() => new Promise<boolean>(done => { resolve = done }))
  await render()
  await act(async () => { host.querySelector('button')!.click(); host.querySelector('button')!.click() })
  expect(save).toHaveBeenCalledOnce()
  expect(run).not.toHaveBeenCalled()
  await act(async () => resolve(true))
  expect(run).toHaveBeenCalledOnce()
})
it('shows request errors and allows an explicit retry', async () => {
  run.mockRejectedValueOnce(new Error('网络连接失败'))
  await render()
  await act(async () => host.querySelector('button')!.click())
  expect(onError).toHaveBeenCalledWith('网络连接失败')
  await act(async () => host.querySelector('button')!.click())
  expect(run).toHaveBeenCalledTimes(2)
})
it('does not send anything when missing a model or another operation disables testing', async () => {
  await render(true)
  await act(async () => host.querySelector('button')!.click())
  expect(save).not.toHaveBeenCalled()
  expect(run).not.toHaveBeenCalled()
})

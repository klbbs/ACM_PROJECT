import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { StatementSamples } from './statement-samples'

it('copies each sample independently with its original whitespace', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText}})
  const host = document.createElement('div')
  const root = createRoot(host)
  await act(async () => root.render(<StatementSamples samples={[
    {input:'2\n  1 2\n\n',output:'3\n'}, {input:'0', output:''},
  ]} />))
  const buttons = host.querySelectorAll('button')
  expect(buttons).toHaveLength(4)
  for (let index = 0; index < 4; index++) {
    await act(async () => buttons[index].click())
  }
  expect(writeText.mock.calls.map(call => call[0])).toEqual(['2\n  1 2\n\n','3\n','0',''])
  expect(buttons[0].getAttribute('aria-label')).toBe('复制样例输入 1')
  expect(buttons[1].getAttribute('aria-label')).toBe('复制样例输出 1')
  expect(host.querySelector('pre')?.textContent).toBe('2\n  1 2\n\n')
  await act(async () => root.unmount())
})

it('shows a retryable failure and resets feedback when the sample changes', async () => {
  const writeText = vi.fn().mockRejectedValueOnce(new Error('denied')).mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText}})
  const host = document.createElement('div')
  const root = createRoot(host)
  await act(async () => root.render(<StatementSamples samples={[{input:'old',output:'0'}]} />))
  await act(async () => host.querySelector('button')!.click())
  expect(host.textContent).toContain('复制失败')
  await act(async () => host.querySelector('button')!.click())
  expect(host.textContent).toContain('样例输入 1已复制')
  await act(async () => root.render(<StatementSamples samples={[{input:'new',output:'0'}]} />))
  expect(host.textContent).not.toContain('已复制')
  await act(async () => root.unmount())
})

it('supports HTTP clipboard fallback and removes the temporary field', async () => {
  Object.defineProperty(navigator, 'clipboard', {configurable:true, value:undefined})
  const original = Object.getOwnPropertyDescriptor(document, 'execCommand')
  const copy = vi.fn(() => {
    expect((document.activeElement as HTMLTextAreaElement).value).toBe('1\n2\n')
    return true
  })
  Object.defineProperty(document, 'execCommand', {configurable:true, value:copy})
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  try {
    await act(async () => root.render(<StatementSamples samples={[{input:'1\n2\n',output:'3'}]} />))
    const button = host.querySelector('button')!
    button.focus()
    await act(async () => button.click())
    expect(copy).toHaveBeenCalledWith('copy')
    expect(document.querySelector('textarea')).toBeNull()
    expect(document.activeElement).toBe(button)
    expect(button.textContent).toContain('已复制')
  } finally {
    await act(async () => root.unmount())
    host.remove()
    if (original) Object.defineProperty(document, 'execCommand', original)
    else delete (document as unknown as Record<string,unknown>).execCommand
  }
})

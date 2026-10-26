import { afterEach, expect, it, vi } from 'vitest'
import { createRoot } from 'react-dom/client'
import { act } from 'react'
import { CfApiFields, cfApiPayload, cfApiValid } from './cf-api-fields'

afterEach(() => {
  document.body.innerHTML = ''
})

it('keeps saved credentials empty and explains the source-only scope', () => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  const changed = vi.fn()
  act(() =>
    root.render(<CfApiFields value={{ has_api_key: true }} disabled={false} onChange={changed} />),
  )
  const inputs = [...host.querySelectorAll('input')]
  expect(inputs).toHaveLength(2)
  expect(inputs.every((input) => input.value === '' && input.type === 'password')).toBe(true)
  expect(inputs[0].placeholder).toContain('已保存')
  expect(inputs.every((input) => input.maxLength === 40)).toBe(true)
  expect(host.textContent).toContain('不代替交题所需的 Cookie')
  expect(host.querySelector('a')?.href).toBe('https://codeforces.com/settings/api')
  act(() => host.querySelector('button')!.click())
  expect(changed).toHaveBeenCalledWith({
    has_api_key: true,
    api_key: '',
    api_secret: '',
    clearApi: true,
  })
  act(() => root.unmount())
})

it('preserves an untouched pair and explicitly clears both fields together', () => {
  expect(cfApiPayload({ has_api_key: true })).toEqual({})
  expect(cfApiPayload({ clearApi: true })).toEqual({ api_key: '', api_secret: '' })
  expect(cfApiPayload({ api_key: 'a'.repeat(32), api_secret: 'b'.repeat(32) })).toEqual({
    api_key: 'a'.repeat(32),
    api_secret: 'b'.repeat(32),
  })
  expect(cfApiValid({})).toBe(true)
  expect(cfApiValid({ api_key: 'a'.repeat(32) })).toBe(false)
  expect(cfApiValid({ api_key: 'x'.repeat(32), api_secret: 'b'.repeat(32) })).toBe(false)
  expect(cfApiValid({ api_key: 'A'.repeat(32), api_secret: 'b'.repeat(32) })).toBe(true)
  expect(cfApiValid({ api_key: 'A'.repeat(40), api_secret: 'b'.repeat(40) })).toBe(true)
  expect(cfApiValid({ api_key: 'A'.repeat(39), api_secret: 'b'.repeat(40) })).toBe(false)
  expect(cfApiValid({ api_key: 'A'.repeat(41), api_secret: 'b'.repeat(40) })).toBe(false)
})

import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { expect, it } from 'vitest'
import { statementKatexAlias } from './statement-katex-config'
import { normalizeMathRelations } from './statement-math'

it('uses the actual renderer stylesheet so the not-equal slash is overlaid instead of printed beside equals', () => {
  const require = createRequire(import.meta.url)
  const renderer = createRequire(require.resolve('rehype-katex'))
  const expectedCss = renderer.resolve('katex/dist/katex.min.css')
  const css = statementKatexAlias['katex/dist/katex.min.css']
  expect(css).toBe(expectedCss)
  const katex = renderer('katex') as { renderToString: (value: string) => string }
  const style = document.createElement('style')
  const host = document.createElement('div')
  style.textContent = readFileSync(css, 'utf8')
  host.innerHTML = katex.renderToString(String.raw`a \neq \frac{b}{c}`)
  document.head.append(style)
  document.body.append(host)
  try {
    const overlay = host.querySelector('.rlap > .inner')!
    expect(overlay).not.toBeNull()
    expect(getComputedStyle(overlay).position).toBe('absolute')
    expect(getComputedStyle(host.querySelector('.vlist')!).position).toBe('relative')
  } finally {
    host.remove()
    style.remove()
  }
})

it('normalizes only split Unicode negation and leaves official TeX commands intact', () => {
  const original = String.raw`a\neq b; c\ne d; e\not= f; \nearrow; \neqx`
  expect(normalizeMathRelations(original)).toBe(original)
  expect(normalizeMathRelations('a=\u0338b; c\u0338=d')).toBe('a≠b; c≠d')
})

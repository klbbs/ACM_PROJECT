// A combining overlay can arrive from adjacent MathML text nodes. Normalize
// just that equivalent spelling; the official \ne/\neq/\not= TeX stays intact.
export function normalizeMathRelations(value: string) {
  return value.replace(/=\s*\u0338|\u0338\s*=/g, '≠')
}

export function remarkStatementRelations() {
  return (tree: unknown) => {
    const visit = (value: unknown) => {
      if (!value || typeof value !== 'object') return
      const node = value as {
        type?: string
        value?: string
        children?: unknown[]
        data?: { hChildren?: unknown[] }
      }
      if ((node.type === 'math' || node.type === 'inlineMath') && typeof node.value === 'string') {
        node.value = normalizeMathRelations(node.value)
        // remark-math also stores the original text on the hast bridge;
        // changing only mdast.value leaves KaTeX rendering the old overlay.
        if (node.data && Array.isArray(node.data.hChildren)) {
          node.data.hChildren = [{ type: 'text', value: node.value }]
        }
      }
      if (Array.isArray(node.children)) node.children.forEach(visit)
    }
    visit(tree)
  }
}

const overlay = (value: string) => value.replace(/=\s*\u0338|\u0338\s*=/g, '≠').normalize('NFC')

function mathml(node: Element): string {
  const name = node.localName.toLowerCase()
  if (name === 'annotation' || name === 'annotation-xml') return ''
  const children = Array.from(node.children)
  const parts = children.map(mathml)
  if (name === 'semantics') return parts[0] || ''
  if (['mi', 'mn', 'mo', 'mtext', 'ms'].includes(name)) return overlay(node.textContent || '')
  if (['msup', 'msub'].includes(name) && parts.length === 2) {
    return `{${parts[0]}}${name === 'msup' ? '^' : '_'}{${parts[1]}}`
  }
  if (name === 'msubsup' && parts.length === 3) return `{${parts[0]}}_{${parts[1]}}^{${parts[2]}}`
  if (name === 'mfrac' && parts.length === 2) return `\\frac{${parts[0]}}{${parts[1]}}`
  if (name === 'msqrt') return `\\sqrt{${parts.join('')}}`
  if (name === 'mroot' && parts.length === 2) return `\\sqrt[${parts[1]}]{${parts[0]}}`
  if (
    name === 'menclose' &&
    node.getAttribute('notation')?.split(/\s+/).includes('updiagonalstrike') &&
    parts.join('').trim() === '='
  )
    return String.raw`\neq `
  return Array.from(node.childNodes, (child) =>
    child.nodeType === Node.ELEMENT_NODE ? mathml(child as Element) : child.textContent || '',
  ).join('')
}

/** Used only by the legacy HTML-to-Markdown fallback, before visual duplicates are flattened. */
export function renderedMathSource(node: Element): { tex: string; block: boolean } | null {
  if (node.closest('pre,code')) return null
  const kind = (node.getAttribute('type') || '').toLowerCase().replace(/\s/g, '')
  if (node.localName === 'script' && ['math/tex', 'math/tex;mode=display'].includes(kind)) {
    return { tex: node.textContent || '', block: kind.includes('mode=display') }
  }
  if (
    node.localName !== 'math' &&
    !['katex', 'katex-display', 'MathJax', 'MathJax_Display'].some((name) =>
      node.classList.contains(name),
    )
  )
    return null
  const annotation = Array.from(node.querySelectorAll('annotation')).find((element) =>
    ['application/x-tex', 'application/x-latex', 'text/x-tex', 'application/tex'].includes(
      (element.getAttribute('encoding') || '').toLowerCase(),
    ),
  )
  const math = node.localName === 'math' ? node : node.querySelector('math')
  const tex = annotation ? annotation.textContent || '' : math ? overlay(mathml(math)) : ''
  if (!tex.trim()) return null
  return {
    tex,
    block:
      node.getAttribute('display') === 'block' ||
      node.classList.contains('katex-display') ||
      node.classList.contains('MathJax_Display'),
  }
}

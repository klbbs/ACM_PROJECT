import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it } from 'vitest'
import { StatementView, statementMarkdown } from './statement-view'
it('renders Markdown, TeX, structured HTML and image fallback without running raw HTML', async () => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  await act(async () =>
    root.render(
      <StatementView
        statement={{
          sections: ['## Heading\n\n**bold**\n\n- first\n\n$x^2$\n\n<script>evil()</script>'],
          samples: [],
          images: [],
        }}
      />,
    ),
  )
  expect(host.querySelector('h2')?.textContent).toBe('Heading')
  expect(host.querySelector('strong')?.textContent).toBe('bold')
  expect(host.querySelector('.katex')).not.toBeNull()
  expect(host.querySelector('script')).toBeNull()
  await act(async () =>
    root.render(
      <StatementView
        statement={{
          body_html:
            '<p>Let <span>$$$x_i^2$$$</span>.</p><img src="https://codeforces.com/a.png"><ul><li>one</li></ul>',
          sections: [],
          samples: [],
          images: [{}],
        }}
      />,
    ),
  )
  expect(host.querySelector('.katex')).not.toBeNull()
  expect(host.querySelector('li')?.textContent).toBe('one')
  const img = host.querySelector('img')!
  await act(async () => img.dispatchEvent(new Event('error')))
  expect(host.textContent).toContain('图片加载失败')
  expect(host.querySelector('.statement-image a')?.getAttribute('href')).toBe(
    'https://codeforces.com/a.png',
  )
  await act(async () => root.unmount())
  host.remove()
})

it('renders server Markdown directly with tables, code, images and math instead of stale HTML', async () => {
  const statement = {
    id: 'codeforces:4-A',
    render_version: 4,
    markdown:
      '## 输入\n\n给定 $x_i^2$。\n\n- 第一项\n- 第二项\n\n| A | B |\n| --- | --- |\n| x | 2 |\n\n```cpp\nif (x < y) {\n\n    a_b();\n}\n```\n\n![图](<https://codeforces.com/a.png>)',
    body_html: '<p>STALE ORIGINAL</p>',
    sections: ['STALE'],
    images: [{}],
    samples: [],
  }
  expect(statementMarkdown(statement)).toBe(statement.markdown)
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  await act(async () => root.render(<StatementView statement={statement} />))
  expect(host.querySelector('h2')?.textContent).toBe('输入')
  expect(host.querySelectorAll('li')).toHaveLength(2)
  expect(host.querySelector('table td')?.textContent).toBe('x')
  expect(host.querySelector('.katex')).not.toBeNull()
  expect(host.querySelector('pre code')?.textContent).toContain('if (x < y) {\n\n    a_b();')
  expect(host.querySelector('img')?.getAttribute('src')).toBe('https://codeforces.com/a.png')
  expect(host.textContent).not.toContain('STALE')
  expect(host.textContent).not.toContain('## 输入')
  await act(async () => root.unmount())
  host.remove()
})

it('preserves Codeforces teletype operators and literal != inside TeX', async () => {
  expect(
    statementMarkdown({
      body_html: '<p><span class="tex-font-style-tt">a != b</span> and $$$a != b$$$.</p>',
    }),
  ).toContain('`a != b` and $a \\texttt{!=} b$.')

  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  await act(async () =>
    root.render(
      <StatementView statement={{ markdown: 'Code: `a != b` and formula $a \\texttt{!=} b$.' }} />,
    ),
  )
  expect(host.querySelector('code')?.textContent).toBe('a != b')
  expect(host.querySelector('.katex .texttt')?.textContent).toBe('!=')
  await act(async () => root.unmount())
  host.remove()
})

it('asks to refresh plain CF caches even when an old render version is set', async () => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  await act(async () =>
    root.render(
      <StatementView
        statement={{
          id: 'codeforces:4-A',
          render_version: 2,
          sections: ['Plain cached text'],
          has_images: true,
        }}
      />,
    ),
  )
  expect(host.textContent).toContain('旧版纯文本缓存；请刷新题面')
  expect(host.textContent).toContain('旧缓存未保存图片地址')
  expect(host.textContent).not.toContain('正文使用 Markdown 渲染，支持列表')
  await act(async () =>
    root.render(
      <StatementView
        statement={{
          id: 'luogu:P1001',
          render_version: 2,
          sections: ['## 题目描述\n\n原生 Markdown'],
        }}
      />,
    ),
  )
  expect(host.textContent).not.toContain('旧版纯文本缓存')
  expect(host.querySelector('h2')?.textContent).toBe('题目描述')
  await act(async () => root.unmount())
  host.remove()
})

it('preserves not-equal semantics for TeX aliases and Unicode overlays without changing code', async () => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  const expressions = [
    String.raw`a\neq b`,
    String.raw`a\ne b`,
    String.raw`a\not= b`,
    'a≠b',
    'a=\u0338b',
  ]
  for (const expression of expressions) {
    await act(async () =>
      root.render(
        <StatementView
          statement={{
            markdown: `Formula $${expression}$; prose ≠; code \`a != b; \\neq\`.`,
          }}
        />,
      ),
    )
    expect(host.querySelector('.katex-mathml mo')?.textContent?.normalize('NFC')).toBe('≠')
    expect(host.querySelector('.katex-error')).toBeNull()
    expect(host.querySelector('code')?.textContent).toBe('a != b; \\neq')
    expect(host.textContent).toContain('prose ≠')
  }
  await act(async () => root.unmount())
  host.remove()
})

it('recovers the TeX annotation once from legacy rendered math instead of visual duplicates', async () => {
  const body = String.raw`<p>Given <span class="katex"><span class="katex-mathml"><math><semantics><mrow><mi>a</mi><mo>≠</mo><mi>b</mi></mrow><annotation encoding="application/x-tex">a \ne b</annotation></semantics></math></span><span class="katex-html">VISUAL DUPLICATE</span></span>.</p>`
  const markdown = statementMarkdown({ body_html: body })
  expect(markdown).toBe(String.raw`Given $a \ne b$.`)
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  await act(async () => root.render(<StatementView statement={{ body_html: body }} />))
  expect(host.querySelectorAll('.katex')).toHaveLength(1)
  expect(host.querySelector('.katex-mathml mo')?.textContent).toBe('≠')
  expect(host.textContent).not.toContain('VISUAL DUPLICATE')
  await act(async () => root.unmount())
  host.remove()
})

it('keeps split MathML negation and MathJax source in the legacy HTML fallback', () => {
  const markdown = statementMarkdown({
    body_html:
      '<p><math><mrow><mi>a</mi><mo>=</mo><mo>&#x0338;</mo><mi>b</mi></mrow></math></p>' +
      String.raw`<p><script type="math/tex; mode=display">c \not= d</script></p>`,
  })
  expect(markdown).toContain('$a≠b$')
  expect(markdown).toContain('$$\nc \\not= d\n$$')
})

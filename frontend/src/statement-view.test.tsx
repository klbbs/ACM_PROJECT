import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it } from 'vitest'
import { StatementView } from './statement-view'
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

import React, { useMemo, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import TurndownService from 'turndown'
import { renderedMathSource, remarkStatementRelations } from './statement-math'
import 'katex/dist/katex.min.css'

const safeUrl = (url?: string) => {
  try {
    const u = new URL(url || '')
    return u.protocol === 'https:' && !u.username && !u.password ? u.href : ''
  } catch {
    return ''
  }
}
export function statementMarkdown(statement: Record<string, any>) {
  if (typeof statement.markdown === 'string') return statement.markdown
  if (!statement.body_html) return (statement.sections || []).join('\n\n').replace(/\$\$\$/g, '$')
  const converter = new TurndownService({ headingStyle: 'atx', codeBlockStyle: 'fenced' })
  converter.addRule('codeforcesTeletype', {
    filter: (node) =>
      node.nodeName === 'SPAN' && (node as HTMLElement).classList.contains('tex-font-style-tt'),
    replacement: (_content, node) => {
      const value = node.textContent || ''
      const fence = '`'.repeat(
        Math.max(1, ...Array.from(value.matchAll(/`+/g), (m) => m[0].length + 1)),
      )
      return value.includes('`') ? `${fence} ${value} ${fence}` : `${fence}${value}${fence}`
    },
  })
  // CF uses triple dollars for inline TeX. Keep text nodes contiguous;
  // get_text(separator="\\n") used to split every inline formula into fragments.
  converter.addRule('tex', {
    filter: (node) => node.nodeName === 'SPAN' && /\$\$\$|\\\(|\\\[/.test(node.textContent || ''),
    replacement: (_content, node) =>
      (node.textContent || '')
        .replace(/\$\$\$/g, '$')
        .replace(/\\\(/g, '$')
        .replace(/\\\)/g, '$')
        .replace(/\\\[/g, '$$')
        .replace(/\\\]/g, '$$'),
  })
  converter.addRule('image', {
    filter: 'img',
    replacement: (_content, node) => {
      const img = node as HTMLImageElement
      const url = safeUrl(img.getAttribute('src') || '')
      return url
        ? `\n\n![${(img.getAttribute('alt') || '题目配图').replace(/[\[\]]/g, '')}](<${url}>)\n\n`
        : ''
    },
  })
  converter.addRule('scripts', {
    filter: ['script', 'style', 'iframe', 'object'],
    replacement: () => '',
  })
  converter.addRule('table', {
    filter: 'table',
    replacement: (_c, node) => {
      const rows = Array.from((node as HTMLElement).querySelectorAll('tr')).map((row) =>
        Array.from(row.querySelectorAll('td,th')).map((cell) =>
          converter.turndown(cell.innerHTML).replace(/\|/g, '\\|').replace(/\n/g, ' '),
        ),
      )
      if (!rows.length) return ''
      return (
        '\n\n' +
        [rows[0], rows[0].map(() => '---'), ...rows.slice(1)]
          .map((row) => '| ' + row.join(' | ') + ' |')
          .join('\n') +
        '\n\n'
      )
    },
  })
  converter.addRule('renderedMathSource', {
    filter: (node) => renderedMathSource(node as Element) !== null,
    replacement: (_content, node) => {
      const source = renderedMathSource(node as Element)!
      return source.block ? `\n\n$$\n${source.tex}\n$$\n\n` : `$${source.tex}$`
    },
  })
  const formulas: string[] = []
  const protectedHtml = statement.body_html.replace(
    /\$\$\$([\s\S]*?)\$\$\$|\\\(([\s\S]*?)\\\)|\\\[([\s\S]*?)\\\]/g,
    (_all: string, cf: string, inline: string, block: string) => {
      const decode = document.createElement('textarea')
      decode.innerHTML = cf ?? inline ?? block
      const tex = decode.value.replace(/(?<![\\=!])!=(?![=])/g, String.raw`\texttt{!=}`)
      const token = `ACMFORMULATOKEN${formulas.length}END`
      formulas.push(block !== undefined ? `\n\n$$\n${tex}\n$$\n\n` : `$${tex}$`)
      return token
    },
  )
  const result = converter
    .turndown(protectedHtml)
    .replace(/ACMFORMULATOKEN(\d+)END/g, (_all, index) => formulas[Number(index)] ?? '')
  return result.replace(/\$\$\$/g, '$')
}
function ProblemImage({ src, alt }: { src?: string; alt?: string }) {
  const [failed, setFailed] = useState(false),
    url = safeUrl(src)
  if (!url) return <span>图片地址不可用，请查看原题。</span>
  return (
    <span className="statement-image">
      {!failed && (
        <img
          src={url}
          alt={alt || '题目配图'}
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setFailed(true)}
        />
      )}
      {failed && <span>图片加载失败，可能被平台限制。</span>}
      <a href={url} target="_blank" rel="noreferrer">
        打开图片原链接 ↗
      </a>
    </span>
  )
}
export function StatementView({ statement }: { statement: Record<string, any> }) {
  const markdown = useMemo(() => statementMarkdown(statement), [statement])
  const diagnostics = statement.diagnostics
  // Luogu supplies native Markdown sections. A CF version marker alone does
  // not mean its old cache retained headings, images or inline structure.
  const structured = Boolean(
    statement.markdown ||
    statement.body_html ||
    (statement.id?.startsWith('luogu:') && statement.render_version >= 2),
  )
  return (
    <>
      <details className="statement-check">
        <summary>
          题面检查：{statement.sections?.length || 0} 段正文 · {statement.samples?.length || 0}{' '}
          组样例 · {statement.images?.length || 0} 张图片
        </summary>
        <p>
          {structured
            ? '正文使用 Markdown 渲染，支持列表、表格、代码及数学公式。图片使用原站链接，加载失败仍可打开原链接。'
            : '这是旧版纯文本缓存；请刷新题面以恢复图片和排版。'}
        </p>
        {diagnostics?.warnings?.map((w: string) => (
          <p key={w}>{w}</p>
        ))}
      </details>
      {!structured && statement.has_images && (
        <p className="wb-warning">旧缓存未保存图片地址，请刷新题面以恢复配图。</p>
      )}
      <div className="statement-body">
        <Markdown
          remarkPlugins={[remarkGfm, remarkMath, remarkStatementRelations]}
          rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false, trust: false }]]}
          skipHtml
          components={{
            img: ({ src, alt }) => <ProblemImage src={src} alt={alt} />,
            a: ({ href, children }) => (
              <a href={safeUrl(href)} target="_blank" rel="noreferrer">
                {children}
              </a>
            ),
          }}
        >
          {markdown}
        </Markdown>
      </div>
    </>
  )
}

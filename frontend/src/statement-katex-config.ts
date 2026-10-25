import { createRequire } from 'node:module'
import { dirname } from 'node:path'

const require = createRequire(import.meta.url)

// Resolve from rehype-katex, rather than assuming npm's nested/hoisted layout.
// Its renderer and the direct app dependency can be different KaTeX versions.
export const statementKatexAlias = {
  'katex/dist/katex.min.css': require.resolve('katex/dist/katex.min.css', {
    paths: [dirname(require.resolve('rehype-katex'))],
  }),
}

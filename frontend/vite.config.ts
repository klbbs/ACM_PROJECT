import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { statementKatexAlias } from './src/statement-katex-config'
// rehype-katex can resolve a different KaTeX version from the app's direct
// dependency. Resolve its stylesheet from the renderer's location as well:
// mixing versions breaks the overlay/vlist layout of \ne and \not=.
export default defineConfig({
  plugins: [react()],
  resolve: { alias: statementKatexAlias },
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  build: { sourcemap: false },
})

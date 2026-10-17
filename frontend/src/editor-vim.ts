import { vim, Vim, getCM } from '@replit/codemirror-vim'
import type { EditorView } from '@codemirror/view'

const saves = new WeakMap<object, () => void>()
const notifications = new WeakMap<object, () => void>()
Vim.defineEx('write', 'w', (cm) => saves.get(cm)?.())
for (const command of ['quit', 'wq', 'xit'])
  Vim.defineEx(command, command === 'quit' ? 'q' : command === 'xit' ? 'x' : undefined, (cm) =>
    notifications.get(cm)?.(),
  )

export { vim, getCM }
export function bindVim(
  editor: EditorView,
  save: () => void,
  notify: () => void,
  changed: () => void,
) {
  const cm = getCM(editor)
  if (!cm) return () => {}
  saves.set(cm, save)
  notifications.set(cm, notify)
  cm.on('vim-mode-change', changed)
  return () => {
    cm.off('vim-mode-change', changed)
    saves.delete(cm)
    notifications.delete(cm)
  }
}

import { EditorSelection, countColumn } from '@codemirror/state'
import type { EditorView, KeyBinding } from '@codemirror/view'
import { indentLess, indentMore, insertNewlineAndIndent } from '@codemirror/commands'
import { indentString } from '@codemirror/language'
import {
  acceptCompletion,
  closeCompletion,
  completionStatus,
  nextSnippetField,
} from '@codemirror/autocomplete'

/**
 * Default indentation for the workbench editor. Both values are installed as
 * editor state (`EditorState.tabSize` / `indentUnit`) so the commands below
 * always follow the configured preferences instead of assuming four spaces.
 */
export const EDITOR_INDENT_UNIT = '    '
export const EDITOR_TAB_SIZE = 4

const leading = (text: string) => /^\s*/.exec(text)![0]

/**
 * Tab indentation modelled on VS Code:
 * - a selection indents whole lines,
 * - the caret inside a line's leading whitespace moves the line to the next
 *   indentation level and lands after that indentation,
 * - the caret inside the code inserts up to the next tab stop instead of
 *   shifting the whole line.
 * The current indentation unit and tab size come from the editor state.
 */
export function indentTab(view: EditorView): boolean {
  const { state } = view
  if (state.readOnly) return false
  const ranges = state.selection.ranges
  if (ranges.length > 1 || ranges.some((range) => !range.empty)) return indentMore(view)
  const range = ranges[0]
  const line = state.doc.lineAt(range.from)
  const indent = leading(line.text)
  if (range.from <= line.from + indent.length) {
    const column = countColumn(indent, state.tabSize)
    const target = indentString(state, column + state.tabSize - (column % state.tabSize))
    const changes = state.changeByRange(() => ({
      changes: { from: line.from, to: line.from + indent.length, insert: target },
      range: EditorSelection.cursor(line.from + target.length),
    }))
    view.dispatch(state.update(changes, { scrollIntoView: true, userEvent: 'input.indent' }))
    return true
  }
  const column = countColumn(line.text, state.tabSize, range.from - line.from)
  const next = state.tabSize - (column % state.tabSize)
  view.dispatch(state.replaceSelection(indentString(state, next)), {
    scrollIntoView: true,
    userEvent: 'input.indent',
  })
  return true
}

/**
 * Enter closes an open completion and still inserts an indentation-aware
 * newline, so the new line inherits the current indentation and gains a level
 * inside a scope. A plain `insertNewline` would drop both.
 */
export function newlineWithIndent(view: EditorView): boolean {
  if (!completionStatus(view.state)) return false
  closeCompletion(view)
  return insertNewlineAndIndent(view)
}

/**
 * Accept an open completion with Tab. Kept separate from `indentKeymap` because
 * it also has to run above the snippet keymap that CodeMirror installs at
 * highest precedence while a snippet's placeholders are active.
 */
export function acceptCompletionOnTab(view: EditorView): boolean {
  return completionStatus(view.state) ? acceptCompletion(view) : false
}

/**
 * Tab / Shift-Tab / Enter bindings for the editor. `canIndent` lets the caller
 * keep Vim normal and visual mode keys untouched, and completions are still
 * accepted with Tab before anything is indented.
 */
export function indentKeymap(canIndent: (view: EditorView) => boolean = () => true): KeyBinding[] {
  return [
    {
      key: 'Tab',
      run: (view) =>
        acceptCompletionOnTab(view) ||
        (canIndent(view) ? nextSnippetField(view) || indentTab(view) : true),
      shift: (view) => (canIndent(view) ? indentLess(view) : true),
    },
    { key: 'Enter', run: newlineWithIndent },
  ]
}

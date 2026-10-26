import { EditorSelection, countColumn } from '@codemirror/state'
import type { EditorView, KeyBinding } from '@codemirror/view'
import { indentLess, indentMore, insertNewlineAndIndent } from '@codemirror/commands'
import { ensureSyntaxTree, indentString, language } from '@codemirror/language'
import {
  acceptCompletion,
  closeCompletion,
  completionStatus,
  nextSnippetField,
} from '@codemirror/autocomplete'

/**
 * Fixed workbench indentation: a new level is four spaces and an existing tab
 * displays as four columns. Existing documents are never rewritten on load.
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
 * One Enter path regardless of completion state. Keep a deliberately indented
 * empty line stable; on code lines let the language decide block/continuation
 * indentation and split bracket pairs. Only newly inserted indentation is
 * normalized to spaces, not the pasted document or previous lines.
 */
export function newlineWithIndent(view: EditorView): boolean {
  if (view.state.readOnly) return false
  if (completionStatus(view.state)) closeCompletion(view)
  const { state } = view
  if (state.selection.ranges.every((range) => range.empty && !/\S/.test(state.doc.lineAt(range.from).text))) {
    const changes = state.changeByRange((range) => {
      const line = state.doc.lineAt(range.from)
      const column = countColumn(line.text.slice(0, range.from - line.from), state.tabSize)
      const indentation = indentString(state, column)
      return {
        changes: { from: range.from, to: line.to, insert: state.lineBreak + indentation },
        range: EditorSelection.cursor(range.from + 1 + indentation.length),
      }
    })
    view.dispatch(state.update(changes, { scrollIntoView: true, userEvent: 'input' }))
    return true
  }
  // Catch the parser up after a paste without parsing the whole document or
  // introducing a second formatting transaction into undo history.
  ensureSyntaxTree(state, state.selection.main.head, 30)
  const languageName = state.facet(language)?.name
  if ((languageName === 'cpp' || languageName === 'java') && state.selection.ranges.every((range) => {
    if (!range.empty) return false
    const line = state.doc.lineAt(range.from)
    // While a bare identifier is incomplete, the C++ parser can lose the whole
    // surrounding function and suggest column zero. Keep the user's current
    // level in this narrow case; braces, labels, continuations and Python's
    // intentional block dedentation still use the language indentation engine.
    return /^[\t ]+(?:[\p{L}_$][\p{L}\p{N}_$]*::)*[\p{L}_$][\p{L}\p{N}_$]*$/u.test(
      line.text.slice(0, range.from - line.from),
    ) && !/\S/.test(line.text.slice(range.from - line.from))
  })) {
    const changes = state.changeByRange((range) => {
      const line = state.doc.lineAt(range.from)
      const indentation = indentString(state, countColumn(leading(line.text), state.tabSize))
      return {
        changes: { from: range.from, to: line.to, insert: state.lineBreak + indentation },
        range: EditorSelection.cursor(range.from + 1 + indentation.length),
      }
    })
    view.dispatch(state.update(changes, { scrollIntoView: true, userEvent: 'input' }))
    return true
  }
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
    {
      key: 'Enter',
      run: (view) => canIndent(view) ? newlineWithIndent(view) : false,
      shift: (view) => canIndent(view) ? newlineWithIndent(view) : false,
    },
  ]
}

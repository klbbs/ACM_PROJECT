import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EditorView } from '@codemirror/view'
import { EditorState } from '@codemirror/state'
import { undo, redo } from '@codemirror/commands'
import { indentUnit, language } from '@codemirror/language'
import { CompletionContext, completionStatus, startCompletion } from '@codemirror/autocomplete'
import { Vim } from '@replit/codemirror-vim'
import CodeEditor from './code-editor'
import { EditorBoundary } from './editor-shell'
import { localCompletion } from './editor-completions'
import { indentTab } from './editor-indentation'
import { codeBytes, defaultEditorSettings, type CodeEditorProps } from './editor-settings'
import type { CodeTemplate } from './template-types'

const personalTemplate: CodeTemplate = {
  id: 'template-one',
  title: '我的循环',
  trigger: '循环',
  language: 'cpp',
  body: 'for (int ${i} = 0; ${i} < ${n}; ++${i}) {\n    ${}\n}',
}

let host: HTMLDivElement, root: Root, props: CodeEditorProps
beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  props = {
    documentId: '4-A',
    value: 'int number;',
    language: 'cpp17',
    settings: defaultEditorSettings,
    readOnly: false,
    onChange: vi.fn(),
    onSave: vi.fn().mockResolvedValue(true),
    onError: vi.fn(),
  }
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})
const render = async (changes: Partial<CodeEditorProps> = {}) => {
  props = { ...props, ...changes }
  await act(async () => {
    root.render(<CodeEditor key={props.documentId} {...props} />)
  })
  return EditorView.findFromDOM(host.querySelector('.cm-editor')!)!
}

describe('local completions', () => {
  it('matches Chinese custom triggers and applies language filtering and personal precedence', () => {
    const state = EditorState.create({ doc: '循' })
    const custom = localCompletion('cpp20', () => true, [personalTemplate])(
      new CompletionContext(state, 1, false),
    )!
    expect(custom.from).toBe(0)
    expect(custom.options.find((c) => c.label === '循环')?.detail).toBe('我的模板 · 我的循环')
    const python = localCompletion('python3', () => true, [personalTemplate])(
      new CompletionContext(state, 1, true),
    )!
    expect(python.options.some((c) => c.label === '循环')).toBe(false)
    const ownMain = { ...personalTemplate, trigger: 'main' }
    const repeated = localCompletion('cpp17', () => true, [ownMain])(
      new CompletionContext(EditorState.create({ doc: 'main' }), 4, true),
    )!
    expect(repeated.options.filter((c) => c.label === 'main')).toHaveLength(1)
    expect(repeated.options.find((c) => c.label === 'main')?.detail).toContain('我的模板')
  })
  it.each(['cpp17', 'cpp20', 'python3', 'java17'])(
    'offers keywords, snippets and current-document words for %s',
    (language) => {
      const state = EditorState.create({ doc: 'customIdentifier\nfo' })
      const result = localCompletion(language)(
        new CompletionContext(state, state.doc.length, true),
      )!
      expect(result.options.some((c) => c.label === 'for' && c.detail === '语言关键字')).toBe(true)
      expect(result.options.some((c) => c.label === 'for-loop' && c.type === 'snippet')).toBe(true)
      expect(
        result.options.some((c) => c.label === 'customIdentifier' && c.detail === '当前文件单词'),
      ).toBe(true)
    },
  )
  it('does not return candidates in Vim normal mode', () => {
    const state = EditorState.create({ doc: 'fo' })
    expect(localCompletion('cpp17', () => false)(new CompletionContext(state, 2, true))).toBeNull()
  })
})

it('updates private templates without replacing the view or losing undo and accepts them with Tab', async () => {
  const view = await render({ value: '循' })
  await act(async () => view.dispatch({ selection: { anchor: 1 } }))
  expect(await render({ templates: [personalTemplate] })).toBe(view)
  expect(view.state.selection.main.head).toBe(1)
  await act(async () => {
    view.focus()
    expect(startCompletion(view)).toBe(true)
    await vi.waitFor(() => expect(completionStatus(view.state)).toBe('active'))
    view.contentDOM.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }),
    )
  })
  expect(view.state.doc.toString()).toContain('for (int i = 0; i < n; ++i)')
  expect(
    view.state.doc.sliceString(view.state.selection.main.from, view.state.selection.main.to),
  ).toBe('i')
  await act(async () =>
    view.contentDOM.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }),
    ),
  )
  expect(
    view.state.doc.sliceString(view.state.selection.main.from, view.state.selection.main.to),
  ).toBe('n')
  await act(async () => {
    expect(undo(view)).toBe(true)
  })
  expect(view.state.doc.toString()).toBe('循')
})

it('retains editor identity, selection and undo while preferences and language change', async () => {
  const view = await render()
  await act(async () =>
    view.dispatch({ changes: { from: 0, insert: '// edit\n' }, selection: { anchor: 8 } }),
  )
  expect(props.onChange).toHaveBeenCalledWith('// edit\nint number;')
  const again = await render({
    settings: { ...defaultEditorSettings, font_size: 20, line_wrapping: true },
    language: 'python3',
  })
  expect(again).toBe(view)
  expect(view.state.selection.main.head).toBe(8)
  await act(async () => {
    expect(undo(view)).toBe(true)
  })
  expect(view.state.doc.toString()).toBe('int number;')
  await act(async () => {
    expect(redo(view)).toBe(true)
  })
  expect(view.state.doc.toString()).toContain('// edit')
})

it('never undoes into another problem and updates programmatically restored text', async () => {
  const first = await render()
  await act(async () => first.dispatch({ changes: { from: 0, insert: 'old' } }))
  const second = await render({ documentId: '5-A', value: 'new problem' })
  expect(second).not.toBe(first)
  expect(undo(second)).toBe(false)
  await render({ value: 'restored server draft' })
  expect(second.state.doc.toString()).toBe('restored server draft')
})

it('rejects an oversized UTF-8 transaction without changing existing text', async () => {
  const value = '字'.repeat(21845)
  const view = await render({ value })
  await act(async () => {
    view.dispatch({ changes: { from: value.length, insert: 'ab' } })
    await Promise.resolve()
  })
  expect(view.state.doc.toString()).toBe(value)
  expect(codeBytes(value)).toBe(65535)
  expect(props.onChange).not.toHaveBeenCalled()
  expect(props.onError).toHaveBeenCalled()
})

it('uses Tab to accept a completion while Enter inserts a newline', async () => {
  const view = await render({ value: 'int mai' })
  await act(async () => {
    view.focus()
    view.dispatch({ selection: { anchor: view.state.doc.length } })
    expect(startCompletion(view)).toBe(true)
    await vi.waitFor(() => expect(completionStatus(view.state)).toBe('active'))
  })
  expect(host.querySelector('.cm-tooltip-autocomplete')).not.toBeNull()
  await act(async () =>
    view.contentDOM.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }),
    ),
  )
  expect(view.state.doc.toString()).toContain('\n')
  expect(host.querySelector('.cm-tooltip-autocomplete')).toBeNull()
  await act(async () => {
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: 'int mai' },
      selection: { anchor: 7 },
    })
    expect(startCompletion(view)).toBe(true)
    await vi.waitFor(() => expect(completionStatus(view.state)).toBe('active'))
    view.contentDOM.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }),
    )
  })
  expect(view.state.doc.toString()).toContain('main')
})

it('waits for composition to complete before publishing Chinese input', async () => {
  const view = await render({ value: '' })
  await act(async () => {
    view.contentDOM.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }))
    view.dispatch({ changes: { from: 0, insert: '中文' } })
  })
  expect(props.onChange).not.toHaveBeenCalled()
  await act(async () => {
    view.contentDOM.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true }))
    await Promise.resolve()
  })
  expect(props.onChange).toHaveBeenCalledWith('中文')
})

it('routes :w to the latest asynchronous save callback without remounting', async () => {
  const view = await render({ settings: { ...defaultEditorSettings, keymap: 'vim' } })
  await act(async () => {
    await vi.waitFor(() => expect(host.textContent).toContain('NORMAL'))
  })
  const { getCM } = await import('@replit/codemirror-vim')
  const save = vi.fn().mockResolvedValue(false)
  expect(await render({ onSave: save })).toBe(view)
  await act(async () => {
    Vim.handleEx(getCM(view)! as Parameters<typeof Vim.handleEx>[0], 'w')
  })
  expect(save).toHaveBeenCalledTimes(1)
  await act(async () => {
    Vim.handleEx(getCM(view)! as Parameters<typeof Vim.handleEx>[0], 'wq')
  })
  expect(save).toHaveBeenCalledTimes(1)
  expect(props.onError).toHaveBeenCalledWith(expect.stringContaining('不'))
  expect(await render({ settings: defaultEditorSettings })).toBe(view)
})

it('preserves an accessible fallback when the enhanced editor fails to render', async () => {
  const broken = () => {
    throw new Error('simulated chunk failure')
  }
  const silent = vi.spyOn(console, 'error').mockImplementation(() => {})
  await act(async () =>
    root.render(
      <EditorBoundary fallback={<textarea aria-label="源代码" defaultValue="preserved" />}>
        {React.createElement(broken)}
      </EditorBoundary>,
    ),
  )
  expect(host.querySelector('textarea')?.value).toBe('preserved')
  silent.mockRestore()
})

it('C++20 completion includes signatures, headers and constrained function snippets', async () => {
  const { languageCompletions } = await import('./editor-completions')
  const options = languageCompletions('cpp20')
  expect(options.find((c) => c.label === 'std::ranges::sort')?.detail).toContain('C++20')
  expect(options.some((c) => c.label === 'include-concepts')).toBe(true)
  expect(options.some((c) => c.label === 'concept-function')).toBe(true)
  expect(options.find((c) => c.label === 'contains')?.detail).toContain('非类型推断')
})

const waitForLanguage = async (view: EditorView, name = 'cpp') => {
  await act(async () => {
    await vi.waitFor(() => expect(view.state.facet(language)?.name).toBe(name))
  })
}
const press = async (view: EditorView, key: string, init: KeyboardEventInit = {}) => {
  await act(async () => {
    view.focus()
    view.contentDOM.dispatchEvent(
      new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init }),
    )
  })
}
const setDoc = async (view: EditorView, text: string, head = text.length) => {
  await act(async () => {
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: text },
      selection: { anchor: Math.min(head, text.length) },
    })
  })
}
const openCompletion = async (view: EditorView) => {
  await act(async () => {
    view.focus()
    expect(startCompletion(view)).toBe(true)
    await vi.waitFor(() => expect(completionStatus(view.state)).toBe('active'))
  })
}

describe('indentation', () => {
  it.each(['Enter', 'ShiftEnter'])(
    '%s uses one newline path with or without an open completion and remains undoable',
    async (key) => {
      const view = await render({ value: '' })
      await waitForLanguage(view)
      const initial = 'int main() {\n    ret'
      await setDoc(view, initial)
      await openCompletion(view)
      await press(view, 'Enter', { shiftKey: key === 'ShiftEnter' })
      expect(view.state.doc.toString()).toBe(initial + '\n    ')
      expect(completionStatus(view.state)).toBeNull()
      await act(async () => { expect(undo(view)).toBe(true) })
      expect(view.state.doc.toString()).toBe(initial)
      await press(view, 'Enter', { shiftKey: key === 'ShiftEnter' })
      expect(view.state.doc.toString()).toBe(initial + '\n    ')
    },
  )

  it('preserves deliberately indented blank lines without changing an imported draft', async () => {
    const original = '\tint x;\n        '
    const view = await render({ value: original })
    await waitForLanguage(view)
    expect(view.state.doc.toString()).toBe(original)
    await act(async () => view.dispatch({ selection: { anchor: original.length } }))
    await press(view, 'Enter')
    expect(view.state.doc.toString()).toBe(original + '\n        ')
    await press(view, 'Enter', { shiftKey: true })
    expect(view.state.doc.toString()).toBe(original + '\n        \n        ')
    await setDoc(view, '        ', 4)
    await press(view, 'Enter')
    expect(view.state.doc.toString()).toBe('    \n    ')
    expect(view.state.selection.main.head).toBe(9)
  })

  it.each([
    ['cpp20', 'cpp', 'int main() {\n\tif (ok) {', '        '],
    ['cpp20', 'cpp', 'int main() {\n\t    if (ok) {', '            '],
    ['java17', 'java', 'class Main {\n\tvoid run() {', '        '],
    ['python3', 'python', 'def solve():\n\tif ok:', '        '],
  ])('normalizes only new %s indentation after mixed whitespace paste', async (id, name, original, expected) => {
    const view = await render({ value: '', language: id })
    await waitForLanguage(view, name)
    await setDoc(view, original)
    await press(view, 'Enter')
    expect(view.state.doc.toString()).toBe(original + '\n' + expected)
    expect(view.state.doc.line(view.state.doc.lines).text).not.toContain('\t')
    await act(async () => { expect(undo(view)).toBe(true) })
    expect(view.state.doc.toString()).toBe(original)
  })

  it.each([
    ['cpp20', 'cpp', 'int main() {\n    std::cou'],
    ['java17', 'java', 'class Main {\n    ret'],
  ])('inherits a partially typed identifier in %s', async (id, name, original) => {
    const view = await render({ value: original, language: id })
    await waitForLanguage(view, name)
    await act(async () => view.dispatch({ selection: { anchor: original.length } }))
    await press(view, 'Enter')
    expect(view.state.doc.toString()).toBe(original + '\n    ')
  })

  it('retains language-directed dedentation after a closing brace', async () => {
    const original = 'int main() {\n    if (ok) {\n    }'
    const view = await render({ value: original })
    await waitForLanguage(view)
    await act(async () => view.dispatch({ selection: { anchor: original.length } }))
    await press(view, 'Enter')
    expect(view.state.doc.toString()).toBe(original + '\n    ')
  })

  it('keeps read-only code unchanged when Enter or Tab is pressed', async () => {
    const original = 'int main() {}'
    const view = await render({ value: original, readOnly: true })
    await waitForLanguage(view)
    await press(view, 'Enter')
    await press(view, 'Enter', { shiftKey: true })
    await press(view, 'Tab')
    expect(view.state.doc.toString()).toBe(original)
    expect(props.onChange).not.toHaveBeenCalled()
  })

  it('inherits the previous indentation and adds a level inside scopes', async () => {
    const view = await render({ value: '' })
    await waitForLanguage(view)
    expect(view.state.facet(indentUnit)).toBe('    ')
    expect(view.state.tabSize).toBe(4)
    await setDoc(view, 'int main() {')
    await press(view, 'Enter')
    expect(view.state.doc.toString()).toBe('int main() {\n    ')
    await setDoc(view, 'int main() {\n    if (x) {')
    await press(view, 'Enter')
    expect(view.state.doc.toString()).toBe('int main() {\n    if (x) {\n        ')
    await setDoc(view, 'int main() {\n    int x;')
    await press(view, 'Enter')
    expect(view.state.doc.toString()).toBe('int main() {\n    int x;\n    ')
  })

  it('indents a split brace pair and a Python block', async () => {
    const view = await render({ value: '' })
    await waitForLanguage(view)
    await setDoc(view, 'int main() {}', 12)
    await press(view, 'Enter')
    expect(view.state.doc.toString()).toBe('int main() {\n    \n}')
    expect(view.state.selection.main.head).toBe(17)
    const python = await render({ documentId: 'py', value: 'def f():', language: 'python3' })
    await waitForLanguage(python, 'python')
    await setDoc(python, 'def f():')
    await press(python, 'Enter')
    expect(python.state.doc.toString()).toBe('def f():\n    ')
  })

  it('keeps the inherited indentation when Enter closes an open completion', async () => {
    const view = await render({ value: '' })
    await waitForLanguage(view)
    await setDoc(view, 'int main() {\n    int mai')
    await openCompletion(view)
    expect(host.querySelector('.cm-tooltip-autocomplete')).not.toBeNull()
    await press(view, 'Enter')
    expect(view.state.doc.toString()).toBe('int main() {\n    int mai\n    ')
    expect(host.querySelector('.cm-tooltip-autocomplete')).toBeNull()
  })

  it('tabs to the next level, keeps mid-line text in place and shifts back', async () => {
    const view = await render({ value: '' })
    await waitForLanguage(view)
    await press(view, 'Tab')
    expect(view.state.doc.toString()).toBe('    ')
    expect(view.state.selection.main.head).toBe(4)
    await setDoc(view, '    int x;', 2)
    await press(view, 'Tab')
    expect(view.state.doc.toString()).toBe('        int x;')
    expect(view.state.selection.main.head).toBe(8)
    await press(view, 'Tab', { shiftKey: true })
    expect(view.state.doc.toString()).toBe('    int x;')
    await setDoc(view, 'int x;', 3)
    await press(view, 'Tab')
    expect(view.state.doc.toString()).toBe('int  x;')
    expect(view.state.selection.main.head).toBe(4)
    await setDoc(view, 'a\nb')
    await act(async () => view.dispatch({ selection: { anchor: 0, head: 3 } }))
    await press(view, 'Tab')
    expect(view.state.doc.toString()).toBe('    a\n    b')
    await press(view, 'Tab', { shiftKey: true })
    expect(view.state.doc.toString()).toBe('a\nb')
  })

  it('keeps Vim normal-mode keys and indents in insert mode', async () => {
    const view = await render({
      value: 'int x;',
      settings: { ...defaultEditorSettings, keymap: 'vim' },
    })
    await waitForLanguage(view)
    await act(async () => {
      await vi.waitFor(() => expect(host.textContent).toContain('NORMAL'))
    })
    await press(view, 'Tab')
    expect(view.state.doc.toString()).toBe('int x;')
    await press(view, 'Tab', { shiftKey: true })
    expect(view.state.doc.toString()).toBe('int x;')
    await press(view, 'i')
    await act(async () => {
      await vi.waitFor(() => expect(host.textContent).toContain('INSERT'))
    })
    await press(view, 'Tab')
    expect(view.state.doc.toString()).toBe('    int x;')
    expect(view.state.selection.main.head).toBe(4)
    await setDoc(view, 'vec', 3)
    await openCompletion(view)
    await press(view, 'Tab')
    expect(view.state.doc.toString()).toBe('vector')
  })

  it('uses four-space Enter and Shift-Enter in Vim insert mode without accepting a completion', async () => {
    const view = await render({ value: '', settings: { ...defaultEditorSettings, keymap: 'vim' } })
    await waitForLanguage(view)
    await act(async () => { await vi.waitFor(() => expect(host.textContent).toContain('NORMAL')) })
    await press(view, 'i')
    await setDoc(view, 'int main() {}', 12)
    await press(view, 'Enter')
    expect(view.state.doc.toString()).toBe('int main() {\n    \n}')
    expect(view.state.selection.main.head).toBe(17)
    await setDoc(view, 'int main() {\n    ret')
    await openCompletion(view)
    await press(view, 'Enter', { shiftKey: true })
    expect(view.state.doc.toString()).toBe('int main() {\n    ret\n    ')
    expect(completionStatus(view.state)).toBeNull()
  })

  it('does not insert indentation in Vim normal or visual mode and preserves visual editing', async () => {
    const original = 'alpha\nbeta'
    const view = await render({ value: original, settings: { ...defaultEditorSettings, keymap: 'vim' } })
    await waitForLanguage(view)
    await act(async () => { await vi.waitFor(() => expect(host.textContent).toContain('NORMAL')) })
    await press(view, 'Enter')
    expect(view.state.doc.toString()).toBe(original)
    await press(view, 'v')
    expect(host.textContent).toContain('VISUAL')
    await press(view, 'Tab')
    await press(view, 'Tab', { shiftKey: true })
    expect(view.state.doc.toString()).toBe(original)
    await press(view, 'Escape')
    await press(view, '0')
    await press(view, 'v')
    await press(view, 'l')
    await press(view, 'x')
    expect(view.state.doc.toString()).toBe('alpha\nta')
    await press(view, 'u')
    expect(view.state.doc.toString()).toBe(original)
  })

  it('follows the configured indentation unit and tab size', () => {
    const parent = document.createElement('div')
    document.body.appendChild(parent)
    const view = new EditorView({
      parent,
      state: EditorState.create({
        doc: '',
        extensions: [indentUnit.of('  '), EditorState.tabSize.of(2)],
      }),
    })
    expect(indentTab(view)).toBe(true)
    expect(view.state.doc.toString()).toBe('  ')
    act(() => {
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: 'ab' },
        selection: { anchor: 1 },
      })
    })
    expect(indentTab(view)).toBe(true)
    expect(view.state.doc.toString()).toBe('a b')
    expect(view.state.selection.main.head).toBe(2)
    view.destroy()
    parent.remove()
  })
})

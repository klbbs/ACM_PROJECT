import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EditorView } from '@codemirror/view'
import { EditorState } from '@codemirror/state'
import { undo, redo } from '@codemirror/commands'
import { CompletionContext, completionStatus, startCompletion } from '@codemirror/autocomplete'
import { Vim } from '@replit/codemirror-vim'
import CodeEditor from './code-editor'
import { EditorBoundary } from './editor-shell'
import { localCompletion } from './editor-completions'
import { codeBytes, defaultEditorSettings, type CodeEditorProps } from './editor-settings'

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
  await act(async () => view.contentDOM.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })))
  expect(view.state.doc.toString()).toContain('\n')
  expect(host.querySelector('.cm-tooltip-autocomplete')).toBeNull()
  await act(async () => {
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: 'int mai' }, selection: { anchor: 7 } })
    expect(startCompletion(view)).toBe(true)
    await vi.waitFor(() => expect(completionStatus(view.state)).toBe('active'))
    view.contentDOM.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
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

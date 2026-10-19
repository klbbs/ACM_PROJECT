import React, { useEffect, useRef, useState } from 'react'
import { basicSetup } from 'codemirror'
import { Compartment, EditorState, Prec, Transaction } from '@codemirror/state'
import { EditorView, keymap } from '@codemirror/view'
import { openSearchPanel } from '@codemirror/search'
import { indentWithTab, insertNewline, redo } from '@codemirror/commands'
import { indentUnit } from '@codemirror/language'
import {
  autocompletion,
  startCompletion,
  closeCompletion,
  acceptCompletion,
  completionStatus,
  nextSnippetField,
  moveCompletionSelection,
} from '@codemirror/autocomplete'
import { oneDark } from '@codemirror/theme-one-dark'
import { localCompletion } from './editor-completions'
import { CODE_LIMIT, codeBytes, type CodeEditorProps } from './editor-settings'

export default function CodeEditor(props: CodeEditorProps) {
  const host = useRef<HTMLDivElement>(null),
    view = useRef<EditorView | null>(null)
  const vimApi = useRef<typeof import('./editor-vim') | null>(null)
  const latest = useRef(props)
  latest.current = props
  const [position, setPosition] = useState('行 1，列 1'),
    [mode, setMode] = useState('普通')
  const language = useRef(new Compartment()),
    bindings = useRef(new Compartment())
  const appearance = useRef(new Compartment()),
    access = useRef(new Compartment()),
    completion = useRef(new Compartment())
  const composing = useRef(false),
    external = useRef(false),
    beforeComposition = useRef('')
  const publish = (editor: EditorView) => {
    const text = editor.state.doc.toString()
    if (codeBytes(text) <= CODE_LIMIT && text !== latest.current.value)
      latest.current.onChange(text)
  }
  const reportLimit = () =>
    latest.current.onError('代码最多 64 KiB UTF-8，本次输入未写入；原有代码已保留')
  const vimMode = (editor: EditorView) => {
    const state = vimApi.current?.getCM(editor)?.state.vim
    return !state ? '普通' : state.insertMode ? 'INSERT' : state.visualMode ? 'VISUAL' : 'NORMAL'
  }
  useEffect(() => {
    const editor = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: latest.current.value,
        extensions: [
          bindings.current.of([]),
          Prec.highest(
            EditorView.domEventHandlers({
              keydown(event) {
                if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
                  event.preventDefault()
                  void latest.current.onSave()
                  return true
                }
                return false
              },
              compositionstart(_event, editor) {
                composing.current = true
                beforeComposition.current = editor.state.doc.toString()
              },
              compositionend(_event, editor) {
                // Let the browser's final composition transaction finish first.
                queueMicrotask(() => {
                  if (view.current !== editor) return
                  composing.current = false
                  if (codeBytes(editor.state.doc.toString()) > CODE_LIMIT) {
                    external.current = true
                    editor.dispatch({
                      changes: {
                        from: 0,
                        to: editor.state.doc.length,
                        insert: beforeComposition.current,
                      },
                      annotations: Transaction.addToHistory.of(false),
                    })
                    external.current = false
                    reportLimit()
                  } else publish(editor)
                })
              },
            }),
          ),
          Prec.highest(keymap.of([{ key: 'Tab', run: acceptCompletion }])),
          basicSetup,
          oneDark,
          EditorState.tabSize.of(4),
          indentUnit.of('    '),
          Prec.high(
            keymap.of([
              { key: 'Ctrl-Space', run: startCompletion },
              { key: 'Mod-Shift-z', run: redo, preventDefault: true },
              {
                key: 'Ctrl-h',
                run: (editor) => {
                  openSearchPanel(editor)
                  editor.dom
                    .querySelector<HTMLInputElement>('.cm-search input[name="replace"]')
                    ?.focus()
                  return true
                },
              },
              { key: 'ArrowDown', run: moveCompletionSelection(true) },
              { key: 'ArrowUp', run: moveCompletionSelection(false) },
              {
                key: 'Tab',
                run: (editor) =>
                  completionStatus(editor.state)
                    ? acceptCompletion(editor)
                    : nextSnippetField(editor) || (indentWithTab.run?.(editor) ?? false),
              },
              {
                key: 'Enter',
                run: (editor) => {
                  if (!completionStatus(editor.state)) return false
                  closeCompletion(editor)
                  return insertNewline(editor)
                },
              },
              { key: 'Escape', run: closeCompletion },
              indentWithTab,
            ]),
          ),
          completion.current.of([]),
          language.current.of([]),
          appearance.current.of([]),
          access.current.of([]),
          EditorView.contentAttributes.of({
            'aria-label': '源代码',
            spellcheck: 'false',
            autocapitalize: 'off',
          }),
          EditorState.transactionFilter.of((tr) => {
            if (
              tr.docChanged &&
              !external.current &&
              !composing.current &&
              codeBytes(tr.newDoc.toString()) > CODE_LIMIT
            ) {
              queueMicrotask(reportLimit)
              return []
            }
            return tr
          }),
          EditorView.updateListener.of((update) => {
            if (update.docChanged && !external.current && !composing.current) publish(update.view)
            if (update.selectionSet || update.docChanged) {
              const at = update.state.selection.main.head,
                line = update.state.doc.lineAt(at)
              setPosition(`行 ${line.number}，列 ${at - line.from + 1}`)
            }
            setMode(vimMode(update.view))
          }),
        ],
      }),
    })
    view.current = editor
    return () => {
      view.current = null
      editor.destroy()
    }
  }, [])

  useEffect(() => {
    const editor = view.current!
    if (editor.state.doc.toString() !== props.value && !composing.current) {
      external.current = true
      editor.dispatch({
        changes: { from: 0, to: editor.state.doc.length, insert: props.value },
        annotations: Transaction.addToHistory.of(false),
      })
      external.current = false
    }
  }, [props.value])

  useEffect(() => {
    const editor = view.current!
    if (props.settings.keymap !== 'vim') {
      editor.dispatch({ effects: bindings.current.reconfigure([]) })
      closeCompletion(editor)
      setMode('普通')
      return
    }
    let cancelled = false,
      dispose = () => {}
    import('./editor-vim')
      .then((api) => {
        if (cancelled) return
        vimApi.current = api
        editor.dispatch({ effects: bindings.current.reconfigure(api.vim({ status: true })) })
        setMode(vimMode(editor))
        dispose = api.bindVim(
          editor,
          () => {
            void latest.current.onSave()
          },
          () => latest.current.onError('请使用 :w 保存；:q、:wq、:x 不会退出网站'),
          () => {
            closeCompletion(editor)
            setMode(vimMode(editor))
          },
        )
      })
      .catch(() => {
        if (!cancelled)
          latest.current.onError('Vim 扩展加载失败，当前仍可用普通模式编辑；请重试切换')
      })
    return () => {
      cancelled = true
      dispose()
    }
  }, [props.settings.keymap])

  useEffect(() => {
    const editor = view.current!
    editor.dispatch({
      effects: [
        appearance.current.reconfigure([
          EditorView.theme({
            '&': { fontSize: `${props.settings.font_size}px` },
            '.cm-scroller': {
              fontFamily: "'JetBrains Mono', Consolas, monospace",
              lineHeight: '1.7',
            },
          }),
          props.settings.line_wrapping ? EditorView.lineWrapping : [],
        ]),
        access.current.reconfigure([
          EditorState.readOnly.of(props.readOnly),
          EditorView.editable.of(!props.readOnly),
        ]),
      ],
    })
  }, [props.settings.font_size, props.settings.line_wrapping, props.readOnly])

  useEffect(() => {
    const editor = view.current!
    const canComplete = () =>
      latest.current.settings.keymap !== 'vim' ||
      !!vimApi.current?.getCM(editor)?.state.vim?.insertMode
    editor.dispatch({
      effects: completion.current.reconfigure(
        autocompletion({
          override: [localCompletion(props.language, canComplete)],
          defaultKeymap: false,
          activateOnTyping: true,
          activateOnTypingDelay: 150,
          interactionDelay: 0,
        }),
      ),
    })
    let cancelled = false
    const loader = props.language.startsWith('cpp')
      ? import('@codemirror/lang-cpp').then((m) => m.cpp())
      : props.language === 'python3'
        ? import('@codemirror/lang-python').then((m) => m.python())
        : import('@codemirror/lang-java').then((m) => m.java())
    loader
      .then((support) => {
        if (!cancelled) editor.dispatch({ effects: language.current.reconfigure(support) })
      })
      .catch(() => {
        if (!cancelled)
          latest.current.onError('语言高亮加载失败，代码仍可编辑和保存；可切换基础编辑器')
      })
    return () => {
      cancelled = true
    }
  }, [props.language])

  return (
    <div className="cm-workspace">
      <div ref={host} className="cm-host" />
      <div className="cm-position">
        <span>{mode}</span>
        <span>{position}</span>
        <span>本地补全 · 四空格缩进</span>
      </div>
    </div>
  )
}

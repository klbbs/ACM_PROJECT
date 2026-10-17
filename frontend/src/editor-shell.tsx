import React, { Component, lazy, Suspense, useState } from 'react'
import { CODE_LIMIT, codeBytes, type CodeEditorProps } from './editor-settings'
const RichEditor = lazy(() => import('./code-editor'))

export class EditorBoundary extends Component<
  { children: React.ReactNode; fallback: React.ReactNode },
  { failed: boolean }
> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children
  }
}
export function EditorShell(props: CodeEditorProps) {
  const [plain, setPlain] = useState(false)
  const fallback = <PlainEditor {...props} />
  const unavailable = (
    <div>
      <p role="alert">增强编辑器加载失败，代码已保留。</p>
      {plain ? (
        fallback
      ) : (
        <button className="secondary" onClick={() => setPlain(true)}>
          启用基础文本框
        </button>
      )}
    </div>
  )
  return (
    <>
      <div className="wb-editor-kind">
        <button className="text-button" onClick={() => setPlain((v) => !v)}>
          {plain ? '返回增强编辑器' : '切换基础编辑器'}
        </button>
      </div>
      {plain ? (
        fallback
      ) : (
        <EditorBoundary fallback={unavailable}>
          <Suspense fallback={<p role="status">正在加载代码编辑器…</p>}>
            <RichEditor {...props} />
          </Suspense>
        </EditorBoundary>
      )}
    </>
  )
}

function PlainEditor(props: CodeEditorProps) {
  const [text, setText] = useState(props.value)
  const composing = React.useRef(false),
    before = React.useRef(props.value)
  React.useEffect(() => {
    if (!composing.current) setText(props.value)
  }, [props.value])
  const accept = (value: string) => {
    if (codeBytes(value) <= CODE_LIMIT) {
      setText(value)
      props.onChange(value)
    } else {
      setText(props.value)
      props.onError('代码最多 64 KiB UTF-8，本次输入未写入')
    }
  }
  return (
    <textarea
      aria-label="源代码"
      className="wb-code"
      disabled={props.readOnly}
      value={text}
      spellCheck={false}
      onCompositionStart={(e) => {
        composing.current = true
        before.current = e.currentTarget.value
      }}
      onCompositionEnd={(e) => {
        composing.current = false
        const value = e.currentTarget.value
        if (codeBytes(value) > CODE_LIMIT) {
          setText(before.current)
          props.onError('代码最多 64 KiB UTF-8，本次输入未写入')
        } else accept(value)
      }}
      onChange={(e) => {
        if (composing.current) setText(e.target.value)
        else accept(e.target.value)
      }}
      onKeyDown={(e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
          e.preventDefault()
          void props.onSave()
        }
      }}
    />
  )
}

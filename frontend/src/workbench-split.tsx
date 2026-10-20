import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { GripVertical } from 'lucide-react'
import { api } from './api'
import './workbench-split.css'

type Layout = { mode: 'auto' | 'split' | 'stacked'; statement_percent: number }
const defaults: Layout = { mode: 'auto', statement_percent: 50 }

/** Keep both panes mounted when changing layout so editor state and undo survive. */
export function WorkbenchSplit({ children }: { children: [ReactNode, ReactNode] }) {
  const [layout, setLayout] = useState<Layout>(defaults)
  const latest = useRef(layout)
  const [ready, setReady] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [width, setWidth] = useState(0)
  const [dragging, setDragging] = useState(false)
  const draggingRef = useRef(false)
  const host = useRef<HTMLDivElement>(null)
  const mounted = useRef(false)
  const queue = useRef(Promise.resolve())
  const sequence = useRef(0)
  const id = useId()

  useEffect(() => {
    let alive = true
    mounted.current = true
    api<Layout>('/workbench/layout-settings')
      .then((value) => {
        if (alive) {
          latest.current = value
          setLayout(value)
        }
      })
      .catch(() => {
        if (alive) setError('布局偏好读取失败，暂用默认布局；调整后可重新保存。')
      })
      .finally(() => {
        if (alive) setReady(true)
      })
    return () => {
      alive = false
      mounted.current = false
    }
  }, [])

  useEffect(() => {
    const element = host.current
    if (!element) return
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const save = (value: Layout) => {
    latest.current = value
    setLayout(value)
    setSaving(true)
    setError('')
    const current = ++sequence.current
    // Serialize writes: a slow previous request must not overwrite the latest preference.
    queue.current = queue.current
      .catch(() => {})
      .then(async () => {
        try {
          await api('/workbench/layout-settings', 'PUT', value)
        } catch {
          if (mounted.current && current === sequence.current)
            setError('布局已调整，但保存失败；重新进入时可能恢复旧布局。')
        } finally {
          if (mounted.current && current === sequence.current) setSaving(false)
        }
      })
  }
  const sideBySide = layout.mode !== 'stacked' && width >= (layout.mode === 'split' ? 820 : 960)
  const available = Math.max(1, width - 18)
  const minimum = sideBySide ? Math.max(30, Math.ceil((320 / available) * 100)) : 30
  const maximum = sideBySide ? Math.min(70, Math.floor((1 - 380 / available) * 100)) : 70
  const clamp = (value: number) => Math.max(minimum, Math.min(maximum, Math.round(value)))
  const percent = sideBySide ? clamp(layout.statement_percent) : layout.statement_percent

  const move = (clientX: number) => {
    const rect = host.current!.getBoundingClientRect()
    const value = {
      ...latest.current,
      statement_percent: clamp(((clientX - rect.left - 9) / available) * 100),
    }
    latest.current = value
    setLayout(value)
  }
  const finishDrag = () => {
    if (!draggingRef.current) return
    draggingRef.current = false
    setDragging(false)
    save(latest.current)
  }

  return (
    <section className="wb-reading-workspace" aria-label="题面与编辑器布局">
      <div className="wb-layout-controls">
        <label>
          工作台布局
          <select
            aria-label="工作台布局"
            value={layout.mode}
            disabled={!ready}
            onChange={(e) => save({ ...latest.current, mode: e.target.value as Layout['mode'] })}
          >
            <option value="auto">自动适配</option>
            <option value="split">左右分栏</option>
            <option value="stacked">上下排列</option>
          </select>
        </label>
        <button className="secondary" disabled={!ready} onClick={() => save(defaults)}>
          重置布局
        </button>
        <span className="muted">
          {sideBySide
            ? `题面 ${percent}% · 编辑器 ${100 - percent}% · 拖动中间分隔线调整`
            : layout.mode === 'split'
              ? '当前空间较窄，已自动上下排列；空间足够时恢复左右分栏。'
              : layout.mode === 'stacked'
                ? '题面在上，编辑器在下。'
                : '题面在上，编辑器在下；可用空间足够时自动左右分栏。'}
        </span>
        <span className="muted" role="status">
          {!ready ? '读取布局偏好…' : saving ? '保存布局…' : error ? '' : '布局按用户保存'}
        </span>
        {error && (
          <span className="wb-layout-error" role="alert">
            {error}{' '}
            <button className="text-button" onClick={() => save(latest.current)}>
              重试保存
            </button>
          </span>
        )}
      </div>
      <div
        ref={host}
        className={`wb-split-panes ${sideBySide ? 'is-split' : 'is-stacked'} ${dragging ? 'is-resizing' : ''}`}
        style={
          sideBySide
            ? { gridTemplateColumns: `minmax(0, ${percent}fr) 18px minmax(0, ${100 - percent}fr)` }
            : undefined
        }
      >
        <div id={`${id}-statement`} className="wb-statement-pane">
          {children[0]}
        </div>
        <div
          className="wb-pane-divider"
          role="separator"
          aria-label="调整题面与编辑器宽度"
          aria-orientation="vertical"
          aria-disabled={!ready}
          aria-valuemin={minimum}
          aria-valuemax={maximum}
          aria-valuenow={percent}
          aria-valuetext={`题面 ${percent}%，编辑器 ${100 - percent}%`}
          aria-controls={`${id}-statement ${id}-editor`}
          tabIndex={sideBySide && ready ? 0 : -1}
          title="拖动调整宽度；方向键微调，双击恢复各占一半"
          onPointerDown={(e) => {
            if (!ready || e.button !== 0) return
            e.preventDefault()
            e.currentTarget.focus()
            e.currentTarget.setPointerCapture(e.pointerId)
            draggingRef.current = true
            setDragging(true)
          }}
          onPointerMove={(e) => {
            if (draggingRef.current) move(e.clientX)
          }}
          onPointerUp={finishDrag}
          onPointerCancel={finishDrag}
          onLostPointerCapture={finishDrag}
          onDoubleClick={() => {
            if (ready) save({ ...latest.current, statement_percent: 50 })
          }}
          onKeyDown={(e) => {
            if (!ready) return
            const step = e.shiftKey ? 10 : 2
            const value =
              e.key === 'ArrowLeft'
                ? percent - step
                : e.key === 'ArrowRight'
                  ? percent + step
                  : e.key === 'Home'
                    ? minimum
                    : e.key === 'End'
                      ? maximum
                      : null
            if (value === null) return
            e.preventDefault()
            save({ ...latest.current, statement_percent: clamp(value) })
          }}
        >
          <GripVertical size={18} aria-hidden="true" />
        </div>
        <div id={`${id}-editor`} className="wb-editor-pane">
          {children[1]}
        </div>
      </div>
    </section>
  )
}

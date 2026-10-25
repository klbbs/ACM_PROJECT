import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { FileText, LoaderCircle, RefreshCw, X } from 'lucide-react'
import { api, date } from './api'
import './selection-report.css'

const confirmation = '此报告为ai生成的代码解析报告，你确定要现在看吗？'
const legacyReason = '此题单生成于报告功能上线前，暂无报告；重新AI选题后可查看'

type SelectionReport = {
  id: string
  problem_id: string
  created_at: number
  provider: string
  model: string
  result: {
    problem_id: string
    suitable: boolean
    selection_fit: string
    problem_summary: string
    constraints: string[]
    approach: string[]
    implementation_notes: string[]
    complexity: string
    limitations: string[]
  }
}
type State = {
  identity: string
  phase: 'closed' | 'confirm' | 'loading' | 'ready' | 'error'
  report?: SelectionReport
  error?: string
}
type Props = { reportId?: string | null; problemId: string; title?: string }

function readReport(value: unknown, reportId: string, problemId: string): SelectionReport {
  if (!value || typeof value !== 'object') throw new Error('报告格式不正确，请稍后重试')
  const report = value as SelectionReport
  if (
    report.id !== reportId ||
    report.problem_id !== problemId ||
    report.result?.problem_id !== problemId
  ) {
    throw new Error('报告与当前题目不匹配，已停止展示，请刷新题单后重试')
  }
  const result = report.result
  if (
    typeof result.suitable !== 'boolean' ||
    ![result.selection_fit, result.problem_summary, result.complexity].every(
      (value) => typeof value === 'string',
    ) ||
    ![result.constraints, result.approach, result.implementation_notes, result.limitations].every(
      (items) => Array.isArray(items) && items.every((item) => typeof item === 'string'),
    ) ||
    typeof report.created_at !== 'number' ||
    !Number.isFinite(report.created_at) ||
    typeof report.provider !== 'string' ||
    typeof report.model !== 'string'
  ) {
    throw new Error('报告内容不完整，请稍后重试')
  }
  return report
}

function Text({ value }: { value: string }) {
  return <Markdown remarkPlugins={[remarkGfm]}>{value}</Markdown>
}

function ReportBody({ report }: { report: SelectionReport }) {
  const result = report.result
  const sections: [string, string[]][] = [
    ['关键约束', result.constraints],
    ['解题思路', result.approach],
    ['实现要点', result.implementation_notes],
    ['限制与不确定性', result.limitations],
  ]
  return (
    <div className="selection-report-content">
      <p className="help">
        {report.model} · {date(report.created_at)} · {report.problem_id}
      </p>
      <section>
        <h3>选题匹配</h3>
        <p>{result.suitable ? 'AI 判断适合本次训练' : 'AI 判断适配性有限，请结合下方说明判断'}</p>
        <Text value={result.selection_fit} />
      </section>
      <section>
        <h3>题目概述</h3>
        <Text value={result.problem_summary} />
      </section>
      {sections.map(([heading, paragraphs]) => (
        <section key={heading}>
          <h3>{heading}</h3>
          {paragraphs.length ? (
            paragraphs.map((paragraph, index) => <Text key={index} value={paragraph} />)
          ) : (
            <p>报告未提供此项信息。</p>
          )}
        </section>
      ))}
      <section>
        <h3>复杂度</h3>
        <Text value={result.complexity} />
      </section>
    </div>
  )
}

/** A deliberate disclosure: no report request is made before confirmation. */
export function SelectionReportButton({ reportId, problemId, title }: Props) {
  const identity = JSON.stringify([reportId || '', problemId])
  const scope = useRef(identity)
  scope.current = identity
  const [state, setState] = useState<State>({ identity, phase: 'closed' })
  const phase = state.identity === identity ? state.phase : 'closed'
  const opened = phase !== 'closed'
  const dialog = useRef<HTMLDialogElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const mounted = useRef(false)
  const request = useRef(0)
  const requesting = useRef(false)
  const id = useId()

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      request.current += 1
      requesting.current = false
    }
  }, [])
  useEffect(() => {
    request.current += 1
    requesting.current = false
    setState({ identity, phase: 'closed' })
  }, [identity])
  useEffect(() => {
    if (!opened) return
    const element = dialog.current
    if (!element) return
    if (!element.open) element.showModal()
    heading.current?.focus()
    const previous = document.documentElement.style.overflow
    document.documentElement.style.overflow = 'hidden'
    return () => {
      document.documentElement.style.overflow = previous
      if (element.open) element.close()
      if (trigger.current?.isConnected) trigger.current.focus()
    }
  }, [opened, identity])

  const close = () => {
    request.current += 1
    requesting.current = false
    setState({ identity, phase: 'closed' })
  }
  const read = async () => {
    if (!reportId || requesting.current || !['confirm', 'error'].includes(phase)) return
    const token = ++request.current
    const current = identity
    requesting.current = true
    setState({ identity, phase: 'loading' })
    try {
      const response: unknown = await api(`/selection-reports/${encodeURIComponent(reportId)}`)
      if (!mounted.current || token !== request.current || scope.current !== current) return
      const report = readReport(response, reportId, problemId)
      setState({ identity, phase: 'ready', report })
    } catch (caught) {
      if (mounted.current && token === request.current && scope.current === current) {
        setState({
          identity,
          phase: 'error',
          error: caught instanceof Error ? caught.message : '报告读取失败，请稍后重试',
        })
      }
    } finally {
      if (mounted.current && token === request.current && scope.current === current) {
        requesting.current = false
      }
    }
  }

  return (
    <div className="selection-report-entry">
      <button
        type="button"
        className="button secondary"
        ref={trigger}
        disabled={!reportId}
        aria-describedby={!reportId ? `${id}-legacy` : undefined}
        aria-haspopup="dialog"
        onClick={() => setState({ identity, phase: 'confirm' })}
      >
        <FileText size={15} /> 查看 AI 报告
      </button>
      {!reportId && (
        <p className="help" id={`${id}-legacy`}>
          {legacyReason}
        </p>
      )}
      {opened &&
        createPortal(
          <dialog
            className="selection-report-dialog"
            ref={dialog}
            aria-labelledby={`${id}-title`}
            aria-describedby={`${id}-notice`}
            onCancel={(event) => {
              event.preventDefault()
              close()
            }}
            onClose={(event) => {
              if (dialog.current === event.currentTarget) close()
            }}
          >
            <div className="selection-report-header">
              <h2 id={`${id}-title`} ref={heading} tabIndex={-1}>
                {phase === 'confirm' ? '查看前确认' : 'AI 选题报告'}
              </h2>
              <button
                type="button"
                className="text-button"
                aria-label="关闭 AI 报告"
                onClick={close}
              >
                <X size={18} />
              </button>
            </div>
            <p className="selection-report-problem">{title || problemId}</p>
            <p id={`${id}-notice`} className="selection-report-notice">
              报告可能包含解题思路和实现要点。内容是 AI
              阅读题面的推测，并非官方解释；未运行代码，也未验证解法正确性。
            </p>
            {phase === 'confirm' && (
              <>
                <p className="selection-report-confirmation">{confirmation}</p>
                <div className="selection-report-actions">
                  <button type="button" className="button secondary" onClick={close}>
                    取消
                  </button>
                  <button type="button" className="button primary" onClick={() => void read()}>
                    确定查看
                  </button>
                </div>
              </>
            )}
            {phase === 'loading' && (
              <p role="status" className="selection-report-loading">
                <LoaderCircle size={19} /> 正在读取已保存报告…
              </p>
            )}
            {phase === 'error' && (
              <div>
                <p role="alert" className="selection-report-error">
                  {state.error}
                </p>
                <button type="button" className="button secondary" onClick={() => void read()}>
                  <RefreshCw size={15} /> 重试读取
                </button>
              </div>
            )}
            {phase === 'ready' && state.report && <ReportBody report={state.report} />}
            {phase !== 'confirm' && (
              <p className="help">这里只读取已保存报告，不会调用 AI 重新生成。</p>
            )}
          </dialog>,
          document.body,
        )}
    </div>
  )
}

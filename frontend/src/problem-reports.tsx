import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { FileText, X } from 'lucide-react'
import { api, date } from './api'
import './selection-report.css'

type Assessment = {
  rating: number | null
  confidence: string
  evidence: string[]
  limitations: string[]
}
type Summary = {
  id: string
  problem_id: string
  created_at: number
  model: string
  difficulty_assessment: Pick<Assessment, 'rating' | 'confidence'>
}
type Report = {
  id: string
  problem_id: string
  model: string
  created_at: number
  report: {
    problem_id: string
    difficulty_assessment: Assessment
    problem_explanation: {
      title: string
      description: string
      input_format: string
      output_format: string
      notes: string[]
    }
    suitable: boolean
    topic_fit: string
    summary: string
    constraints: string[]
    approach: string[]
    correctness: string[]
    complexity: string
    sample_walkthrough: string[]
    pitfalls: string[]
    alternative_approaches: string[]
    limitations: string[]
  }
}

function validate(value: Report, pid: string, rid: string): Report {
  const r = value?.report
  if (value?.id !== rid || value?.problem_id !== pid || r?.problem_id !== pid)
    throw new Error('报告与当前题目不匹配，已停止展示')
  if (
    !r.difficulty_assessment ||
    ![
      r.summary,
      r.topic_fit,
      r.complexity,
      r.problem_explanation?.description,
      r.problem_explanation?.input_format,
      r.problem_explanation?.output_format,
    ].every((v) => typeof v === 'string') ||
    ![
      r.problem_explanation?.notes,
      r.constraints,
      r.approach,
      r.correctness,
      r.sample_walkthrough,
      r.pitfalls,
      r.alternative_approaches,
      r.limitations,
      r.difficulty_assessment.evidence,
      r.difficulty_assessment.limitations,
    ].every((v) => Array.isArray(v) && v.every((s) => typeof s === 'string'))
  )
    throw new Error('报告内容不完整，请稍后重试')
  return value
}

function Content({ value }: { value: Report }) {
  const report = value.report
  const sections: [string, string[]][] = [
    ['难度评分依据', report.difficulty_assessment.evidence],
    ['选题适配', [report.topic_fit]],
    ['题目说明', [report.problem_explanation.description]],
    ['输入', [report.problem_explanation.input_format]],
    ['输出', [report.problem_explanation.output_format]],
    ['题意备注', report.problem_explanation.notes],
    ['问题概述', [report.summary]],
    ['约束', report.constraints],
    ['解题思路', report.approach],
    ['正确性说明', report.correctness],
    ['复杂度', [report.complexity]],
    ['样例分析', report.sample_walkthrough],
    ['实现与边界', report.pitfalls],
    ['其他思路', report.alternative_approaches],
    ['限制与不确定性', [...report.difficulty_assessment.limitations, ...report.limitations]],
  ]
  return (
    <div className="selection-report-content">
      <p className="help">
        {value.model} · {date(value.created_at)} · AI 推测评分{' '}
        {report.difficulty_assessment.rating ?? '无法确定'}（
        {report.difficulty_assessment.confidence}）
      </p>
      {!report.suitable && (
        <p className="help">此报告未被评为适合生成时的训练条件；题解仍可作为参考。</p>
      )}
      {sections.map(([title, paragraphs]) => (
        <section key={title}>
          <h3>{title}</h3>
          {paragraphs.length ? (
            paragraphs.map((text, i) => (
              <Markdown key={i} remarkPlugins={[remarkGfm]}>
                {text}
              </Markdown>
            ))
          ) : (
            <p>暂无补充。</p>
          )}
        </section>
      ))}
    </div>
  )
}

/** Lists metadata only; solution text is fetched after an explicit spoiler confirmation. */
export function ProblemReports({ problemId }: { problemId: string }) {
  return <ProblemReportsView key={problemId} problemId={problemId} />
}

function ProblemReportsView({ problemId }: { problemId: string }) {
  const [listing, setListing] = useState<{ pid: string; items: Summary[]; error?: string }>()
  const [reload, setReload] = useState(0)
  const [phase, setPhase] = useState<'closed' | 'confirm' | 'loading' | 'ready' | 'error'>('closed')
  const [selected, setSelected] = useState('')
  const [report, setReport] = useState<Report>()
  const [error, setError] = useState('')
  const modal = useRef<HTMLDialogElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const sequence = useRef(0)
  const busy = useRef(false)
  const id = useId()
  const pid = useRef(problemId)
  pid.current = problemId
  useEffect(() => {
    let active = true
    sequence.current++
    busy.current = false
    setPhase('closed')
    setReport(undefined)
    api<{ problem_id: string; items: Summary[] }>(
      `/problem-reports?problem_id=${encodeURIComponent(problemId)}`,
    )
      .then((value) => {
        if (!active) return
        if (
          value.problem_id !== problemId ||
          !Array.isArray(value.items) ||
          value.items.some((item) => item.problem_id !== problemId || typeof item.id !== 'string')
        )
          throw new Error('共享报告目录与题目不匹配')
        setListing({ pid: problemId, items: value.items })
        setSelected(value.items[0]?.id || '')
      })
      .catch((err) => {
        if (active)
          setListing({
            pid: problemId,
            items: [],
            error: err instanceof Error ? err.message : '共享报告读取失败',
          })
      })
    return () => {
      active = false
      sequence.current++
      busy.current = false
    }
  }, [problemId, reload])
  const close = () => {
    sequence.current++
    busy.current = false
    setPhase('closed')
    setReport(undefined)
  }
  const items = listing?.pid === problemId ? listing.items : []
  const opened = phase !== 'closed'
  useEffect(() => {
    if (!opened || !modal.current) return
    const dialog = modal.current
    dialog.showModal()
    heading.current?.focus()
    const overflow = document.documentElement.style.overflow
    document.documentElement.style.overflow = 'hidden'
    return () => {
      document.documentElement.style.overflow = overflow
      if (dialog.open) dialog.close()
      trigger.current?.focus()
    }
  }, [opened])
  const read = async () => {
    if (busy.current || !selected || !['confirm', 'error'].includes(phase)) return
    const token = ++sequence.current,
      current = problemId,
      rid = selected
    busy.current = true
    setPhase('loading')
    setError('')
    try {
      const value = await api<Report>(
        `/problem-reports/${encodeURIComponent(rid)}?problem_id=${encodeURIComponent(current)}`,
      )
      if (token !== sequence.current || pid.current !== current) return
      setReport(validate(value, current, rid))
      setPhase('ready')
    } catch (err) {
      if (token === sequence.current && pid.current === current) {
        setError(err instanceof Error ? err.message : '报告读取失败')
        setPhase('error')
      }
    } finally {
      if (token === sequence.current) busy.current = false
    }
  }
  return (
    <div className="selection-report-entry">
      {items.length > 0 && (
        <select
          aria-label="选择共享题解报告"
          value={selected}
          onChange={(e) => {
            close()
            setSelected(e.target.value)
          }}
        >
          {items.map((item) => (
            <option key={item.id} value={item.id}>
              {item.model} · AI评分 {item.difficulty_assessment?.rating ?? '无法确定'} ·{' '}
              {date(item.created_at)}
            </option>
          ))}
        </select>
      )}
      <button
        type="button"
        className="secondary"
        ref={trigger}
        disabled={!selected || !items.length}
        aria-haspopup="dialog"
        onClick={() => setPhase('confirm')}
      >
        <FileText size={15} />
        共享题解报告{items.length ? `（${items.length}）` : ''}
      </button>
      {listing?.pid === problemId && !items.length && (
        <span className="help">{listing.error || '暂无与当前题面对应的共享报告'}</span>
      )}
      <button
        type="button"
        className="text-button"
        onClick={() => setReload((v) => v + 1)}
        aria-label="刷新共享报告目录"
      >
        刷新
      </button>
      {opened &&
        createPortal(
          <dialog
            className="selection-report-dialog"
            ref={modal}
            aria-labelledby={`${id}-title`}
            onCancel={(e) => {
              e.preventDefault()
              close()
            }}
          >
            <div className="selection-report-header">
              <h2 id={`${id}-title`} ref={heading} tabIndex={-1}>
                共享 AI 题解报告
              </h2>
              <button
                type="button"
                className="text-button"
                aria-label="关闭共享报告"
                onClick={close}
              >
                <X size={18} />
              </button>
            </div>
            <p className="selection-report-notice">
              此报告由其他抽题任务生成并公开共享，可能有误，含解题思路；非官方解释，未执行或提交代码。
            </p>
            {phase === 'confirm' && (
              <>
                <p>此报告为ai生成的代码解析报告，你确定要现在看吗？</p>
                <div className="selection-report-actions">
                  <button type="button" className="secondary" onClick={close}>
                    取消
                  </button>
                  <button type="button" onClick={() => void read()}>
                    确定查看
                  </button>
                </div>
              </>
            )}
            {phase === 'loading' && <p role="status">正在读取已保存报告…</p>}
            {phase === 'error' && (
              <>
                <p role="alert">{error}</p>
                <button type="button" onClick={() => void read()}>
                  重试读取
                </button>
              </>
            )}
            {phase === 'ready' && report && <Content value={report} />}
            <p className="help">免费读取已保存报告，不调用 AI，不改变训练、提交或签到状态。</p>
          </dialog>,
          document.body,
        )}
    </div>
  )
}

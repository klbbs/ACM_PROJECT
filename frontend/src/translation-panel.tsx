import React, { useEffect, useRef, useState } from 'react'
import { Languages, RefreshCw, Upload } from 'lucide-react'
import { api, date } from './api'

const StatementView = React.lazy(() =>
  import('./statement-view').then((m) => ({ default: m.StatementView })),
)
type Row = Record<string, any>
type Props = {
  problemId: string
  statement: Row
  provider: string
  model: string
  revision: number
  jobs: Row[]
  run: (path: string, payload?: Row) => Promise<any>
  onError: (message: string) => void
  children: React.ReactNode
}
const statusNames: Record<string, string> = {
  private: '临时译文（30 分钟）',
  pending: '等待管理员审核',
  approved: '审核已通过',
  rejected: '审核未通过',
}
const engineNames: Record<string, string> = { ai: 'AI', google: 'Google 翻译', bing: 'Bing 翻译' }
function provenance(item: Row) {
  if (typeof item.provenance === 'string') return item.provenance
  return [item.provenance?.vendor || item.provenance?.provider, item.model]
    .filter(Boolean)
    .join(' · ')
}
export function TranslationStatement({ statement }: { statement: Row }) {
  return (
    <>
      <React.Suspense fallback={<p>正在排版译文…</p>}>
        <StatementView statement={statement} />
      </React.Suspense>
      {(statement.samples || []).map((sample: Row, index: number) => (
        <div className="wb-samples" key={index}>
          <div>
            <h4>样例输入 {index + 1}</h4>
            <pre>{sample.input}</pre>
          </div>
          <div>
            <h4>样例输出 {index + 1}</h4>
            <pre>{sample.output}</pre>
          </div>
        </div>
      ))}
    </>
  )
}

export function TranslationPanel({
  problemId,
  statement,
  provider,
  model,
  revision,
  jobs,
  run,
  onError,
  children,
}: Props) {
  const [data, setData] = useState<Row | null>(null),
    [selected, setSelected] = useState('')
  const [engine, setEngine] = useState('ai')
  const disclosureId = React.useId()
  const [busy, setBusy] = useState(false),
    [notice, setNotice] = useState('')
  const [translationError, setTranslationError] = useState('')
  const [generationJobId, setGenerationJobId] = useState('')
  const [reviews, setReviews] = useState<Row[] | null>(null),
    [reviewNotes, setReviewNotes] = useState<Record<string, string>>({})
  const pendingGeneration = useRef<{ engine: string; model: string; before: Set<string> } | null>(
    null,
  )
  const active = jobs.some(
    (job) => job.kind === 'translation' && ['running', 'queued'].includes(job.status),
  )
  const fail = (error: unknown) => onError(error instanceof Error ? error.message : '翻译请求失败')
  useEffect(() => {
    pendingGeneration.current = null
    setGenerationJobId('')
    setTranslationError('')
    setNotice('')
    setSelected('')
    setData(null)
  }, [problemId])
  useEffect(() => {
    let alive = true
    api(`/workbench/problem/${encodeURIComponent(problemId)}/translations`)
      .then((result) => {
        if (!alive) return
        setData(result)
        const wanted = pendingGeneration.current
        if (wanted) {
          const added = result.items.find(
            (item: Row) =>
              !wanted.before.has(item.id) &&
              item.engine === wanted.engine &&
              (wanted.engine !== 'ai' || item.model === wanted.model),
          )
          if (added) {
            setSelected(added.id)
            pendingGeneration.current = null
            setNotice('译文仅在服务器内存暂存 30 分钟，服务重启后清除；需要共享时请及时上传审核。')
          }
        }
        setSelected((id) => (result.items.some((item: Row) => item.id === id) ? id : ''))
      })
      .catch((error) => {
        if (alive) fail(error)
      })
    return () => {
      alive = false
    }
  }, [problemId, revision, statement])
  useEffect(() => {
    if (!generationJobId) return
    const job = jobs.find((item) => item.id === generationJobId && item.kind === 'translation')
    if (job?.status === 'failed' || job?.status === 'interrupted') {
      pendingGeneration.current = null
      setTranslationError(job.error || '翻译任务中断，请检查连接后重新发起。')
    } else if (
      job?.status === 'completed' &&
      data?.items.some((item: Row) => item.id === job.result?.translation_id)
    ) {
      setSelected(job.result.translation_id)
      pendingGeneration.current = null
      setGenerationJobId('')
      setNotice('翻译已完成，可在下方切换阅读；个人译文仅在内存暂存 30 分钟。')
    }
  }, [jobs, data, generationJobId])
  const reload = async () => {
    const result = await api(`/workbench/problem/${encodeURIComponent(problemId)}/translations`)
    setData(result)
    return result
  }
  const current: Row | undefined = data?.items.find((item: Row) => item.id === selected)
  const configured = engine === 'ai' || data?.services?.[engine]?.configured
  const refreshReviews = async () => {
    const result = await api('/translations/review?status=pending')
    setReviews(result.items)
  }
  const generate = async () => {
    if (busy || active || !configured || (engine === 'ai' && !model)) return
    setBusy(true)
    setNotice('')
    setTranslationError('')
    setGenerationJobId('')
    const pending = {
      engine,
      model,
      before: new Set<string>((data?.items || []).map((item: Row) => item.id)),
    }
    pendingGeneration.current = pending
    try {
      const response = await run(`/workbench/problem/${encodeURIComponent(problemId)}/translate`, {
        engine,
        provider,
        model: engine === 'ai' ? model : '',
        consent: true,
      })
      if (pendingGeneration.current !== pending) return
      if (response?.error) throw new Error(response.error)
      setGenerationJobId(response?.id || '')
    } catch (error) {
      if (pendingGeneration.current !== pending) return
      pendingGeneration.current = null
      setTranslationError(error instanceof Error ? error.message : '翻译请求失败')
      fail(error)
    } finally {
      setBusy(false)
    }
  }
  const upload = async () => {
    if (!current) return
    setBusy(true)
    setNotice('')
    try {
      await api(`/translations/${encodeURIComponent(current.id)}/upload`, 'POST', {})
      await reload()
      setNotice('已上传，等待管理员审核；通过前不会公开给其他用户。')
    } catch (error) {
      fail(error)
    } finally {
      setBusy(false)
    }
  }
  const review = async (item: Row, decision: string) => {
    setBusy(true)
    try {
      await api(`/translations/${encodeURIComponent(item.id)}/review`, 'POST', {
        decision,
        note: reviewNotes[item.id] || '',
      })
      await refreshReviews()
      await reload()
      setNotice(
        decision === 'approved' ? '译文已审核通过，可供其他用户免费选用。' : '已拒绝该译文。',
      )
    } catch (error) {
      fail(error)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="translation-panel">
      <details className="translation-controls">
        <summary>
          <Languages size={16} /> 题面翻译与共享译文{' '}
          <span className="muted">
            {data?.items.filter((item: Row) => item.scope === 'shared').length || 0} 份免费共享
          </span>
        </summary>
        <p className="help">
          选择已有共享译文不会调用 AI。新翻译仅发送本题题面，不发送你的代码、提交记录或平台
          Cookie。个人译文仅在内存暂存 30 分钟，服务重启后清除；仅主动上传的共享译文持久保存。
        </p>
        <div className="translation-form">
          <label>
            翻译方式
            <select value={engine} onChange={(event) => setEngine(event.target.value)}>
              <option value="ai">AI 翻译</option>
              <option value="google">Google 翻译</option>
              <option value="bing">Bing 翻译</option>
            </select>
          </label>
          {engine === 'ai' ? (
            <p className="translation-model">
              当前 AI：{provider === 'codex' ? '官方登录通道' : 'API 通道'} ·{' '}
              <strong>{model || '尚未选择模型'}</strong>
              <br />
              <span className="muted">在下方 AI 选择器选择模型，或到连接与设置中配置。</span>
            </p>
          ) : (
            <p className="translation-model">
              {configured
                ? '服务器已配置官方翻译服务。'
                : '服务器尚未配置此翻译服务；可打开官方网页翻译，或复制题面到官方页面。'}
              <br />
              普通翻译不会调用 AI。
            </p>
          )}
        </div>
        {configured ? (
          <>
            <p className="help" id={disclosureId}>
              {engine === 'ai'
                ? '点击“翻译题面（AI）”会将本题题面发送给所选 AI，可能分段调用并计费。'
                : `点击翻译会将本题题面发送给 ${engineNames[engine]} 官方服务，可能按服务规则计费；不会调用 AI。`}
            </p>
            <button
              aria-describedby={disclosureId}
              disabled={busy || active || (engine === 'ai' && !model)}
              onClick={generate}
            >
              翻译题面{engine === 'ai' ? '（AI）' : `（${engineNames[engine]}）`}
            </button>
          </>
        ) : (
          <div className="translation-actions">
            {data?.links?.[engine] && (
              <a href={data.links[engine]} target="_blank" rel="noreferrer">
                打开 {engineNames[engine]} 官方网页 ↗
              </a>
            )}
            <button
              className="secondary"
              onClick={async () => {
                try {
                  const { statementMarkdown } = await import('./statement-view')
                  await navigator.clipboard.writeText(statementMarkdown(statement))
                  setNotice('题面已复制，请粘贴到官方翻译页面。')
                } catch {
                  onError('无法复制，请选择题面文本后手动复制。')
                }
              }}
            >
              复制题面
            </button>
          </div>
        )}
        {active && <p role="status">题面翻译正在进行，可继续编码；完成后仅在内存暂存 30 分钟。</p>}
        {translationError && <p role="alert">{translationError} 原题和已有译文仍可阅读。</p>}
        {!data?.shareable && (
          <p className="help">手动导入等未经核验的题面仅支持个人翻译，不接受共享上传。</p>
        )}
        <p className="help">
          每个题面版本、每个模型仅接受一份审核通过的 AI
          译文。上传前请检查公式、配图和样例；后台审核通过后才公开。
        </p>
        {data?.can_review && (
          <details
            className="translation-review"
            onToggle={(event) => {
              if (event.currentTarget.open && reviews === null) refreshReviews().catch(fail)
            }}
          >
            <summary>管理员：共享译文审核</summary>
            <button
              className="secondary"
              disabled={busy}
              onClick={() => refreshReviews().catch(fail)}
            >
              <RefreshCw size={14} /> 刷新待审译文
            </button>
            {reviews?.length === 0 && <p>暂无待审核译文。</p>}
            {reviews?.map((item) => (
              <article className="translation-review-item" key={item.id}>
                <h4>
                  {item.problem_id} · {item.model}
                </h4>
                <p className="muted">
                  {provenance(item)} · 上传于 {date(item.submitted_at)}
                </p>
                <details>
                  <summary>查看原题对照</summary>
                  <TranslationStatement statement={item.source_statement} />
                </details>
                <details>
                  <summary>查看待审核译文</summary>
                  <TranslationStatement statement={item.statement} />
                </details>
                <label>
                  审核理由
                  <textarea
                    maxLength={1000}
                    value={reviewNotes[item.id] || ''}
                    onChange={(event) =>
                      setReviewNotes((notes) => ({ ...notes, [item.id]: event.target.value }))
                    }
                    placeholder="说明译文质量、错误或拒绝原因"
                  />
                </label>
                <div className="translation-actions">
                  <button disabled={busy} onClick={() => review(item, 'approved')}>
                    批准共享译文
                  </button>
                  <button
                    className="secondary"
                    disabled={busy || !reviewNotes[item.id]?.trim()}
                    onClick={() => review(item, 'rejected')}
                  >
                    拒绝共享译文
                  </button>
                </div>
              </article>
            ))}
          </details>
        )}
      </details>
      <label className="translation-selection">
        阅读内容
        <select
          aria-label="阅读原题或译文"
          value={selected}
          onChange={(event) => setSelected(event.target.value)}
        >
          <option value="">原题</option>
          {(data?.items || []).map((item: Row) => (
            <option key={item.id} value={item.id}>
              {item.scope === 'shared' ? '免费共享' : '临时个人译文'} ·{' '}
              {item.model || engineNames[item.engine]} · {statusNames[item.status] || item.status}
            </option>
          ))}
        </select>
      </label>
      {notice && (
        <p className="help" role="status">
          {notice}
        </p>
      )}
      {current ? (
        <div className="translation-result">
          <div className="translation-result-meta">
            <span>
              {current.scope === 'shared' ? '免费共享译文 · 不调用 AI' : '临时个人译文'} ·{' '}
              {engineNames[current.engine]} · {provenance(current)} · {statusNames[current.status]}
            </span>
            {current.engine === 'ai' &&
              current.scope === 'private' &&
              current.shareable &&
              ['private', 'rejected'].includes(current.status) && (
                <button className="secondary" disabled={busy} onClick={upload}>
                  <Upload size={14} /> 上传翻译供审核
                </button>
              )}
          </div>
          {current.review_note && <p className="help">审核意见：{current.review_note}</p>}
          <h3>{current.statement.title}</h3>
          <TranslationStatement statement={current.statement} />
        </div>
      ) : (
        children
      )}
    </div>
  )
}

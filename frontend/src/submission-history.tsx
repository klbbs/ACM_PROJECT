import { useEffect, useRef, useState } from 'react'
import { api, date } from './api'
type Row = Record<string, any>
export function SubmissionHistory({ problemId, revision, selected, onError }: any) {
  const selectedRow = useRef<HTMLElement | null>(null)
  const [items, setItems] = useState<Row[]>([]),
    [detail, setDetail] = useState<Row | null>(null),
    [loading, setLoading] = useState(true)
  useEffect(() => {
    let alive = true
    setLoading(true)
    setDetail(null)
    api(`/workbench/problem/${encodeURIComponent(problemId)}/history`)
      .then((v) => {
        if (alive) setItems(v.items)
      })
      .catch((e: Error) => {
        if (alive) onError(e.message)
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [problemId, revision])
  useEffect(() => {
    selectedRow.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [items, selected])
  return (
    <section className="card">
      <h3>本题历史提交</h3>
      {loading ? (
        <p>正在读取历史记录…</p>
      ) : !items.length ? (
        <p>暂无历史提交；同步平台账号后可在这里查看。</p>
      ) : (
        items.map((r) => (
          <article
            className={`wb-attempt ${selected && String(r.submission_id) === String(selected.submission_id) && r.account === selected.account ? 'history-selected' : ''}`}
            ref={
              selected &&
              String(r.submission_id) === String(selected.submission_id) &&
              r.account === selected.account
                ? selectedRow
                : undefined
            }
            key={`${r.account}:${r.id}`}
          >
            <strong>
              {r.submission_id ? `#${r.submission_id}` : '尚未关联平台提交'} · {r.verdict}
            </strong>
            <p>
              {r.account} · {date(r.submitted_at)} · {r.language || '语言未记录'}
            </p>
            {r.has_local_code && r.attempt_id ? (
              <button
                onClick={async () => {
                  try {
                    setDetail({
                      ...(await api(`/workbench/attempt/${r.attempt_id}`)),
                      review: r.review,
                    })
                  } catch (e: any) {
                    onError(e.message)
                  }
                }}
              >
                查看当次代码与复盘
              </button>
            ) : (
              <p className="muted">
                仅采集到平台判题记录，未保存当次源码。当前编辑器内容不是这次历史提交的代码。
              </p>
            )}
          </article>
        ))
      )}
      {detail && (
        <div className="wb-history-detail">
          <h4>当次提交代码（只读）</h4>
          <p>此代码由用户在关联时提供，未与平台源码核对；不会覆盖当前草稿。</p>
          <pre className="wb-snapshot">{detail.code}</pre>
          {detail.review && (
            <p>
              {detail.review.evidence} {detail.review.question}
            </p>
          )}
          <button onClick={() => setDetail(null)}>关闭当次代码</button>
        </div>
      )}
    </section>
  )
}
export function SubmissionForm({ handles, code, language, active, onSubmit }: any) {
  const [handle, setHandle] = useState(''),
    [sid, setSid] = useState(''),
    [confirmed, setConfirmed] = useState(false)
  useEffect(() => setConfirmed(false), [code, language])
  const value = handles.includes(handle) ? handle : handles[0] || ''
  return (
    <form
      className="wb-submission-form"
      onSubmit={(e) => {
        e.preventDefault()
        if (confirmed)
          onSubmit({ handle: value, submission_id: Number(sid), code_matches_submission: true })
      }}
    >
      <h3>关联本次提交结果</h3>
      <p>先复制代码并在官方页面提交，再填提交号。无需额外保存操作。</p>
      <label>
        提交账号
        <select aria-label="本次提交账号" value={value} onChange={(e) => setHandle(e.target.value)}>
          <option value="">选择已绑定账号</option>
          {handles.map((h: string) => (
            <option key={h} value={h}>
              {h}
            </option>
          ))}
        </select>
      </label>
      <label>
        平台提交号
        <input
          aria-label="本次平台提交号"
          inputMode="numeric"
          value={sid}
          onChange={(e) => setSid(e.target.value)}
          placeholder="官方平台返回的数字提交号"
        />
      </label>
      <label>
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(e) => setConfirmed(e.target.checked)}
        />
        我确认本次官方提交使用了当前编辑器中的代码；这不是为历史记录补填代码。
      </label>
      <button
        disabled={
          active || !value || !/^\d+$/.test(sid) || Number(sid) <= 0 || !code.trim() || !confirmed
        }
      >
        关联并核验本次提交
      </button>
    </form>
  )
}

import { useEffect, useRef, useState } from 'react'
import { api, date, names } from './api'
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
export function parseSubmissionId(input: string, platform: string): number | null {
  const text = input.trim()
  let digits = /^\d+$/.test(text) ? text : platform === 'luogu' ? /^R(\d+)$/i.exec(text)?.[1] : null
  if (!digits) {
    try {
      const url = new URL(text)
      if (url.protocol !== 'https:' || url.username || url.password || url.port || url.hash)
        return null
      if (
        platform === 'codeforces' &&
        ['codeforces.com', 'www.codeforces.com'].includes(url.hostname) &&
        !url.search
      )
        digits =
          /^\/(?:contest|gym)\/\d+\/submission\/(\d+)\/?$/.exec(url.pathname)?.[1] ||
          /^\/problemset\/submission\/\d+\/(\d+)\/?$/.exec(url.pathname)?.[1]
      if (platform === 'atcoder' && url.hostname === 'atcoder.jp' && !url.search)
        digits = /^\/contests\/[A-Za-z0-9_-]+\/submissions\/(\d+)\/?$/.exec(url.pathname)?.[1]
      if (
        platform === 'luogu' &&
        ['www.luogu.com.cn', 'luogu.com.cn'].includes(url.hostname) &&
        !url.search
      )
        digits = /^\/record\/(\d+)\/?$/.exec(url.pathname)?.[1]
      if (
        platform === 'nowcoder' &&
        url.hostname === 'ac.nowcoder.com' &&
        url.pathname === '/acm/contest/view-submission' &&
        [...url.searchParams.keys()].length === 1
      )
        digits = url.searchParams.get('submissionId')
    } catch {
      return null
    }
  }
  const value = digits && /^\d+$/.test(digits) ? Number(digits) : 0
  return Number.isSafeInteger(value) && value > 0 && value <= 10 ** 12 ? value : null
}

export function SubmissionForm({
  handles,
  code,
  language,
  active,
  onSubmit,
  platform = 'codeforces',
  onRefreshAccounts,
  onSyncPlatform,
  onError,
}: any) {
  const [handle, setHandle] = useState(''),
    [sid, setSid] = useState(''),
    [confirmed, setConfirmed] = useState(false),
    [pending, setPending] = useState(false),
    [message, setMessage] = useState('')
  const value = handles.includes(handle) ? handle : handles[0] || ''
  const submissionId = parseSubmissionId(sid, platform)
  useEffect(() => setConfirmed(false), [code, language, value, sid])
  const reason = !value
    ? '尚未读取到此平台账号，请先添加账号，或点击“刷新已配置账号”。'
    : !sid.trim()
      ? '请填写本次官方提交号或提交记录链接。'
      : !submissionId
        ? `提交格式无效，请填写 ${names[platform] || platform} 的数字提交号或官方提交记录链接。`
        : !code.trim()
          ? '请在编辑器中保留本次官方提交使用的代码。'
          : !confirmed
            ? '请确认官方提交使用了当前编辑器中的代码。'
            : ''
  const perform = async (action: () => Promise<unknown>) => {
    setPending(true)
    setMessage('')
    try {
      await action()
    } catch (error) {
      const text = error instanceof Error ? error.message : '操作失败，请重试'
      setMessage(text)
      onError?.(text)
    } finally {
      setPending(false)
    }
  }
  return (
    <form
      className="wb-submission-form"
      onSubmit={(e) => {
        e.preventDefault()
        if (!active && !pending && !reason && submissionId)
          void perform(() =>
            onSubmit({ handle: value, submission_id: submissionId, code_matches_submission: true }),
          )
      }}
    >
      <h3>关联本次提交结果</h3>
      <p>
        先复制代码并在 {names[platform] || platform}{' '}
        官方页面提交，再填提交号或记录链接。关联结果不会代你交题。
      </p>
      {platform !== 'codeforces' && (
        <p className="muted">
          此平台需要先同步账号提交记录，再关联本题的本次提交；仅保存账号或 Cookie
          不会自动获取判题结果。
        </p>
      )}
      {platform === 'nowcoder' && (
        <p className="muted">
          若采集器尚未覆盖本场比赛或未采集到此提交，暂时无法关联，请在牛客官方记录页查看结果。
        </p>
      )}
      <div className="actions">
        {onRefreshAccounts && (
          <button
            type="button"
            className="secondary"
            disabled={active || pending}
            onClick={() => void perform(onRefreshAccounts)}
          >
            刷新已配置账号
          </button>
        )}
        {platform !== 'codeforces' && onSyncPlatform && (
          <button
            type="button"
            className="secondary"
            disabled={active || pending || !handles.length}
            onClick={() => void perform(onSyncPlatform)}
          >
            同步此平台提交记录
          </button>
        )}
        {!handles.length && (
          <button
            type="button"
            className="text-button"
            onClick={() => window.dispatchEvent(new Event('acm:open-guide'))}
          >
            添加平台账号
          </button>
        )}
      </div>
      <label>
        提交账号
        <select aria-label="本次提交账号" value={value} onChange={(e) => setHandle(e.target.value)}>
          <option value="">选择已配置账号</option>
          {handles.map((h: string) => (
            <option key={h} value={h}>
              {h}
            </option>
          ))}
        </select>
      </label>
      <label>
        平台提交号或官方记录链接
        <input
          aria-label="本次平台提交号"
          value={sid}
          onChange={(e) => setSid(e.target.value)}
          placeholder={
            platform === 'luogu'
              ? '数字提交号、R123 或官方提交记录链接'
              : '数字提交号或官方提交记录链接'
          }
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
      <p role="status" className="muted">
        {message ||
          (active || pending
            ? '正在处理，请稍候…'
            : reason ||
              `将核验 ${names[platform] || platform} 提交 #${submissionId} 的账号、题号和时间。`)}
      </p>
      <button disabled={active || pending || !!reason}>关联并核验本次提交</button>
    </form>
  )
}

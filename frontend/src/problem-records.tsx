import { useEffect, useRef, useState } from 'react'
import { Bookmark, BookOpen, Search, StickyNote } from 'lucide-react'
import { api, ApiError, date, names } from './api'
import './problem-records.css'

type RecordData = {
  problem_id: string
  title: string
  url: string
  platform: string
  favorite: boolean
  note: string
  revision: number
  updated_at: number | null
}
type RecordList = { items: RecordData[]; total: number }
const recordPath = (id: string) => `/problem-records/${encodeURIComponent(id)}`
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : '保存未完成，请稍后重试'

export function ProblemRecordEditor({ problemId, title }: { problemId: string; title?: string }) {
  const [record, setRecord] = useState<RecordData | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [conflict, setConflict] = useState(false)
  const [loading, setLoading] = useState(true)
  const [reload, setReload] = useState(0)
  const activeProblem = useRef(problemId)
  activeProblem.current = problemId
  const dirty = record !== null && note !== record.note

  useEffect(() => {
    let current = true
    setLoading(true)
    setBusy(false)
    setRecord(null)
    setNote('')
    setError('')
    setStatus('')
    setConflict(false)
    api<RecordData>(recordPath(problemId))
      .then((data) => {
        if (current) {
          setRecord(data)
          setNote(data.note)
        }
      })
      .catch((error) => {
        if (current) setError(errorText(error))
      })
      .finally(() => {
        if (current) setLoading(false)
      })
    return () => {
      current = false
    }
  }, [problemId, reload])

  useEffect(() => {
    const guard = (event: Event) => {
      if (!dirty && !busy) return
      event.preventDefault()
      setError(
        busy
          ? '正在保存题目记录，请稍候再切换'
          : '备注尚未保存，请先保存或点击“放弃本次修改”再切换',
      )
    }
    const unload = (event: BeforeUnloadEvent) => {
      if (dirty || busy) {
        event.preventDefault()
        event.returnValue = ''
      }
    }
    window.addEventListener('acm:before-navigate', guard)
    window.addEventListener('acm:before-problem-change', guard)
    window.addEventListener('beforeunload', unload)
    return () => {
      window.removeEventListener('acm:before-navigate', guard)
      window.removeEventListener('acm:before-problem-change', guard)
      window.removeEventListener('beforeunload', unload)
    }
  }, [dirty, busy])

  async function save(kind: 'favorite' | 'note') {
    if (!record || busy) return
    const id = problemId
    setBusy(true)
    setError('')
    setStatus('')
    try {
      const data = await api<RecordData>(recordPath(id), 'PUT', {
        revision: record.revision,
        ...(kind === 'favorite' ? { favorite: !record.favorite } : { note }),
      })
      if (activeProblem.current !== id) return
      setRecord(data)
      // A bookmark operation only changes the bookmark; preserve unsaved note text.
      if (kind === 'note') setNote(data.note)
      setConflict(false)
      setStatus(
        kind === 'note' ? '备注已保存' : data.favorite ? '已加入收藏' : '已取消收藏，备注仍保留',
      )
      window.dispatchEvent(new Event('acm:problem-records-updated'))
    } catch (error) {
      if (activeProblem.current === id) {
        setError(errorText(error))
        setConflict(error instanceof ApiError && error.status === 409)
      }
    } finally {
      if (activeProblem.current === id) setBusy(false)
    }
  }

  return (
    <section className="problem-record-editor" aria-label="题目收藏与备注">
      <div className="problem-record-heading">
        <h3>
          <StickyNote size={18} />
          收藏与备注
        </h3>
        <button
          type="button"
          disabled={!record || busy || conflict}
          aria-pressed={record?.favorite || false}
          className={record?.favorite ? 'record-favorite selected' : 'record-favorite'}
          onClick={() => void save('favorite')}
        >
          <Bookmark size={16} fill={record?.favorite ? 'currentColor' : 'none'} />
          {record?.favorite ? '已收藏' : '收藏本题'}
        </button>
      </div>
      <p className="muted">仅你可见 · {title || record?.title || problemId}</p>
      {loading ? (
        <p role="status">正在读取题目记录…</p>
      ) : (
        <>
          <label className="problem-record-note">
            我的备注
            <textarea
              aria-label="我的题目备注"
              value={note}
              maxLength={20000}
              disabled={!record || busy}
              rows={4}
              placeholder="记录思路、易错点，或为什么这道题值得再做一次…"
              onChange={(event) => {
                setNote(event.target.value)
                setStatus('')
              }}
            />
          </label>
          <div className="problem-record-actions">
            <button
              type="button"
              disabled={!dirty || busy || conflict}
              onClick={() => void save('note')}
            >
              {busy ? '保存中…' : '保存备注'}
            </button>
            {dirty && (
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setNote(record?.note || '')
                  setError('')
                  setStatus('已放弃本次修改')
                }}
              >
                放弃本次修改
              </button>
            )}
            <small className="muted">
              {note.length.toLocaleString()} / 20,000{dirty ? ' · 未保存' : ''}
            </small>
          </div>
        </>
      )}
      {error && (
        <p role="alert" className="problem-record-error">
          {error}
        </p>
      )}
      {status && (
        <p role="status" className="problem-record-success">
          {status}
        </p>
      )}
      {(conflict || (!loading && !record)) && (
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            if (!dirty || window.confirm('重新载入会替换当前未保存的备注，是否继续？'))
              setReload((value) => value + 1)
          }}
        >
          重新载入记录{dirty ? '（替换当前输入）' : ''}
        </button>
      )}
    </section>
  )
}

export function ProblemRecordsList({ onOpenProblem }: { onOpenProblem: (id: string) => void }) {
  const [search, setSearch] = useState('')
  const [favoritesOnly, setFavoritesOnly] = useState(false)
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState<RecordList>({ items: [], total: 0 })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    const refresh = () => setRevision((value) => value + 1)
    window.addEventListener('acm:problem-records-updated', refresh)
    return () => window.removeEventListener('acm:problem-records-updated', refresh)
  }, [])
  useEffect(() => {
    let current = true
    setLoading(true)
    setError('')
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({
        search,
        favorites_only: String(favoritesOnly),
        offset: String(offset),
        limit: '30',
      })
      api<RecordList>(`/problem-records?${params}`)
        .then((result) => {
          if (current) setData(result)
        })
        .catch((error) => {
          if (current) {
            setError(errorText(error))
            setData({ items: [], total: 0 })
          }
        })
        .finally(() => {
          if (current) setLoading(false)
        })
    }, 150)
    return () => {
      current = false
      window.clearTimeout(timer)
    }
  }, [search, favoritesOnly, offset, revision])
  return (
    <section className="problem-records-page" aria-label="收藏与备注记录">
      <h2>
        <BookOpen size={24} />
        收藏与备注
      </h2>
      <p className="muted">你的私人题目记录。收藏好题、记下思考，随时回到工作台继续练习。</p>
      <div className="problem-record-filters">
        <label>
          <Search size={18} />
          <input
            aria-label="搜索题目记录"
            value={search}
            maxLength={200}
            placeholder="搜索题号、标题或备注"
            onChange={(event) => {
              setSearch(event.target.value)
              setOffset(0)
            }}
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={favoritesOnly}
            onChange={(event) => {
              setFavoritesOnly(event.target.checked)
              setOffset(0)
            }}
          />
          只看收藏
        </label>
      </div>
      {error && (
        <p role="alert" className="problem-record-error">
          {error}{' '}
          <button type="button" onClick={() => setRevision((value) => value + 1)}>
            重试
          </button>
        </p>
      )}
      {loading ? (
        <p role="status">正在读取记录…</p>
      ) : (
        !error && (
          <>
            <p className="muted">共 {data.total} 条记录</p>
            {!data.items.length && (
              <p>暂无记录。打开工作台的题目，点击收藏或保存备注后会显示在这里。</p>
            )}
            <div className="problem-record-list">
              {data.items.map((item) => (
                <article key={item.problem_id}>
                  <div className="problem-record-heading">
                    <h3>
                      {item.favorite && (
                        <Bookmark size={16} fill="currentColor" aria-label="已收藏" />
                      )}
                      {item.title}
                    </h3>
                    <span className="muted">{names[item.platform] || item.platform}</span>
                  </div>
                  <p className="muted">
                    {item.problem_id} · {date(item.updated_at || undefined)}
                  </p>
                  {item.note && (
                    <p className="problem-record-preview">
                      {item.note.length > 400 ? `${item.note.slice(0, 400)}…` : item.note}
                    </p>
                  )}
                  <div className="problem-record-actions">
                    <button type="button" onClick={() => onOpenProblem(item.problem_id)}>
                      去工作台
                    </button>
                    <a href={item.url} target="_blank" rel="noopener noreferrer">
                      查看原题
                    </a>
                  </div>
                </article>
              ))}
            </div>
            {data.total > 30 && (
              <div className="problem-record-actions">
                <button
                  type="button"
                  disabled={offset === 0}
                  onClick={() => setOffset((value) => Math.max(0, value - 30))}
                >
                  上一页
                </button>
                <span>
                  {Math.floor(offset / 30) + 1} / {Math.ceil(data.total / 30)}
                </span>
                <button
                  type="button"
                  disabled={offset + 30 >= data.total}
                  onClick={() => setOffset((value) => value + 30)}
                >
                  下一页
                </button>
              </div>
            )}
          </>
        )
      )}
    </section>
  )
}

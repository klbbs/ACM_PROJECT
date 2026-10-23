import { useCallback, useEffect, useRef, useState } from 'react'
import { FileText, RefreshCw } from 'lucide-react'
import { api, date } from './api'
import './system-logs.css'

type Log = {
  id: string
  timestamp: number
  level: string
  category: string
  event: string
  message: string
  request_id?: string
  details: Record<string, unknown>
}
type LogPage = {
  items: Log[]
  next_cursor: string | null
  has_more: boolean
  cursor_expired?: boolean
  storage: { available: boolean; error?: string; max_bytes: number; backup_count: number; file_count: number }
}
type Filters = { level: string; category: string; q: string }
type FailedRequest = { before?: string; key: string }
const categories: Record<string, string> = {
  system: '系统', task: '任务', platform: '平台', security: '安全', request: '请求',
}
const levels: Record<string, string> = { INFO: '信息', WARNING: '警告', ERROR: '错误' }
const detailLabels: Record<string, string> = {
  method: '请求方法', route: '请求路由', status: '响应状态', duration_ms: '耗时（毫秒）',
  job_id: '任务编号', kind: '任务类型', job_status: '任务状态', platform: '平台', reason: '原因',
  upstream_status: '平台响应状态', upstream_code: '平台响应代码', user_id: '用户编号',
  component: '组件', warning_count: '警告数量', finding_count: '发现问题数', mode: '模式',
}
const cursorPattern = /^[a-f0-9]{32}$/i
const megabytes = (bytes: number) => `${Number((bytes / 1024 / 1024).toFixed(1))} MB`

export function SystemLogs() {
  const [filters, setFilters] = useState<Filters>({ level: '', category: '', q: '' })
  const [query, setQuery] = useState('')
  const [page, setPage] = useState<LogPage | null>(null)
  const [pageKey, setPageKey] = useState('')
  const [loading, setLoading] = useState<'refresh' | 'more' | null>(null)
  const [error, setError] = useState('')
  const [failed, setFailed] = useState<FailedRequest | null>(null)
  const filterKey = JSON.stringify(filters)
  const currentKey = useRef(filterKey)
  currentKey.current = filterKey
  const mounted = useRef(false)
  const sequence = useRef(0)
  const inFlight = useRef<{ key: string; sequence: number } | null>(null)

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; ++sequence.current; inFlight.current = null }
  }, [])

  const load = useCallback(async (before?: string) => {
    if (!mounted.current || currentKey.current !== filterKey || inFlight.current?.key === filterKey) return
    const request = { key: filterKey, sequence: ++sequence.current }
    inFlight.current = request
    setLoading(before ? 'more' : 'refresh')
    setError('')
    setFailed(null)
    const params = new URLSearchParams({ limit: '50' })
    if (filters.level) params.set('level', filters.level)
    if (filters.category) params.set('category', filters.category)
    if (filters.q) params.set('q', filters.q)
    if (before) params.set('before', before)
    try {
      const result = await api<LogPage>(`/admin/logs?${params}`)
      if (!mounted.current || currentKey.current !== filterKey || request.sequence !== sequence.current) return
      setPage((previous) => {
        if (!before || !previous) return result
        const seen = new Set(previous.items.map((item) => item.id))
        return { ...result, items: [...previous.items, ...result.items.filter((item) => !seen.has(item.id))] }
      })
      setPageKey(filterKey)
    } catch (cause) {
      if (!mounted.current || currentKey.current !== filterKey || request.sequence !== sequence.current) return
      setError(cause instanceof Error ? cause.message : '日志读取失败，请稍后重试。')
      setFailed({ before, key: filterKey })
    } finally {
      if (inFlight.current === request) inFlight.current = null
      if (mounted.current && currentKey.current === filterKey && request.sequence === sequence.current) setLoading(null)
    }
  }, [filterKey, filters])

  useEffect(() => { void load() }, [load])
  const stale = Boolean(page && pageKey !== filterKey)
  const canLoadMore = !stale && page?.has_more && cursorPattern.test(page.next_cursor || '')
  return (
    <section className="card admin-card system-logs" aria-label="后台运行日志">
      <div className="card-heading">
        <div><h2><FileText size={18} /> 后台运行日志</h2>
          <p className="help">仅管理员可查看。记录运行事件及必要诊断信息；手动刷新获取新日志。</p></div>
        <button className="secondary" disabled={Boolean(loading)} onClick={() => void load()}>
          <RefreshCw size={15} /> {loading === 'refresh' ? '正在刷新日志…' : '刷新日志'}
        </button>
      </div>
      <form className="system-log-filters" onSubmit={(event) => {
        event.preventDefault()
        const next = query.trim()
        if (next === filters.q) void load()
        else setFilters((old) => ({ ...old, q: next }))
      }}>
        <label>日志级别<select value={filters.level} onChange={(event) => setFilters((old) => ({ ...old, level: event.target.value }))}>
          <option value="">全部级别</option>
          {Object.entries(levels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
        </select></label>
        <label>日志类别<select value={filters.category} onChange={(event) => setFilters((old) => ({ ...old, category: event.target.value }))}>
          <option value="">全部类别</option>
          {Object.entries(categories).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
        </select></label>
        <label>关键词<input type="search" maxLength={100} value={query} placeholder="搜索事件或说明"
          onChange={(event) => setQuery(event.target.value)} /></label>
        <button type="submit" className="secondary" disabled={Boolean(loading) && query.trim() === filters.q}>搜索日志</button>
      </form>
      {error && <div className="system-log-error" role="alert">
        <p>日志读取失败：{error}{page ? '。已有结果已保留。' : ''}</p>
        <button className="secondary" disabled={Boolean(loading)} onClick={() => void load(failed?.key === filterKey ? failed.before : undefined)}>重试读取日志</button>
      </div>}
      {loading && <p className="help" role="status">{loading === 'more' ? '正在读取更早的日志…' : '正在读取最新日志…'}</p>}
      {stale && <p className="help">当前保留上次筛选的结果；新筛选读取成功后会替换，旧结果不用于继续翻页。</p>}
      {page?.cursor_expired && <p className="help">分页对应的日志已轮转；当前仅显示仍保留的记录，可刷新查看最新日志。</p>}
      {page?.storage && <div className="system-log-storage">
        <p className="help">日志文件自动轮转：单文件上限 {megabytes(page.storage.max_bytes)}，保留 {page.storage.backup_count} 份备份，
          当前 {page.storage.file_count} 个文件；轮转空间预算约 {megabytes(page.storage.max_bytes * (page.storage.backup_count + 1))}。</p>
        {(!page.storage.available || page.storage.error) && <p className="system-log-error" role="alert">日志存储或写入异常，部分新事件可能未记录，请检查写入权限或磁盘空间。</p>}
      </div>}
      {!loading && !error && page?.items.length === 0 && <p className="help">没有符合筛选条件的运行日志。</p>}
      <div className="system-log-list">
        {page?.items.map((item) => <article className="system-log-entry" key={item.id}>
          <div className="system-log-meta">
            <time dateTime={new Date(item.timestamp * 1000).toISOString()}>{date(item.timestamp)}</time>
            <span className={`system-log-level system-log-level-${item.level.toLowerCase()}`}>{levels[item.level] || item.level}</span>
            <span>{categories[item.category] || item.category}</span>
          </div>
          <p className="system-log-message">{item.message}</p>
          <details><summary>查看诊断信息</summary>
            <dl className="system-log-details">
              <dt>事件</dt><dd>{item.event}</dd>
              {cursorPattern.test(item.request_id || '') && <><dt>请求编号</dt><dd>{item.request_id}</dd></>}
              {Object.entries(detailLabels).map(([key, label]) => {
                const value = item.details?.[key]
                return typeof value === 'string' || typeof value === 'number'
                  ? <div key={key}><dt>{label}</dt><dd>{String(value)}</dd></div> : null
              })}
            </dl>
          </details>
        </article>)}
      </div>
      {canLoadMore && <button className="secondary" disabled={Boolean(loading)} onClick={() => void load(page!.next_cursor!)}>
        {loading === 'more' ? '正在加载更多…' : '加载更多日志'}
      </button>}
    </section>
  )
}

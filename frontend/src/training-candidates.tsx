import React, { useEffect, useRef, useState } from 'react'
import { RefreshCw, Search } from 'lucide-react'
import { api } from './api'
import './training-candidates.css'

type PlatformCoverage = {
  platform: string
  verified: boolean
  cached_count: number
  topic_count: number
  eligible_count: number
  template_count: number
  unknown_topic_count: number
  conditions: { code: string; count: number; message: string }[]
  discovery?: { status: string; message?: string }
}
type Coverage = {
  topic_id: string
  platforms: PlatformCoverage[]
  eligible_count: number
  template_count: number
  notice?: string
}
type Job = {
  id: string
  status: string
  error?: string
  result?: {
    completed_platforms?: number
    total_platforms?: number
    platforms?: { platform: string; status: string; count?: number; message?: string }[]
  }
}
type Props = {
  topicId: string
  topicTitle?: string
  minimum?: number
  maximum?: number
  valid?: boolean
  disabled?: boolean
  mode?: string
  revision?: number
}
const names: Record<string, string> = {
  codeforces: 'Codeforces',
  atcoder: 'AtCoder',
  luogu: '洛谷',
  nowcoder: '牛客',
}
const statuses: Record<string, string> = {
  queued: '已排队',
  running: '补抓中',
  updated: '已更新',
  completed: '已完成',
  partial: '部分成功',
  failed: '失败',
  interrupted: '已中断',
  skipped: '未参与',
}
const explain = (error: unknown) => (error instanceof Error ? error.message : '请求未完成，请重试')

/** Local candidate evidence is separate from the explicit network refresh and paid AI selection. */
export function TrainingCandidates({
  topicId,
  topicTitle,
  minimum,
  maximum,
  valid = true,
  disabled = false,
  mode = 'learn',
  revision = 0,
}: Props) {
  const scope = JSON.stringify([topicId, minimum, maximum, revision])
  const current = useRef(scope)
  current.current = scope
  const alive = useRef(true)
  const operation = useRef(0)
  const pending = useRef('')
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const [state, setState] = useState<{
    scope: string
    loading?: boolean
    refreshing?: boolean
    error?: string
    coverage?: Coverage
    job?: Job
    message?: string
  }>({ scope })
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      operation.current++
      clearTimeout(timer.current)
    }
  }, [])
  useEffect(() => {
    operation.current++
    pending.current = ''
    clearTimeout(timer.current)
    setState({ scope })
  }, [scope])
  const visible = state.scope === scope ? state : { scope }
  const active = !!visible.loading || !!visible.refreshing
  const canRead = !!topicId && valid && !disabled && !active
  const isCurrent = (token: number, selected: string) =>
    alive.current && token === operation.current && current.current === selected
  const read = async () => {
    const query = new URLSearchParams({ topic_id: topicId })
    if (minimum != null && maximum != null) {
      query.set('cf_minimum', String(minimum))
      query.set('cf_maximum', String(maximum))
    }
    return api<Coverage>(`/training/candidates?${query}`)
  }
  const inspect = async () => {
    if (!canRead || pending.current === scope) return
    pending.current = scope
    const selected = scope,
      token = ++operation.current
    setState((old) => ({ ...old, scope, loading: true, error: '', message: '' }))
    try {
      const coverage = await read()
      if (isCurrent(token, selected)) setState({ scope, coverage })
    } catch (error) {
      if (isCurrent(token, selected))
        setState((old) => ({ ...old, loading: false, error: explain(error) }))
    } finally {
      if (isCurrent(token, selected)) pending.current = ''
    }
  }
  const refresh = async () => {
    if (!canRead || pending.current === scope) return
    pending.current = scope
    const selected = scope,
      token = ++operation.current
    setState((old) => ({ ...old, scope, refreshing: true, error: '', message: '', job: undefined }))
    try {
      const created = await api<Job>('/training/catalog/refresh', 'POST', { topic_id: topicId })
      if (!isCurrent(token, selected)) return
      setState((old) => ({ ...old, job: created }))
      let polls = 0
      const poll = async () => {
        if (!isCurrent(token, selected)) return
        try {
          const result = await api<{ items: Job[] }>('/jobs')
          if (!isCurrent(token, selected)) return
          const job = result.items.find((item) => item.id === created.id)
          if (job) setState((old) => ({ ...old, job }))
          if (job && !['queued', 'running'].includes(job.status)) {
            const coverage = await read()
            if (!isCurrent(token, selected)) return
            setState({
              scope,
              coverage,
              job,
              message: '补抓已结束，已重新读取本专题候选。请检查下方条件，再自行点击 AI 生成题单。',
            })
            pending.current = ''
            return
          }
          if (++polls >= 200)
            throw new Error(
              '等待补抓进度超时；后台任务可能仍在运行，请稍后重新查看候选，不会自动重发任务',
            )
          timer.current = setTimeout(() => void poll(), 1800)
        } catch (error) {
          if (isCurrent(token, selected)) {
            pending.current = ''
            setState((old) => ({ ...old, refreshing: false, error: explain(error) }))
          }
        }
      }
      await poll()
    } catch (error) {
      if (isCurrent(token, selected)) {
        pending.current = ''
        setState((old) => ({ ...old, refreshing: false, error: explain(error) }))
      }
    }
  }
  const coverage = visible.coverage
  return (
    <section className="training-candidates" aria-label="专项候选诊断" aria-busy={active}>
      <div className="training-candidates-heading">
        <strong>「{topicTitle || '当前专题'}」题库与筛选说明</strong>
        <div className="training-candidates-actions">
          <button
            type="button"
            className="button secondary"
            disabled={!canRead}
            onClick={() => void inspect()}
          >
            <Search size={15} />
            {visible.loading ? '正在检查候选…' : '查看本专题候选'}
          </button>
          <button
            type="button"
            className="button secondary"
            disabled={!canRead}
            onClick={() => void refresh()}
          >
            <RefreshCw
              size={15}
              className={visible.refreshing ? 'training-candidates-spinner' : ''}
            />
            {visible.refreshing ? '正在补抓本专题…' : '补抓本专题题库'}
          </button>
        </div>
      </div>
      <p className="help">
        将你本人已验证登录平台的专题候选合并筛选。“查看”只检查本地缓存；“补抓”按专题读取有界的公开题库，不调用
        AI，也不会自动生成或替换题单。
      </p>
      {!topicId && <p className="help">请先选择训练专题。</p>}
      {!valid && <p className="help">请先填写有效的 CF rating 上下限。</p>}
      {visible.error && (
        <p role="alert">候选检查或补抓未完成：{visible.error}。可重新查看候选，或手动重试补抓。</p>
      )}
      {visible.refreshing && (
        <p role="status">
          本专题补抓{statuses[visible.job?.status || 'queued'] || '进行中'}
          。逐个平台访问，可能需要数分钟；这里只等待进度，不会自动调用
          AI。切换专题只停止本页等待，后台任务仍可能继续。
        </p>
      )}
      {visible.job && (
        <div className="training-candidates-progress">
          <p role="status">
            本次补抓：{statuses[visible.job.status] || '状态未知'}
            {visible.job.result?.total_platforms != null &&
              ` · ${visible.job.result.completed_platforms ?? 0}/${visible.job.result.total_platforms} 个平台已处理`}
          </p>
          {visible.job.error && <p>{visible.job.error}</p>}
          {!!visible.job.result?.platforms?.length && (
            <ul>
              {visible.job.result.platforms.map((item) => (
                <li key={item.platform}>
                  {names[item.platform] || item.platform}：{statuses[item.status] || '已处理'}
                  {item.count != null ? ` · 读取 ${item.count} 题` : ''}
                  {item.message ? `；${item.message}` : ''}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {visible.message && <p role="status">{visible.message}</p>}
      {coverage && (
        <div className="training-candidates-evidence">
          <p>
            <strong>
              当前缓存可选 {coverage.eligible_count} 题，其中符合条件的模板题{' '}
              {coverage.template_count} 题。
            </strong>
            {mode === 'learn' &&
              coverage.template_count === 0 &&
              '“新学”首题必须有模板证据，当前缓存尚无符合条件的模板题。'}
          </p>
          <p className="help">
            {coverage.notice || '这是当前缓存与训练条件的交集，不代表各平台全站没有该专题题目。'}{' '}
            专题、发布日期、难度、已完成情况均会影响数量；不会悄悄放宽这些条件。
          </p>
          <ul className="training-candidates-platforms">
            {coverage.platforms.map((item) => (
              <li key={item.platform}>
                <div>
                  <strong>{names[item.platform] || item.platform}</strong>
                  <span className={`badge ${item.verified ? 'success' : 'neutral'}`}>
                    {item.verified ? '已验证，参与筛选' : '未验证，不参与选题'}
                  </span>
                </div>
                <p>
                  缓存 {item.cached_count} 题 · 专题匹配 {item.topic_count} 题 · 可选{' '}
                  {item.eligible_count} 题 · 模板 {item.template_count} 题
                </p>
                {!!item.unknown_topic_count && (
                  <p>
                    其中 {item.unknown_topic_count} 题缺少可靠专题标签；不会把未知标签当作匹配。
                  </p>
                )}
                {item.conditions?.map((condition) => (
                  <p key={condition.code}>
                    {condition.message}
                    {condition.count ? `（${condition.count} 题）` : ''}
                  </p>
                ))}
                {item.discovery?.message && <p>专题补抓：{item.discovery.message}</p>}
                {!item.verified && <a href="#settings">前往连接与设置检测平台登录</a>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

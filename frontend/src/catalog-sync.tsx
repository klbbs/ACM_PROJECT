import React from 'react'
import { AlertCircle, CheckCircle2, Clock, LoaderCircle } from 'lucide-react'
import './catalog-sync.css'

type PlatformResult = {
  platform: string
  status?: string
  count?: number
  dated_count?: number
  message?: string
}
export type CatalogSyncJob = {
  id?: string
  status?: string
  created_at?: number
  updated_at?: number
  error?: string | null
  result?: {
    count?: number
    completed_platforms?: number
    total_platforms?: number
    platforms?: PlatformResult[]
    submission_sync?: { status: string; message?: string }
  } | null
}
type PlatformEligibility = {
  platform: string
  login_verified?: boolean
  total_cached_count?: number
  cached_count?: number
  eligible_count?: number
  notice?: string
  reasons?: string[]
}
export type CatalogEligibility = {
  candidate_count?: number
  requires_publication_date?: boolean
  notice?: string
  platforms?: PlatformEligibility[]
  refresh?: PlatformResult[]
  last_refresh_at?: number
}
export type CatalogSyncPanelProps = {
  job?: CatalogSyncJob
  eligibility?: CatalogEligibility
  candidateCount: number
  busy?: boolean
  requestError?: string
  candidateError?: string
  loadingCandidates?: boolean
  onSettings?: () => void
}
const names: Record<string, string> = {
  codeforces: 'Codeforces',
  atcoder: 'AtCoder',
  luogu: '洛谷',
  nowcoder: '牛客',
}
const statusLabels: Record<string, string> = {
  queued: '等待同步',
  running: '正在获取',
  updated: '同步成功',
  failed: '同步失败',
}
function timestamp(value?: number) {
  return value ? new Date(value * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : ''
}

export function CatalogSyncPanel({
  job,
  eligibility,
  candidateCount,
  busy,
  requestError,
  candidateError,
  loadingCandidates,
  onSettings,
}: CatalogSyncPanelProps) {
  const status = job?.status
  const starting = busy && status !== 'queued' && status !== 'running'
  const active = busy || status === 'queued' || status === 'running'
  const results = starting
    ? []
    : (job?.result?.platforms ?? (job || active ? [] : eligibility?.refresh || []))
  const completed = starting
    ? 0
    : (job?.result?.completed_platforms ??
      results.filter((row) => ['updated', 'failed'].includes(row.status || '')).length)
  const total = job?.result?.total_platforms ?? (results.length || 4)
  const title = requestError
    ? '同步请求未启动'
    : starting
      ? '正在请求同步任务'
      : status === 'queued'
        ? '同步任务已排队'
        : active
          ? '正在同步平台题库'
          : status === 'completed'
            ? '平台题库同步完成'
            : status === 'partial'
              ? '部分平台同步成功'
              : status === 'failed'
                ? '本次同步未成功'
                : status === 'interrupted'
                  ? '上次同步已中断，请重新同步'
                  : results.length
                    ? '最近一次题库同步结果'
                    : '题库同步与候选条件'
  const available = eligibility?.candidate_count ?? candidateCount
  const resultTime = job ? job.updated_at || job.created_at : eligibility?.last_refresh_at
  return (
    <section
      className={`catalog-sync-panel ${requestError || status === 'failed' ? 'has-error' : ''}`}
      aria-label="题库同步进度与说明"
    >
      <div className="catalog-sync-title" role="status" aria-live="polite">
        {active ? (
          <LoaderCircle className="catalog-sync-spinner" size={19} />
        ) : requestError || ['failed', 'partial', 'interrupted'].includes(status || '') ? (
          <AlertCircle size={19} />
        ) : status === 'completed' ? (
          <CheckCircle2 size={19} />
        ) : (
          <Clock size={19} />
        )}
        <strong>{title}</strong>
        {active && (
          <span>
            {completed} / {total} 个平台已处理
          </span>
        )}
      </div>
      {active && (
        <>
          <progress aria-label="题库同步进度" value={completed} max={total} />
          <p>
            按平台依次读取官方公开列表。AtCoder
            需要请求多个比赛页面，通常耗时更长；同步期间可继续使用旧缓存，无需重复点击。
          </p>
        </>
      )}
      {requestError && <p role="alert">{requestError}</p>}
      {!starting && job?.error && <p>{job.error}</p>}
      {!starting && resultTime && (
        <p className="catalog-sync-time">
          {active ? '任务时间' : '结果时间'}：{timestamp(resultTime)}
          。这是该次任务的结果，当前候选资格以下方筛选说明为准。
        </p>
      )}
      {!!results.length && (
        <ul className="catalog-sync-results">
          {results.map((row) => (
            <li key={row.platform}>
              <strong>{names[row.platform] || row.platform}</strong>
              <span>
                {statusLabels[row.status || ''] || '等待同步'}
                {row.status === 'updated' ? ` · 更新 ${row.count ?? 0} 题` : ''}
              </span>
              {row.status === 'updated' && row.dated_count != null && (
                <small>有可靠发布日期 {row.dated_count} 题</small>
              )}
              {row.message && <p>{row.message}</p>}
            </li>
          ))}
        </ul>
      )}
      {!starting && job?.result?.submission_sync && (
        <p role="status">
          今日提交同步：{({ running: '正在读取官方提交…', completed: '已完成', partial: '部分完成', failed: '未完成', skipped: '未启动' } as Record<string, string>)[job.result.submission_sync.status] || '状态未知'}
          {job.result.submission_sync.message ? `。${job.result.submission_sync.message}` : ''}
        </p>
      )}
      {loadingCandidates ? (
        <p role="status">正在读取候选条件…</p>
      ) : candidateError ? (
        <p role="alert">
          暂时无法读取候选条件：{candidateError}。同步任务状态仍以本面板上方为准，请重试读取。
        </p>
      ) : (
        <div className="catalog-sync-eligibility">
          {active && (
            <p>下列为上次读取的筛选结果，任务结束后会自动刷新；同步进度与更新题数以上方为准。</p>
          )}
          <strong>
            当前筛选共 {available} 道可选题
            {available > candidateCount ? `，本次展示 ${candidateCount} 道` : ''}
          </strong>
          {available === 0 && (
            <p>
              同步成功不等于可以推荐。请按以下原因检查登录、日期和筛选条件；系统不会放宽发布日期或把未验证账号当作已登录。
            </p>
          )}
          {!!eligibility?.requires_publication_date && <p>{eligibility.notice}</p>}
          <ul>
            {(eligibility?.platforms || []).map((row) => (
              <li key={row.platform}>
                <strong>{names[row.platform] || row.platform}</strong>：缓存{' '}
                {row.total_cached_count ?? row.cached_count ?? 0} 题，当前可选{' '}
                {row.eligible_count ?? 0} 题。
                {(row.reasons?.length ? row.reasons : row.notice ? [row.notice] : []).map(
                  (reason, index) => (
                    <p key={index}>{reason}</p>
                  ),
                )}
              </li>
            ))}
          </ul>
          {onSettings && (
            <button type="button" className="text-button" onClick={onSettings}>
              检查平台登录与训练偏好
            </button>
          )}
        </div>
      )}
      <details className="catalog-sync-guide">
        <summary>“同步平台题库”怎么用？</summary>
        <ol>
          <li>
            点击同步，获取四个平台的官方公开题目列表。本操作不获取个人提交记录、不检测
            Cookie，也不调用 AI。
          </li>
          <li>
            到“连接与设置”绑定账号并点击“检测登录状态”。只有检测通过且未过期的平台会参与选题。
          </li>
          <li>
            检查最早发布日期、CF 难度区间及标签。已提交、完成、跳过和对战占用的题会自动排除；非 CF
            平台保留原生难度，不套用 CF 分数。
          </li>
          <li>
            看到真实候选后，确认连接与设置中的默认 AI 模型已配置，再点击 AI
            选题。同步没有候选时，请先按上面的分平台原因调整条件。
          </li>
        </ol>
        <p>
          这是有范围的同步：CF 读取已结束比赛的带 rating 题目；AtCoder 读取最近 6 场 ABC / ARC / AGC
          的题目并补充 Library Practice 模板；洛谷补充官方模板分页，专项选题还可定向补抓专题列表；牛客读取官方列表首页。无法确认发布日期的题目，在启用日期下限时会被排除。访问限制或页面变化可能使单个平台失败，其他平台仍可成功。
        </p>
      </details>
    </section>
  )
}

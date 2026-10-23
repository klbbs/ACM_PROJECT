import { useCallback, useEffect, useRef, useState } from 'react'
import {
  CheckCircle2,
  ExternalLink,
  FileCode2,
  LoaderCircle,
  RefreshCw,
  Sparkles,
  XCircle,
} from 'lucide-react'
import { api, ApiError, date, names } from './api'
import './problem-submissions.css'

type Row = Record<string, any>

export type ProblemSubmission = {
  submission_id?: string | null
  account?: string | null
  verdict?: string | null
  submitted_at?: number | null
  language?: string | null
  url?: string | null
  attempt_id?: string | null
  has_local_code?: boolean
  code_verified?: boolean
  analysis_available?: boolean
  analysis_disabled_reason?: string
  submission_review?: any
}

export type SyncStatus = {
  status?: string
  count?: number
  complete?: boolean
  coverage?: string
  warnings?: string[]
  message?: string
  error?: string
  started_at?: number
  finished_at?: number
  job_id?: string
}

type CodeState = {
  code: string
  language?: string | null
  verified: boolean
  provenance: 'official' | 'local_saved'
  attempt_id?: string | null
}

export type ProblemSubmissionsProps = {
  problemId: string
  handles: string[]
  editorCode: string
  active: boolean
  revision: number
  aiReady: boolean
  coach: (path: string) => Promise<any> | void
  loadCode: (code: string, language?: string | null, stillCurrent?: () => boolean) => Promise<void>
  onChanged: () => void | Promise<void>
  onError: (message: string) => void
  selected?: Row | null
}

/** Official record links only; anything else stays plain text. */
const OFFICIAL_HOSTS: Record<string, string[]> = {
  codeforces: ['codeforces.com', 'www.codeforces.com'],
  atcoder: ['atcoder.jp'],
  luogu: ['www.luogu.com.cn', 'luogu.com.cn'],
  nowcoder: ['ac.nowcoder.com', 'www.nowcoder.com'],
}
const ERROR_VERDICTS = new Set([
  'WA',
  'RE',
  'CE',
  'TLE',
  'MLE',
  'OLE',
  'PE',
  'FAIL',
  'PARTIAL',
  'FAILED',
  'PRESENTATION_ERROR',
  'IDLENESS_LIMIT_EXCEEDED',
])
const PENDING_VERDICTS = new Set(['', 'PENDING', 'TESTING', 'JUDGING', 'QUEUED'])
const POLL_MS = 1200
const POLL_TRIES = 75
const REVIEW_CATEGORY_NAMES: Record<string, string> = {
  algorithm_strategy: '算法思路/解题策略',
  implementation_logic: '代码实现/逻辑错误',
  boundary_conditions: '边界条件',
  complexity_resources: '复杂度或资源超限',
  input_output_format: '输入输出/格式',
  language_compilation: '语言或编译问题',
  judging_information_insufficient: '判题信息不足',
  unable_to_determine: '无法确定',
}

export function allowedRecordUrl(problemId: string, value?: string | null): string | undefined {
  if (!value) return undefined
  try {
    const url = new URL(value)
    if (
      url.protocol !== 'https:' ||
      url.username ||
      url.password ||
      url.port ||
      url.hash
    )
      return undefined
    const platform = problemId.split(':')[0]
    return OFFICIAL_HOSTS[platform]?.includes(url.hostname) ? url.href : undefined
  } catch {
    return undefined
  }
}

const text = (error: unknown) =>
  error instanceof Error ? error.message : '请求未完成，请检查连接后重试'
const sleep = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms))
const running = (status?: string) => !!status && ['queued', 'running'].includes(status)

export function ProblemSubmissions({
  problemId,
  handles,
  editorCode,
  active,
  revision,
  aiReady,
  coach,
  loadCode,
  onChanged,
  onError,
  selected,
}: ProblemSubmissionsProps) {
  const platform = problemId.split(':')[0]
  const platformName = names[platform] || platform
  const remote = platform === 'codeforces' || platform === 'atcoder'
  const [handle, setHandle] = useState(handles[0] || '')
  const [serverHandles, setServerHandles] = useState<string[]>([])
  const [items, setItems] = useState<ProblemSubmission[]>([])
  const [loading, setLoading] = useState(false)
  const [listError, setListError] = useState('')
  const [sync, setSync] = useState<SyncStatus | null>(null)
  const [syncError, setSyncError] = useState('')
  const [syncBusy, setSyncBusy] = useState(false)
  const [cooldown, setCooldown] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const [codes, setCodes] = useState<Record<string, CodeState>>({})
  const [codeBusy, setCodeBusy] = useState('')
  const [codeErrors, setCodeErrors] = useState<Record<string, string>>({})
  const [confirmed, setConfirmed] = useState<Record<string, boolean>>({})
  const [loaded, setLoaded] = useState<Record<string, string>>({})
  const [analysisBusy, setAnalysisBusy] = useState<Record<string, boolean>>({})
  const [analysisErrors, setAnalysisErrors] = useState<Record<string, string>>({})
  const analysisRunning = useRef(new Set<string>())

  const mounted = useRef(true)
  const currentProblem = useRef(problemId)
  const currentHandle = useRef(handle)
  currentProblem.current = problemId
  currentHandle.current = handle
  const propsHandles = useRef(handles)
  propsHandles.current = handles
  const listingGeneration = useRef(0)
  const pollGeneration = useRef(0)
  const codeGeneration = useRef(0)
  const syncing = useRef(false)
  const callbacks = useRef({ coach, loadCode, onChanged, onError })
  callbacks.current = { coach, loadCode, onChanged, onError }

  const handleOptions = Array.from(new Set([...handles, ...serverHandles]))
  const handleOptionsKey = handleOptions.join('\n')
  useEffect(() => {
    // The listing can discover accounts the workbench payload did not include yet,
    // so the selection must always point at a real option. A valid user choice is
    // never replaced, including after switching accounts.
    if (!handleOptionsKey) {
      if (handle) setHandle('')
      return
    }
    if (!handleOptions.includes(handle)) setHandle(handleOptions[0])
  }, [handleOptionsKey, handle])
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      listingGeneration.current++
      pollGeneration.current++
      codeGeneration.current++
    }
  }, [])
  useEffect(() => {
    if (cooldown <= Date.now()) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [cooldown])
  const cooldownLeft = Math.max(0, Math.ceil((cooldown - now) / 1000))

  const load = useCallback(async (target: string, account: string) => {
    const generation = ++listingGeneration.current
    let adopting = false
    setLoading(true)
    try {
      const query = account ? `?handle=${encodeURIComponent(account)}` : ''
      const result = await api<{
        items?: ProblemSubmission[]
        handles?: string[]
        sync_status?: SyncStatus | null
      }>(`/problem-submissions/${encodeURIComponent(target)}${query}`)
      if (
        !mounted.current ||
        currentProblem.current !== target ||
        currentHandle.current !== account ||
        generation !== listingGeneration.current
      )
        return
      const discovered = Array.isArray(result.handles) ? result.handles : null
      if (discovered !== null) setServerHandles(discovered)
      const known = new Set([...(discovered || []), ...propsHandles.current])
      setListError('')
      // A live sync owns the summary; a late list read must not replace it.
      if (!syncing.current && result.sync_status) setSync(result.sync_status)
      // Discovering accounts must not render rows for another account (or for no
      // account at all): the selection effect adopts a real account and reloads.
      if (discovered?.length && !known.has(account)) {
        adopting = true
        return
      }
      setItems(Array.isArray(result.items) ? result.items : [])
    } catch (error) {
      if (
        mounted.current &&
        currentProblem.current === target &&
        currentHandle.current === account &&
        generation === listingGeneration.current
      )
        setListError(text(error))
    } finally {
      if (
        mounted.current &&
        currentProblem.current === target &&
        currentHandle.current === account &&
        generation === listingGeneration.current &&
        !adopting
      )
        setLoading(false)
    }
  }, [])

  const key = `${problemId}\u0000${handle}`
  const resetKey = useRef('')
  useEffect(() => {
    // A parent refresh (revision) reloads records but must not discard code the
    // user already opened for this problem and account.
    if (resetKey.current !== key) {
      resetKey.current = key
      codeGeneration.current++
      setItems([])
      setSync(null)
      setSyncError('')
      setCodeBusy('')
      setCodes({})
      setCodeErrors({})
      setConfirmed({})
      setLoaded({})
      setAnalysisBusy({})
      setAnalysisErrors({})
    }
    void load(problemId, handle)
  }, [problemId, handle, revision, key, load])

  const selectedRow = useRef<HTMLElement | null>(null)
  const isSelected = (item: ProblemSubmission) => {
    if (!selected?.submission_id) return false
    if (String(item.submission_id) !== String(selected.submission_id)) return false
    if (!selected.account || !item.account) return true
    return platform === 'codeforces'
      ? String(item.account).toLowerCase() === String(selected.account).toLowerCase()
      : item.account === selected.account
  }
  useEffect(() => {
    selectedRow.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [items, selected])

  async function pollJob(
    jobId: string,
    target: string,
    account: string,
    generation: number,
  ): Promise<{ status: string; result: Row; error?: string } | null> {
    let misses = 0
    for (let attempt = 0; attempt < POLL_TRIES; attempt++) {
      await sleep(POLL_MS)
      if (
        !mounted.current ||
        currentProblem.current !== target ||
        generation !== pollGeneration.current
      )
        return null
      let jobs: { items?: Row[] }
      try {
        jobs = await api<{ items?: Row[] }>('/jobs')
      } catch (error) {
        if (++misses >= 3) {
          setSyncError(
            `暂时无法读取同步进度：${text(error)}。任务可能仍在后台执行，请稍后刷新本题记录，请勿重复点击同步。`,
          )
          return null
        }
        continue
      }
      if (
        !mounted.current ||
        currentProblem.current !== target ||
        generation !== pollGeneration.current
      )
        return null
      const job = (jobs.items || []).find((item) => item.id === jobId)
      if (!job) {
        if (++misses >= 5) {
          setSyncError('暂时读不到这次同步任务的状态，请稍后重试读取本题记录；请勿重复点击同步。')
          return null
        }
        continue
      }
      misses = 0
      if (running(job.status)) {
        if (currentHandle.current === account)
          setSync((previous) => ({ ...(previous || {}), status: job.status }))
        continue
      }
      return { status: job.status, result: job.result || {}, error: job.error }
    }
    setSyncError('同步任务仍在后台处理，请稍后重新读取本题记录；不要重复点击同步。')
    return null
  }

  async function startSync() {
    if (!handle || syncing.current || active || cooldownLeft > 0) return
    const target = problemId
    const account = handle
    syncing.current = true
    const generation = ++pollGeneration.current
    setSyncBusy(true)
    setSyncError('')
    try {
      // Records are read for this problem and account only: no code, revision or
      // submission id is sent, so an empty editor never blocks synchronization.
      const created = await api<{ id: string; status?: string }>(
        `/problem-submissions/${encodeURIComponent(target)}/sync`,
        'POST',
        { handle: account },
      )
      if (!mounted.current || currentProblem.current !== target) return
      setSync({ status: created.status || 'queued', started_at: Math.floor(Date.now() / 1000) })
      const finished = await pollJob(created.id, target, account, generation)
      if (!finished) return
      if (
        !mounted.current ||
        currentProblem.current !== target ||
        currentHandle.current !== account
      )
        return
      setSync({ ...finished.result, status: finished.status })
      if (finished.status === 'failed')
        setSyncError(
          finished.error ||
            finished.result?.message ||
            '本题记录未完成同步，原记录已保留；请检查平台网络或登录状态。',
        )
      if (finished.status === 'interrupted')
        setSyncError('同步被中断（服务可能重启）；原有记录已保留，可以重新同步。')
      await load(target, account)
      try {
        await callbacks.current.onChanged()
      } catch (error) {
        if (mounted.current)
          setSyncError(`记录已读取，但工作台刷新失败：${text(error)}。可稍后刷新页面查看代码记录。`)
      }
    } catch (error) {
      if (mounted.current && currentProblem.current === target) {
        const message = text(error)
        const retry = error instanceof ApiError ? error.retryAfterSeconds : undefined
        setSyncError(retry ? `${message}（${retry} 秒后可重试）` : message)
        if (retry) {
          setNow(Date.now())
          setCooldown(Date.now() + retry * 1000)
        }
        callbacks.current.onError(message)
      }
    } finally {
      syncing.current = false
      if (mounted.current) setSyncBusy(false)
    }
  }

  async function fetchCode(item: ProblemSubmission) {
    if (codeBusy || !item.submission_id) return
    const target = problemId
    const account = handle
    const submission = item.submission_id
    const generation = ++codeGeneration.current
    // The response belongs to the account and problem that asked for it: a late
    // reply must never write code, metadata or errors into another selection.
    const stillCurrent = () =>
      mounted.current &&
      currentProblem.current === target &&
      currentHandle.current === account &&
      generation === codeGeneration.current
    setCodeBusy(submission)
    setCodeErrors((previous) => ({ ...previous, [submission]: '' }))
    try {
      let state: CodeState
      if (item.has_local_code && item.attempt_id) {
        const attempt = await api<Row>(`/workbench/attempt/${encodeURIComponent(item.attempt_id)}`)
        state = {
          code: attempt.code,
          language: attempt.language,
          verified: Boolean(attempt.code_verified),
          provenance: attempt.code_verified ? 'official' : 'local_saved',
          attempt_id: attempt.id,
        }
      } else {
        if (!/^\d+$/.test(submission)) throw new Error('这条记录没有可读取的官方提交号')
        const result = await api<Row>(
          `/problem-submissions/${encodeURIComponent(target)}/source`,
          'POST',
          { handle: account, submission_id: submission },
        )
        state = {
          code: result.code,
          language: result.language,
          verified: Boolean(result.code_verified),
          provenance: result.code_verified ? 'official' : 'local_saved',
          attempt_id: result.attempt_id,
        }
      }
      if (!stillCurrent()) return
      setCodes((previous) => ({ ...previous, [submission]: state }))
      // The first read creates the attempt record, so this row becomes reviewable
      // immediately instead of waiting for a reload or another code card.
      setItems((previous) =>
        previous.map((row) =>
          String(row.submission_id) === String(submission)
            ? {
                ...row,
                attempt_id: state.attempt_id || row.attempt_id,
                has_local_code: true,
                code_verified: state.verified,
                analysis_available: state.verified,
                analysis_disabled_reason: state.verified ? '' : '源码未与本次平台提交核验，不能用于分析',
                language: state.language || row.language,
              }
            : row,
        ),
      )
      // The workbench now owns an immutable record, so the existing review and
      // code view can be reused without loading the draft.
      try {
        await callbacks.current.onChanged()
      } catch {
        /* Reading the record succeeded; a background refresh failure is not fatal. */
      }
    } catch (error) {
      if (stillCurrent())
        setCodeErrors((previous) => ({
          ...previous,
          [submission]: `判题已同步，源码暂不可读：${text(error)}`,
        }))
    } finally {
      if (generation === codeGeneration.current) setCodeBusy('')
    }
  }

  async function review(item: ProblemSubmission, attemptId: string) {
    const target = problemId
    const account = handle
    const submission = String(item.submission_id || '')
    const reviewKey = `${target}\u0000${account}\u0000${submission}\u0000${attemptId}`
    if (analysisRunning.current.has(reviewKey)) return
    analysisRunning.current.add(reviewKey)
    const stillCurrent = () => mounted.current && currentProblem.current === target && currentHandle.current === account
    setAnalysisBusy((previous) => ({ ...previous, [reviewKey]: true }))
    setAnalysisErrors((previous) => ({ ...previous, [reviewKey]: '' }))
    try {
      const result = await callbacks.current.coach(
        `/workbench/attempt/${encodeURIComponent(attemptId)}/submission-review`,
      )
      if (result?.error) throw new Error(result.error)
      if (!result?.id) throw new Error('AI 分析任务没有成功排队，请重试')
      let finished = false
      for (let index = 0; index < POLL_TRIES; index++) {
        await sleep(POLL_MS)
        if (!stillCurrent()) return
        const jobs = await api<{ items?: Row[] }>('/jobs')
        if (!stillCurrent()) return
        const job = (jobs.items || []).find((row) => row.id === result.id)
        if (!job) continue
        if (running(job.status)) continue
        if (job.status !== 'completed') throw new Error(job.error || 'AI 分析失败，可以重试')
        finished = true
        await load(target, account)
        break
      }
      if (stillCurrent() && !finished) throw new Error('AI 分析仍在后台处理，请稍后重试查看记录')
    } catch (error) {
      if (stillCurrent()) setAnalysisErrors((previous) => ({ ...previous, [reviewKey]: text(error) }))
    } finally {
      analysisRunning.current.delete(reviewKey)
      if (stillCurrent()) setAnalysisBusy((previous) => ({ ...previous, [reviewKey]: false }))
    }
  }

  async function loadIntoEditor(item: ProblemSubmission, state: CodeState) {
    const target = problemId
    const account = handle
    const submission = String(item.submission_id || item.attempt_id || '')
    const stillCurrent = () =>
      mounted.current && currentProblem.current === target && currentHandle.current === account
    if (!stillCurrent()) return
    try {
      await callbacks.current.loadCode(state.code, state.language, stillCurrent)
      if (!stillCurrent()) return
      setLoaded((previous) => ({ ...previous, [submission]: '已载入编辑器，正在自动保存' }))
      setCodeErrors((previous) => ({ ...previous, [submission]: '' }))
      setConfirmed((previous) => ({ ...previous, [submission]: false }))
    } catch (error) {
      if (stillCurrent())
        setCodeErrors((previous) => ({
          ...previous,
          [submission]: `未载入编辑器：${text(error)}`,
        }))
    }
  }

  const count = items.length
  const syncStatus = sync?.status || ''
  // The job itself completes even when the platform read was cut short: only the
  // result's own completeness may be reported as success.
  const partial = syncStatus === 'partial' || (!!sync && sync.complete === false)
  const failed = syncStatus === 'failed'
  const disabled = !handle || syncBusy || active || cooldownLeft > 0
  const reason = !handle
    ? `尚未配置 ${platformName} 账号；添加账号后才能同步本题记录。`
    : syncBusy || running(syncStatus)
      ? '正在同步本题记录，请稍候…'
      : active
        ? '有工作台任务正在执行，请稍后再同步。'
        : cooldownLeft > 0
          ? `平台请求过于频繁，${cooldownLeft} 秒后可重试。`
          : ''

  return (
    <section className="problem-submissions" aria-label="同步本题提交记录">
      <h3>
        <RefreshCw size={18} />
        同步本题提交
      </h3>
      <p className="muted">
        {remote
          ? `CF、AtCoder 可以在上方「提交到平台」由本站提交；如果你已在 ${platformName} 官方页面提交，回到这里点击一次「同步本题提交」即可读取这个账号在当前题的记录和判题，不需要填写提交号、重新粘贴代码或勾选代码校对。`
          : `${platformName} 的提交请在官方页面完成；回到这里点击「同步本题提交」，本站会读取这个账号在当前题的记录和判题，不需要填写提交号、重新粘贴代码或勾选代码校对。`}{' '}
        同步只读取记录，不会替你交题。
      </p>
      <div className="problem-submit-controls">
        <label>
          平台账号
          <select
            aria-label="同步账号"
            value={handle}
            disabled={syncBusy}
            onChange={(event) => setHandle(event.target.value)}
          >
            {!handleOptions.length && <option value="">尚未配置账号</option>}
            {handleOptions.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="problem-submit-primary"
          disabled={disabled}
          onClick={() => void startSync()}
        >
          {syncBusy || running(syncStatus) ? (
            <LoaderCircle size={15} className="problem-submission-spin" />
          ) : (
            <RefreshCw size={15} />
          )}
          {syncBusy || running(syncStatus) ? '正在同步…' : '同步本题提交'}
        </button>
        {!handleOptions.length && (
          <a className="text-button" href="#settings">
            前往连接与设置添加账号
          </a>
        )}
      </div>
      {reason && (
        <p className="muted" role="status">
          {reason}
        </p>
      )}
      {syncError && (
        <p className="problem-submission-error" role="alert">
          {syncError}
        </p>
      )}
      {sync && !syncError && (
        <p className={partial || failed ? 'problem-submission-warning' : 'muted'} role="status">
          {failed
            ? sync.message || '本题记录未完成同步，原记录已保留。'
            : running(syncStatus)
              ? '正在读取平台记录…'
              : partial
                ? `部分成功：本次读取未覆盖全部记录${sync.count != null ? `（已读取 ${sync.count} 条本题提交）` : ''}。原有记录已保留。`
                : `已同步${sync.count != null ? ` ${sync.count} 条本题提交` : ''}${sync.message ? ` · ${sync.message}` : ''}`}
          {sync.coverage ? ` · 查询范围：${sync.coverage}` : ''}
          {!!sync.warnings?.length ? ` · ${sync.warnings.join('；')}` : ''}
          {sync.error ? ` · ${sync.error}` : ''}
          {sync.finished_at ? ` · ${date(sync.finished_at)}` : ''}
        </p>
      )}
      {partial && sync?.error && (
        <p className="problem-submission-warning" role="status">
          {sync.error}
        </p>
      )}

      <h4>
        本题提交记录 <span className="problem-submit-count">{loading ? '…' : count}</span>
      </h4>
      {listError && (
        <p className="problem-submission-error" role="alert">
          暂时无法读取本题记录：{listError}{' '}
          <button type="button" className="secondary" onClick={() => void load(problemId, handle)}>
            重新读取记录
          </button>
        </p>
      )}
      {loading && !count && <p className="muted">正在读取本题记录…</p>}
      {!loading && !count && !listError && (
        <p className="muted">
          还没有本题的提交记录。如果刚在 {platformName} 官方页面提交，请点击上方「同步本题提交」。同步显示没有记录时，
          通常是本次查询范围未覆盖到这条提交，或该账号没有提交过这道题；原有记录不会被删除。
        </p>
      )}
      {!!count && (
        <div className="problem-submit-records" aria-label="本题提交记录">
          {items.map((item, index) => {
            const submission = String(item.submission_id || item.attempt_id || index)
            const verdict = String(item.verdict || '').toUpperCase()
            const accepted = verdict === 'AC'
            const pending = PENDING_VERDICTS.has(verdict)
            const errored = !accepted && !pending && ERROR_VERDICTS.has(verdict)
            const url = allowedRecordUrl(problemId, item.url)
            const state = codes[submission]
            const note = codeErrors[submission]
            // A first source read creates the attempt; use its metadata before any
            // reload so the row can be reviewed immediately.
            const attemptId = state?.attempt_id || item.attempt_id
            const reviewKey = `${problemId}\u0000${handle}\u0000${String(item.submission_id || '')}\u0000${attemptId || ''}`
            const report = item.submission_review?.result
            const analysisDisabledReason = accepted
              ? 'AC 记录无需错因分析'
              : pending
                ? '判题中，结束后才能分析'
                : !errored
                  ? '此判题状态暂不支持错因分析'
                  : !aiReady
                    ? '尚未配置 AI 模型，请先在连接与设置中选择模型'
                    : active
                      ? '当前有工作台任务处理中，请稍后再分析'
                      : !attemptId
                        ? '缺少已核验的本次提交源码，请先获取本次代码'
                        : (state ? !state.verified : item.analysis_available !== true)
                          ? item.analysis_disabled_reason || '源码未与本次平台提交核验，请先获取本次代码'
                          : ''
            const replacing =
              !!state && !!editorCode.trim() && editorCode.trim() !== state.code.trim()
            return (
              <article
                key={submission}
                ref={isSelected(item) ? selectedRow : undefined}
                className={`problem-submission ${accepted ? 'is-ac' : errored ? 'is-error' : ''} ${isSelected(item) ? 'history-selected' : ''}`}
              >
                <div className="problem-submission-head">
                  <strong className="problem-submission-verdict" role="status">
                    {accepted ? (
                      <CheckCircle2 size={18} />
                    ) : errored ? (
                      <XCircle size={18} />
                    ) : null}
                    {verdict || '判题中'}
                  </strong>
                  <span className="muted">
                    {item.submission_id ? `#${item.submission_id}` : '本地代码记录（无官方提交号）'}
                    {item.account ? ` · ${item.account}` : ''} · {date(item.submitted_at || undefined)}
                    {item.language ? ` · ${item.language}` : ' · 语言未记录'}
                  </span>
                </div>
                <div className="problem-submission-actions">
                  {url && (
                    <a href={url} target="_blank" rel="noopener noreferrer">
                      官方记录 <ExternalLink size={13} />
                    </a>
                  )}
                  {!!item.submission_id && (
                    <button
                      type="button"
                      className="secondary"
                      disabled={!!codeBusy || loading}
                      onClick={() => void fetchCode(item)}
                    >
                      <FileCode2 size={14} />
                      {codeBusy === submission
                        ? '正在读取代码…'
                        : state
                          ? '重新读取代码'
                          : item.has_local_code
                            ? '查看当次代码'
                            : '获取本次代码'}
                    </button>
                  )}
                  <button
                    type="button"
                    className="secondary"
                    disabled={!!analysisDisabledReason || !!analysisBusy[reviewKey]}
                    title={analysisDisabledReason || undefined}
                    onClick={() => attemptId && void review(item, attemptId)}
                  >
                    {analysisBusy[reviewKey] ? <LoaderCircle size={14} className="problem-submission-spin" /> : <Sparkles size={14} />}
                    {analysisBusy[reviewKey] ? 'AI 分析中…' : analysisErrors[reviewKey] ? '重试 AI 分析' : 'AI 分析'}
                  </button>
                </div>
                {analysisDisabledReason && (
                  <p className="problem-submission-analysis-note" title={analysisDisabledReason}>
                    {analysisDisabledReason}
                  </p>
                )}
                {analysisErrors[reviewKey] && (
                  <p className="problem-submission-error" role="alert">{analysisErrors[reviewKey]}</p>
                )}
                {report && (
                  <div className="problem-submission-review" aria-label="本次提交的 AI 分析">
                    <strong>AI 推测 · {report.confidence === 'high' ? '较高' : report.confidence === 'medium' ? '中等' : '较低'}把握</strong>
                    <p className="muted">这是基于当前题面、当次源码和判题信息的推测，不等同于官方解释。</p>
                    <p><b>判题现象：</b>{report.phenomenon}</p>
                    <p><b>主要错误类型：</b>{report.categories.map((category: string) => REVIEW_CATEGORY_NAMES[category] || category).join('、')}</p>
                    <div><b>结合题面与代码的证据：</b><ul>{report.evidence.map((evidence: string, evidenceIndex: number) => <li key={evidenceIndex}>{evidence}</li>)}</ul></div>
                    <div><b>建议检查方向：</b><ul>{report.check_directions.map((direction: string, directionIndex: number) => <li key={directionIndex}>{direction}</li>)}</ul></div>
                    {report.minimal_change_idea && <p><b>最小修改思路：</b>{report.minimal_change_idea}</p>}
                  </div>
                )}
                {note && (
                  <p className="problem-submission-warning" role="status">
                    {note}
                  </p>
                )}
                {state && (
                  <div className="problem-submission-code">
                    <p className="muted">
                      {state.verified
                        ? '代码来自官方页面并核对过本人身份；仅用于本次复盘，不会自动提交。'
                        : '这份代码是本站保存的当次记录，未与平台源码核对；当前编辑器内容不是它的来源。'}
                    </p>
                    <details>
                      <summary>查看当次代码（只读）</summary>
                      <pre className="wb-snapshot">{state.code}</pre>
                    </details>
                    <div className="problem-submission-actions">
                      <button
                        type="button"
                        className="secondary"
                        disabled={!!replacing && !confirmed[submission]}
                        onClick={() => void loadIntoEditor(item, state)}
                      >
                        载入编辑器
                      </button>
                    </div>
                    {replacing && (
                      <label className="problem-submission-confirm">
                        <input
                          type="checkbox"
                          checked={!!confirmed[submission]}
                          onChange={(event) =>
                            setConfirmed((previous) => ({
                              ...previous,
                              [submission]: event.target.checked,
                            }))
                          }
                        />
                        确认用这次提交的代码替换编辑器当前草稿（现有代码会先自动保存）
                      </label>
                    )}
                    {loaded[submission] && (
                      <p className="muted" role="status">
                        {loaded[submission]}
                      </p>
                    )}
                  </div>
                )}
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}

import { useCallback, useEffect, useRef, useState } from 'react'
import { CheckCircle2, ExternalLink, RefreshCw, Send, XCircle } from 'lucide-react'
import { api, ApiError, date, names } from './api'
import './remote-submission.css'

type Compiler = { id: string | number; name: string }
type RemoteRow = {
  id: string
  problem_id: string
  handle: string
  status: string
  message?: string
  submission_id?: string | number | null
  attempt_id?: string | null
  verdict?: string | null
  official_url?: string | null
  created_at?: number
  updated_at?: number
  revision?: number
  compiler_name?: string
  request_key?: string
}
type Intent = {
  problem_id: string
  handle: string
  revision: number
  compiler_id: string
  request_key: string
}
export type RemoteSubmissionPanelProps = {
  problemId: string
  handles: string[]
  language: string
  code: string
  save: () => Promise<number | null>
  onError: (message: string) => void
  onChanged: () => void | Promise<void>
}
const pending = (row: RemoteRow) => ['queued', 'submitting', 'judging'].includes(row.status)
const uncertain = (row: RemoteRow) => row.status === 'uncertain'
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : '请求未完成，请检查连接后重试'
function cppStandard(name: string) {
  const match = name.match(/(?:[cg]\+\+|gnu\+\+|cxx|cpp)\s*(\d{2})(?!\d)/i)
  if (!match) return null
  const standard = Number(match[1])
  return standard === 98 || standard === 3 ? 0 : standard
}
function languageMatch(name: string, language: string) {
  // These modes expect a function/library rather than the editor's normal main program.
  if (/ioi.?style|function|library/i.test(name)) return false
  if (language.startsWith('cpp')) return /c\+\+|g\+\+|gnu\+\+|clang\+\+|cpp|cxx/i.test(name)
  if (language === 'python3') return /python|pypy/i.test(name)
  if (language === 'java17') return /java|openjdk|jdk/i.test(name) && !/javascript/i.test(name)
  return true
}
function chooseCompiler(items: Compiler[], language: string): string {
  const matches = items.filter((item) => languageMatch(item.name, language))
  if (language.startsWith('cpp')) {
    const required = Number(language.slice(3))
    const exact = matches.find((item) => cppStandard(item.name) === required)
    const newer = matches.find((item) => (cppStandard(item.name) ?? 0) >= required)
    return String((exact || newer)?.id ?? '')
  }
  const exact = matches.find((item) =>
    language === 'python3'
      ? /(?:python|pypy)\s*3/i.test(item.name)
      : /(?:java|jdk)\s*17/i.test(item.name),
  )
  const newerJava =
    language === 'java17'
      ? matches.find((item) => {
          const version = item.name.match(/(?:java|openjdk|jdk)\s*(\d{1,2})(?!\d)/i)
          return version && Number(version[1]) >= 17
        })
      : undefined
  return String((exact || newerJava)?.id ?? '')
}
function allowedOfficialUrl(row: RemoteRow): string | undefined {
  if (!row.official_url) return
  try {
    const url = new URL(row.official_url)
    const platform = row.problem_id.split(':')[0]
    const hosts: Record<string, string[]> = {
      codeforces: ['codeforces.com', 'www.codeforces.com'],
      atcoder: ['atcoder.jp'],
      luogu: ['www.luogu.com.cn'],
      nowcoder: ['ac.nowcoder.com', 'www.nowcoder.com'],
    }
    if (
      url.protocol === 'https:' &&
      !url.username &&
      !url.password &&
      !url.port &&
      hosts[platform]?.includes(url.hostname)
    )
      return url.href
  } catch {
    /* Unexpected URLs remain plain data, never navigation targets. */
  }
}
function rowTitle(row: RemoteRow) {
  if (row.status === 'completed' && row.verdict === 'AC') return 'AC · 通过！'
  if (row.status === 'completed') return `${row.verdict || '判题完成'} · 请查看判题结果`
  if (row.status === 'queued') return '已排队，等待提交'
  if (row.status === 'submitting') return '正在向平台提交'
  if (row.status === 'judging') return '官方判题中'
  if (row.status === 'blocked') return '需要登录或验证'
  if (row.status === 'uncertain') return '发送结果未确认（不会自动重交）'
  if (row.status === 'failed') return '提交未完成'
  return row.status
}

export function RemoteSubmissionPanel({
  problemId,
  handles,
  language,
  code,
  save,
  onError,
  onChanged,
}: RemoteSubmissionPanelProps) {
  const [handle, setHandle] = useState(handles[0] || '')
  const [compilers, setCompilers] = useState<Compiler[]>([])
  const [compilerId, setCompilerId] = useState('')
  const [optionsReady, setOptionsReady] = useState(false)
  const [optionsBusy, setOptionsBusy] = useState(false)
  const [sendBusy, setSendBusy] = useState(false)
  const [rows, setRows] = useState<RemoteRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [listError, setListError] = useState('')
  const [intent, setIntent] = useState<Intent | null>(null)
  const [refreshingId, setRefreshingId] = useState('')
  const [dismissConfirmed, setDismissConfirmed] = useState<Record<string, boolean>>({})
  const mounted = useRef(true)
  const currentProblem = useRef(problemId)
  currentProblem.current = problemId
  const optionsGeneration = useRef(0)
  const listGeneration = useRef(0)
  const sending = useRef(false)
  const optionsLock = useRef(false)
  const outstanding = useRef<Intent | null>(null)
  const autoRefreshAfter = useRef<Record<string, number>>({})
  const callbacks = useRef({ save, onError, onChanged })
  callbacks.current = { save, onError, onChanged }
  const previousRows = useRef('')
  const handleKey = handles.join('\n')

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  useEffect(() => {
    setHandle((previous) => (handles.includes(previous) ? previous : handles[0] || ''))
  }, [handleKey])
  useEffect(() => {
    optionsGeneration.current++
    optionsLock.current = false
    setCompilers([])
    setCompilerId('')
    setOptionsReady(false)
    setOptionsBusy(false)
    setError('')
  }, [problemId, handle, language])
  const notifyChanged = () => {
    Promise.resolve()
      .then(() => callbacks.current.onChanged())
      .catch((error) => {
        if (mounted.current) setListError(`提交记录已保存，但工作台刷新失败：${errorText(error)}`)
      })
  }
  const loadRows = useCallback(async (target: string, initial = false) => {
    const generation = ++listGeneration.current
    if (initial) setLoading(true)
    try {
      const result = await api<{ items: RemoteRow[] }>(
        `/remote-submissions?${new URLSearchParams({ problem_id: target })}`,
      )
      if (
        !mounted.current ||
        currentProblem.current !== target ||
        generation !== listGeneration.current
      )
        return []
      const items = result.items || []
      setRows(items)
      setListError('')
      const signature = JSON.stringify(items)
      if (previousRows.current && previousRows.current !== signature) notifyChanged()
      previousRows.current = signature
      if (
        outstanding.current &&
        items.some((row) => row.request_key === outstanding.current?.request_key)
      ) {
        outstanding.current = null
        setIntent(null)
      }
      return items
    } catch (error) {
      if (
        mounted.current &&
        currentProblem.current === target &&
        generation === listGeneration.current
      )
        setListError(`读取提交记录失败：${errorText(error)}`)
      return []
    } finally {
      if (
        mounted.current &&
        currentProblem.current === target &&
        generation === listGeneration.current
      )
        setLoading(false)
    }
  }, [])
  useEffect(() => {
    setRows([])
    previousRows.current = ''
    setListError('')
    outstanding.current = null
    setIntent(null)
    void loadRows(problemId, true)
    return () => {
      listGeneration.current++
    }
  }, [problemId, loadRows])
  const needsPolling = rows.some(pending)
  useEffect(() => {
    if (!needsPolling) return
    let cancelled = false
    let timer: number
    const tick = async () => {
      const items = await loadRows(problemId)
      const now = Date.now()
      for (const row of items) {
        if (row.status !== 'judging' || !row.submission_id || !row.updated_at) continue
        if (now - row.updated_at * 1000 < 12000 || (autoRefreshAfter.current[row.id] || 0) > now)
          continue
        autoRefreshAfter.current[row.id] = now + 15000
        try {
          const result = await api<RemoteRow>(
            `/remote-submissions/${encodeURIComponent(row.id)}/refresh`,
            'POST',
            {},
          )
          if (cancelled || currentProblem.current !== problemId) return
          listGeneration.current++
          setRows((previous) => previous.map((item) => (item.id === result.id ? result : item)))
          notifyChanged()
        } catch {
          autoRefreshAfter.current[row.id] = Date.now() + 30000
        }
      }
      if (!cancelled) timer = window.setTimeout(() => void tick(), 3000)
    }
    timer = window.setTimeout(() => void tick(), 3000)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [needsPolling, problemId, loadRows])
  useEffect(() => {
    const guard = (event: Event) => {
      if (!sending.current && !outstanding.current) return
      event.preventDefault()
      setError(
        outstanding.current
          ? '本次发送尚未确认，请先查询本次提交后再切换题目或页面，避免重复交题'
          : '正在保存或发送代码，请稍候再切换',
      )
    }
    const unload = (event: BeforeUnloadEvent) => {
      if (sending.current || outstanding.current) {
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
  }, [])
  const report = (error: unknown) => {
    const message = errorText(error)
    setError(message)
    callbacks.current.onError(message)
  }
  async function fetchOptions() {
    if (!handle || optionsLock.current || sending.current || outstanding.current) return
    optionsLock.current = true
    const target = problemId
    const generation = ++optionsGeneration.current
    setOptionsBusy(true)
    setOptionsReady(false)
    setError('')
    try {
      const result = await api<{ items: Compiler[]; available: boolean }>(
        '/remote-submissions/options',
        'POST',
        { problem_id: target, handle },
      )
      if (
        !mounted.current ||
        generation !== optionsGeneration.current ||
        currentProblem.current !== target
      )
        return
      if (!result.available) throw new Error('此平台暂时无法代提交，请在官方页面提交后关联记录')
      const options = result.items.filter(
        (item) => String(item.id) && typeof item.name === 'string',
      )
      setCompilers(options)
      setCompilerId(chooseCompiler(options, language))
      setOptionsReady(true)
      if (!options.length) setError('平台未返回可用编译器，请确认 Cookie 登录有效，再重新读取')
    } catch (error) {
      if (mounted.current && generation === optionsGeneration.current) report(error)
    } finally {
      if (mounted.current && generation === optionsGeneration.current) {
        optionsLock.current = false
        setOptionsBusy(false)
      }
    }
  }
  async function send() {
    if (sending.current) return
    const target = problemId
    const generation = optionsGeneration.current
    sending.current = true
    setSendBusy(true)
    setError('')
    try {
      let request = outstanding.current
      if (!request) {
        if (!handle || !compilerId || !optionsReady) throw new Error('请先选择账号并读取平台编译器')
        if (!code.trim()) throw new Error('请先在编辑器中填写代码')
        const revision = await callbacks.current.save()
        if (revision === null || revision < 1)
          throw new Error('代码尚未保存成功，本次未发送到平台，请保存后重试')
        if (
          !mounted.current ||
          currentProblem.current !== target ||
          generation !== optionsGeneration.current
        )
          throw new Error('题目、账号或语言已切换，本次未发送，请重新选择编译器')
        request = {
          problem_id: target,
          handle,
          revision,
          compiler_id: compilerId,
          request_key: crypto.randomUUID(),
        }
        outstanding.current = request
        setIntent(request)
      }
      const row = await api<RemoteRow>('/remote-submissions', 'POST', request)
      if (!mounted.current || currentProblem.current !== target) return
      outstanding.current = null
      setIntent(null)
      listGeneration.current++
      setRows((previous) => [row, ...previous.filter((item) => item.id !== row.id)])
      notifyChanged()
      if (row.message && ['failed', 'blocked', 'uncertain'].includes(row.status))
        setError(row.message)
    } catch (error) {
      if (mounted.current && currentProblem.current === target) {
        // An explicit validation/authentication rejection never accepted this request.
        if (error instanceof ApiError && [400, 401, 403, 404, 422].includes(error.status)) {
          outstanding.current = null
          setIntent(null)
          report(error)
        } else
          report(
            outstanding.current
              ? new Error(
                  `发送结果尚未确认：${errorText(error)}。点击“查询本次提交”将使用相同请求标识，不会重新保存或重复交题。`,
                )
              : error,
          )
      }
    } finally {
      sending.current = false
      if (mounted.current && currentProblem.current === target) setSendBusy(false)
    }
  }
  async function refresh(row: RemoteRow) {
    if (refreshingId) return
    const target = problemId
    setRefreshingId(row.id)
    setError('')
    try {
      const result = await api<RemoteRow>(
        `/remote-submissions/${encodeURIComponent(row.id)}/refresh`,
        'POST',
        {},
      )
      if (!mounted.current || currentProblem.current !== target) return
      listGeneration.current++
      setRows((previous) => previous.map((item) => (item.id === result.id ? result : item)))
      notifyChanged()
    } catch (error) {
      if (mounted.current && currentProblem.current === target) report(error)
    } finally {
      if (mounted.current && currentProblem.current === target) setRefreshingId('')
    }
  }
  async function dismiss(row: RemoteRow) {
    if (refreshingId || !dismissConfirmed[row.id]) return
    const target = problemId
    setRefreshingId(row.id)
    setError('')
    try {
      const result = await api<RemoteRow>(
        `/remote-submissions/${encodeURIComponent(row.id)}/dismiss`,
        'POST',
        { confirmed_not_submitted: true },
      )
      if (!mounted.current || currentProblem.current !== target) return
      listGeneration.current++
      setRows((previous) => previous.map((item) => (item.id === result.id ? result : item)))
      setDismissConfirmed((previous) => ({ ...previous, [row.id]: false }))
      notifyChanged()
    } catch (error) {
      if (mounted.current && currentProblem.current === target) report(error)
    } finally {
      if (mounted.current && currentProblem.current === target) setRefreshingId('')
    }
  }
  const platform = problemId.split(':')[0]
  const supported = ['codeforces', 'atcoder'].includes(platform)
  const currentRows = rows.filter((row) =>
    platform === 'codeforces'
      ? row.handle.toLowerCase() === handle.toLowerCase()
      : row.handle === handle,
  )
  const active = currentRows.some((row) => pending(row) || uncertain(row))
  const matchingCompilers = compilers.filter((item) => languageMatch(item.name, language))
  const chosen = compilers.find((item) => String(item.id) === compilerId)
  const tooOld =
    language === 'cpp20' &&
    chosen &&
    cppStandard(chosen.name) !== null &&
    cppStandard(chosen.name)! < 20
  const disabledReason = !supported
    ? `${names[platform] || platform} 暂不支持本站代提交，请在官方提交页提交，再通过“本次提交与错因复盘”关联官方提交号。`
    : !handles.length
      ? '尚未配置此平台账号，请先前往连接与设置绑定 Cookie 并检测登录。'
      : loading
        ? '正在确认此题已有的提交记录，请稍候。'
        : listError
          ? '无法确认是否存在正在发送的提交，请先重试读取提交记录，避免重复交题。'
          : active && !intent
            ? '本账号已有进行中或发送结果未确认的提交，请先查看或刷新下方判题结果。'
            : !optionsReady && !intent
              ? '请先读取平台编译器；此步骤会检查实际登录会话，填写账号不代表已经登录。'
              : !compilerId && !intent
                ? '请选择实际平台编译器，未自动选择标准不明或过低的版本。'
                : tooOld && !intent
                  ? '当前编辑器使用 C++20，请选择支持 C++20 或更新标准的编译器。'
                  : !code.trim() && !intent
                    ? '请先在编辑器中填写代码。'
                    : ''
  return (
    <section className="remote-submission" aria-label="真实平台提交">
      <h3>
        <Send size={18} />
        提交到 {names[problemId.split(':')[0]] || '算法平台'}
      </h3>
      <p className="muted">
        使用你绑定的登录会话提交代码。发送服务器已保存的版本，随后可继续编辑；提交中的代码不会被新修改覆盖。
      </p>
      <div className="remote-submit-controls">
        <label>
          平台账号
          <select
            aria-label="代提交平台账号"
            value={handle}
            disabled={sendBusy || !!intent}
            onChange={(event) => setHandle(event.target.value)}
          >
            {!handles.length && <option value="">尚未绑定账号</option>}
            {handles.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={!supported || !handle || optionsBusy || sendBusy || !!intent}
          onClick={() => void fetchOptions()}
        >
          <RefreshCw size={15} />
          {optionsBusy ? '正在检查登录和编译器…' : '读取平台编译器'}
        </button>
        <label>
          实际平台编译器
          <select
            aria-label="实际平台编译器"
            value={compilerId}
            disabled={!optionsReady || sendBusy || !!intent}
            onChange={(event) => setCompilerId(event.target.value)}
          >
            <option value="">请选择平台编译器</option>
            {matchingCompilers.map((item) => (
              <option key={item.id} value={String(item.id)}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="remote-send-primary"
          disabled={sendBusy || (!!disabledReason && !intent)}
          onClick={() => void send()}
        >
          <Send size={16} />
          {sendBusy ? '正在保存并提交…' : intent ? '查询本次提交' : '提交到平台'}
        </button>
      </div>
      {disabledReason && (
        <p className="muted" role="status">
          {disabledReason}
        </p>
      )}
      {optionsReady && !matchingCompilers.length && (
        <p className="remote-submit-error">
          平台未提供与当前编辑器语言匹配的编译器，请切换编辑器语言或使用官方提交页。
        </p>
      )}
      {intent && (
        <p className="remote-submit-warning">
          正在确认账号 {intent.handle} 的已保存版本 #{intent.revision}
          ；编辑器后续修改不会重新发送。本次请求标识已固定，请勿在官方页面重复提交。
        </p>
      )}
      {error && (
        <p role="alert" className="remote-submit-error">
          {error}
        </p>
      )}
      {(!handles.length || error) && (
        <p>
          <a href="#settings">前往连接与设置，更新 Cookie 并检测登录</a>
        </p>
      )}
      {listError && (
        <p role="alert" className="remote-submit-error">
          {listError}{' '}
          <button type="button" onClick={() => void loadRows(problemId, true)}>
            重试读取提交记录
          </button>
        </p>
      )}
      {rows.length > 0 && (
        <div className="remote-submit-records" aria-label="平台提交进度">
          {rows.map((row) => {
            const ac = row.status === 'completed' && row.verdict === 'AC'
            const failed =
              (row.status === 'completed' && !ac) ||
              row.status === 'failed' ||
              row.status === 'blocked'
            const url = allowedOfficialUrl(row)
            return (
              <article
                key={row.id}
                className={`remote-submit-record ${ac ? 'is-ac' : failed ? 'is-error' : ''}`}
              >
                <strong className="remote-submit-verdict" role="status">
                  {ac ? (
                    <CheckCircle2 size={20} />
                  ) : failed ? (
                    <XCircle size={20} />
                  ) : (
                    <RefreshCw size={18} className={pending(row) ? 'remote-status-spin' : ''} />
                  )}
                  {rowTitle(row)}
                </strong>
                <p className="muted">
                  {row.handle} · {row.revision ? `已保存版本 #${row.revision} · ` : ''}
                  {row.compiler_name ? `${row.compiler_name} · ` : ''}
                  {row.submission_id ? `官方提交 #${row.submission_id} · ` : ''}
                  {date(row.created_at)}
                </p>
                {row.message && <p>{row.message}</p>}
                <div className="remote-submit-controls">
                  <button type="button" disabled={!!refreshingId} onClick={() => void refresh(row)}>
                    {refreshingId === row.id ? '正在查询官方结果…' : '刷新判题'}
                  </button>
                  {url && (
                    <a href={url} target="_blank" rel="noopener noreferrer">
                      官方提交记录 <ExternalLink size={14} />
                    </a>
                  )}
                </div>
                {uncertain(row) && !row.submission_id && (
                  <div className="remote-submit-dismiss">
                    <label>
                      <input
                        type="checkbox"
                        checked={!!dismissConfirmed[row.id]}
                        disabled={!!refreshingId}
                        onChange={(event) =>
                          setDismissConfirmed((previous) => ({
                            ...previous,
                            [row.id]: event.target.checked,
                          }))
                        }
                      />
                      我已在官方记录中确认本次没有提交成功
                    </label>
                    <button
                      type="button"
                      disabled={!dismissConfirmed[row.id] || !!refreshingId}
                      onClick={() => void dismiss(row)}
                    >
                      解除本次提交
                    </button>
                    <small className="muted">
                      仅解除本站阻塞，不会向平台重新发送代码；请先检查官方记录，避免重复提交。
                    </small>
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

import React, { useEffect, useRef, useState } from 'react'
import { api, date } from './api'
import { Code2, Lightbulb, RefreshCw, Save, ExternalLink } from 'lucide-react'
type Row = Record<string, any>
type Props = {
  revision: number
  provider: string
  model: string
  aiPicker: React.ReactNode
  jobs: Row[]
  run: (path: string, payload?: Row) => Promise<any>
  onError: (s: string) => void
}

function download(code: string, name: string) {
  const url = URL.createObjectURL(new Blob([code], { type: 'text/plain;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

export function WorkbenchPage({ revision, provider, model, aiPicker, jobs, run, onError }: Props) {
  const [choices, setChoices] = useState<Row>({ items: [], recent: [], handles: [], profile: {} })
  const [pid, setPid] = useState(''),
    [manual, setManual] = useState('')
  const [data, setData] = useState<Row | null>(null),
    [code, setCode] = useState(''),
    [language, setLanguage] = useState('cpp17')
  const [saved, setSaved] = useState('尚未选择题目'),
    [busy, setBusy] = useState(false)
  const [consent, setConsent] = useState(false),
    [saveTick, setSaveTick] = useState(0)
  const draftRevision = useRef(0),
    dirty = useRef(false),
    saving = useRef(false),
    currentPid = useRef('')
  const active = jobs.some(
    (j) => j.kind.startsWith('wb_') && ['running', 'queued'].includes(j.status),
  )
  const fail = (e: unknown) => onError(e instanceof Error ? e.message : '请求失败')
  const loadChoices = () => api('/workbench').then(setChoices).catch(fail)
  const refresh = async (id = currentPid.current, initial = false) => {
    if (!id) return
    const result = await api(`/workbench/problem/${encodeURIComponent(id)}`)
    if (currentPid.current !== id) return
    setData(result)
    if (initial) {
      setCode(result.draft.code)
      setLanguage(result.draft.language)
      draftRevision.current = result.draft.revision
      dirty.current = false
      setSaved(result.draft.revision ? '已从服务器恢复' : '尚无草稿')
    }
  }
  useEffect(() => {
    loadChoices()
    refresh().catch(fail)
  }, [revision])
  useEffect(() => {
    currentPid.current = pid
  }, [pid])

  const save = async () => {
    if (!currentPid.current || saving.current) return false
    if (!dirty.current) return true
    saving.current = true
    setSaved('正在保存…')
    const id = currentPid.current,
      text = code,
      lang = language
    try {
      const result = await api(`/workbench/problem/${encodeURIComponent(id)}/draft`, 'PUT', {
        code: text,
        language: lang,
        revision: draftRevision.current,
      })
      draftRevision.current = result.revision
      // Edits may continue while the request is pending. Never mark newer edits as saved.
      if (editor.current.code === text && editor.current.language === lang) {
        dirty.current = false
        setSaved('已保存到个人数据库')
      } else {
        setSaved('还有未保存修改')
        setSaveTick((n) => n + 1)
      }
      return !dirty.current
    } catch (e) {
      setSaved('保存失败 · 请下载代码备份后重试')
      fail(e)
      return false
    } finally {
      saving.current = false
    }
  }
  const editor = useRef({ code, language })
  editor.current = { code, language }
  useEffect(() => {
    if (!dirty.current || !pid || busy) return
    const timer = setTimeout(() => {
      save()
    }, 1600)
    return () => clearTimeout(timer)
  }, [code, language, pid, saveTick, busy])
  useEffect(() => {
    const leave = (e: Event) => {
      if (dirty.current) {
        e.preventDefault()
        onError('草稿尚未保存，请先保存或下载代码后再离开工作台')
      }
    }
    window.addEventListener('acm:before-navigate', leave)
    const unload = (e: BeforeUnloadEvent) => {
      if (dirty.current) e.preventDefault()
    }
    window.addEventListener('beforeunload', unload)
    return () => {
      window.removeEventListener('beforeunload', unload)
      window.removeEventListener('acm:before-navigate', leave)
    }
  }, [])

  const choose = async (value: string) => {
    if (busy || saving.current) return
    if (dirty.current && !(await save())) return
    setBusy(true)
    try {
      const state = await api(`/workbench/resolve?problem=${encodeURIComponent(value)}`)
      currentPid.current = state.id
      setPid(state.id)
      setData(state)
      setCode(state.draft.code)
      setLanguage(state.draft.language)
      draftRevision.current = state.draft.revision
      dirty.current = false
      setSaved(state.draft.revision ? '已从服务器恢复' : '尚无草稿')
      if (!state.statement) await run('/workbench/open', { problem: state.id })
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }
  const snapshot = async () => {
    if (!(await save())) return
    setBusy(true)
    try {
      await api(`/workbench/problem/${encodeURIComponent(pid)}/snapshot`, 'POST', {
        code,
        language,
        revision: draftRevision.current,
      })
      await refresh()
      loadChoices()
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }
  const coach = (path: string) => {
    if (!consent || !model) {
      onError('请先选择模型，并同意将题面或所选代码快照交给 AI 分析')
      return
    }
    return run(path, { provider, model })
  }
  const statement = data?.statement
  const profile = choices.profile
  return (
    <div className="workbench">
      <section className="card wb-intro">
        <div>
          <h2>
            <Code2 size={20} /> 刷题工作台{' '}
            <span className="badge neutral">Codeforces · 第一阶段</span>
          </h2>
          <p>读题、独立思考、逐步提示、记录每次尝试。代码保存在你的个人空间。</p>
        </div>
        <p className="muted">
          当前流程：保存代码快照 → 前往 Codeforces 提交 →
          关联提交号核验判题。自动代提交与其他平台将在后续接入。
        </p>
        <div className="wb-metrics">
          <span>
            练习题数 <b>{profile.distinct_problems || 0}</b>
          </span>
          <span>
            代码快照 <b>{profile.local_snapshots || 0}</b>
          </span>
          <span>
            提示次数 <b>{profile.hints_requested || 0}</b>
          </span>
          <span>
            核验通过 <b>{profile.verified_verdicts?.AC || 0}</b>
          </span>
        </div>
      </section>
      <div className="wb-layout">
        <aside className="card wb-list">
          <h3>为你挑选</h3>
          <p className="muted">{choices.score_note}</p>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              choose(manual)
            }}
            className="wb-open"
          >
            <input
              aria-label="Codeforces 题号或链接"
              placeholder="4-A 或官方题目链接"
              value={manual}
              onChange={(e) => setManual(e.target.value)}
              maxLength={300}
            />
            <button disabled={busy}>打开</button>
          </form>
          {!choices.items.length && (
            <p>暂无候选。请在「今日练习」同步题库并生成 AI 推荐，或输入题号开始。</p>
          )}
          {choices.items.map((p: Row) => (
            <button
              key={p.id}
              disabled={busy}
              className={`wb-choice ${pid === p.id ? 'selected' : ''}`}
              onClick={() => choose(p.id)}
            >
              <span>
                <strong>
                  {p.problem_id} · {p.title}
                </strong>
                <b className="wb-score">{p.match_score}</b>
              </span>
              <small>
                {p.difficulty} · {p.recommendation_source}
              </small>
              <small>{p.reason}</small>
            </button>
          ))}
          {!!choices.recent.length && (
            <>
              <h3>最近草稿</h3>
              {choices.recent.map((p: Row) => (
                <button
                  className="wb-choice"
                  key={p.problem_id}
                  onClick={() => choose(p.problem_id)}
                  disabled={busy}
                >
                  {p.problem_id}
                  <small>{date(p.updated_at)}</small>
                </button>
              ))}
            </>
          )}
        </aside>
        <div className="wb-content">
          {!pid ? (
            <section className="card empty">
              <h3>选择一道题开始训练</h3>
              <p>先读题、观察样例，再打开右侧的代码编辑器。</p>
            </section>
          ) : (
            <>
              <section className="card wb-statement">
                <div className="wb-heading">
                  <h2>{statement?.title || pid}</h2>
                  <a href={data?.url} target="_blank" rel="noreferrer">
                    查看原题 <ExternalLink size={14} />
                  </a>
                  <button
                    className="secondary"
                    disabled={active}
                    onClick={() => run('/workbench/open', { problem: pid })}
                  >
                    <RefreshCw size={14} /> 刷新题面
                  </button>
                </div>
                {!statement ? (
                  <p>
                    题面尚未获取。任务状态见页面底部；平台要求浏览器验证时，可先打开原题阅读并继续写代码。
                  </p>
                ) : (
                  <>
                    <p className="muted">
                      {statement.time_limit} · {statement.memory_limit} · 抓取于{' '}
                      {date(data?.fetched_at)}
                    </p>
                    {statement.has_images && (
                      <p className="wb-warning">
                        题面包含图片，请同时查看原题。此版本以纯文本显示数学公式和题面。
                      </p>
                    )}
                    {statement.sections.map((s: string, i: number) => (
                      <p className="wb-text" key={i}>
                        {s}
                      </p>
                    ))}
                    {statement.samples.map((s: Row, i: number) => (
                      <div key={i} className="wb-samples">
                        <div>
                          <h4>样例输入 {i + 1}</h4>
                          <pre>{s.input}</pre>
                        </div>
                        <div>
                          <h4>样例输出 {i + 1}</h4>
                          <pre>{s.output}</pre>
                        </div>
                      </div>
                    ))}
                  </>
                )}
              </section>
              <section className="card wb-editor">
                <div className="wb-heading">
                  <h3>代码编辑器</h3>
                  <select
                    aria-label="代码语言"
                    disabled={busy}
                    value={language}
                    onChange={(e) => {
                      setLanguage(e.target.value)
                      dirty.current = true
                      setSaved('尚未保存')
                    }}
                  >
                    <option value="cpp17">C++17</option>
                    <option value="cpp20">C++20</option>
                    <option value="python3">Python 3</option>
                    <option value="java17">Java 17</option>
                  </select>
                  <span className="muted" role="status">
                    {saved}
                  </span>
                </div>
                <textarea
                  aria-label="源代码"
                  disabled={busy}
                  className="wb-code"
                  value={code}
                  spellCheck={false}
                  maxLength={65536}
                  onChange={(e) => {
                    setCode(e.target.value)
                    dirty.current = true
                    setSaved('尚未保存')
                  }}
                  onKeyDown={(e) => {
                    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
                      e.preventDefault()
                      save()
                    }
                    if (e.key === 'Tab') {
                      e.preventDefault()
                      const start = e.currentTarget.selectionStart,
                        end = e.currentTarget.selectionEnd
                      setCode(code.slice(0, start) + '    ' + code.slice(end))
                      dirty.current = true
                      setSaved('尚未保存')
                      const el = e.currentTarget
                      requestAnimationFrame(() => {
                        el.selectionStart = el.selectionEnd = start + 4
                      })
                    }
                  }}
                  placeholder="在这里编写你的解法。Tab 缩进，Ctrl/Cmd + S 保存。"
                />
                <div className="wb-heading">
                  <button className="secondary" onClick={save}>
                    <Save size={15} /> 保存草稿
                  </button>
                  <button
                    className="secondary"
                    onClick={() => download(code, pid.replace(':', '-') + '.txt')}
                  >
                    下载代码
                  </button>
                  <button
                    className="secondary"
                    disabled={busy || saving.current}
                    onClick={async () => {
                      download(code, pid.replace(':', '-') + '-backup.txt')
                      setBusy(true)
                      try {
                        await refresh(pid, true)
                      } catch (e) {
                        fail(e)
                      } finally {
                        setBusy(false)
                      }
                    }}
                  >
                    下载备份并恢复服务器草稿
                  </button>
                  <button disabled={busy || !code.trim()} onClick={snapshot}>
                    保存提交快照
                  </button>
                </div>
                <p className="muted">
                  最多 64 KiB；服务器不执行代码。保存快照后，请在 Codeforces
                  选择相应语言提交同一份代码。
                </p>
              </section>
              <section className="card wb-coach">
                <h3>
                  <Lightbulb size={18} /> AI 小提示
                </h3>
                {aiPicker}
                <label className="wb-consent">
                  <input
                    type="checkbox"
                    checked={consent}
                    onChange={(e) => setConsent(e.target.checked)}
                  />{' '}
                  我同意将本题题面和提示历史发送给所选
                  AI；点击错因复盘时，另发送该次代码快照及判题信息。
                </label>
                <p className="muted">
                  每次只给一个切入点，最多 8 次；先分析题目，再结合已有提示逐步引导。AI
                  可能判断有误，仍需自己验证。
                </p>
                {data?.hints.map((h: Row, i: number) => (
                  <article className="wb-hint" key={i}>
                    <strong>
                      提示 {i + 1} · {h.focus}
                    </strong>
                    <p>{h.hint}</p>
                    <p className="muted">想一想：{h.question}</p>
                  </article>
                ))}
                <button
                  disabled={!consent || !model || active || !statement || data?.hints.length >= 8}
                  onClick={() => coach(`/workbench/problem/${encodeURIComponent(pid)}/hint`)}
                >
                  给我一个小提示 {data?.hints.length || 0}/8
                </button>
              </section>
              <section className="card">
                <h3>提交快照与错因复盘</h3>
                <p className="muted">
                  平台账号、题号和时间可核验；当前无法核验平台实际源码是否与快照一致，复盘仅针对保存的代码。
                </p>
                {!data?.attempts.length && (
                  <p>尚无提交快照。完成代码后点击上方「保存提交快照」。</p>
                )}
                {data?.attempts.map((attempt: Row) => (
                  <Attempt
                    key={attempt.id}
                    attempt={attempt}
                    handles={choices.handles}
                    provider={provider}
                    model={model}
                    active={active}
                    consent={consent && !!model}
                    run={run}
                    coach={coach}
                    onError={onError}
                  />
                ))}
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function Attempt({
  attempt: a,
  handles,
  provider,
  model,
  active,
  consent,
  run,
  coach,
  onError,
}: any) {
  const [handle, setHandle] = useState(a.handle || handles[0] || ''),
    [sid, setSid] = useState(a.submission_id ? String(a.submission_id) : '')
  const [autoReview, setAutoReview] = useState(false)
  const [contest, index] = a.problem_id.split(':')[1].split('-')
  return (
    <article className="wb-attempt">
      <div className="wb-heading">
        <strong>{a.verdict}</strong>
        <span>
          {date(a.created_at)} · {a.language}
        </span>
        <code title={a.code_hash}>SHA256 {a.code_hash.slice(0, 12)}</code>
      </div>
      <div className="wb-heading">
        <button className="secondary" onClick={() => download(a.code, `attempt-${a.id}.txt`)}>
          下载本次代码
        </button>
        <a
          className="button"
          href={`https://codeforces.com/problemset/submit?contestId=${contest}&submittedProblemIndex=${index}`}
          target="_blank"
          rel="noreferrer"
        >
          前往官方提交页 ↗
        </a>
      </div>
      <details>
        <summary>查看不可变代码快照</summary>
        <pre className="wb-snapshot">{a.code}</pre>
      </details>
      <form
        className="wb-heading"
        onSubmit={(e) => {
          e.preventDefault()
          if (!/^\d+$/.test(sid)) {
            onError('请输入有效提交号')
            return
          }
          run(`/workbench/attempt/${a.id}/sync`, {
            handle,
            submission_id: Number(sid),
            review: autoReview && consent ? { provider, model } : null,
          })
        }}
      >
        <select
          aria-label="提交账号"
          value={handle}
          onChange={(e) => setHandle(e.target.value)}
          disabled={!!a.handle}
        >
          <option value="">选择已配置账号</option>
          {handles.map((h: string) => (
            <option key={h} value={h}>
              {h}
            </option>
          ))}
        </select>
        <input
          aria-label="平台提交号"
          inputMode="numeric"
          placeholder="Codeforces 提交号"
          value={sid}
          onChange={(e) => setSid(e.target.value)}
          disabled={!!a.submission_id}
        />
        <button className="secondary" disabled={active || !handle || !sid}>
          {a.submission_id ? '刷新判题（间隔 20 秒）' : '关联并核验判题'}
        </button>
      </form>
      <label className="wb-consent">
        <input
          type="checkbox"
          disabled={!consent}
          checked={autoReview}
          onChange={(e) => setAutoReview(e.target.checked)}
        />{' '}
        本次同步得到未通过结果时，自动发送此快照进行 AI 复盘
      </label>
      {a.judge_data && (
        <p className="muted">
          官方结果：{a.judge_data.verdict || '判题中'} · 已过测试{' '}
          {a.judge_data.passedTestCount ?? '未知'} · {a.judge_data.timeConsumedMillis ?? '?'} ms ·{' '}
          {Math.round((a.judge_data.memoryConsumedBytes || 0) / 1024)} KiB{' '}
          <a
            href={`https://codeforces.com/contest/${contest}/submission/${a.submission_id}`}
            target="_blank"
            rel="noreferrer"
          >
            查看记录 ↗
          </a>
        </p>
      )}
      {!['UNLINKED', 'PENDING', 'AC'].includes(a.verdict) && (
        <button
          className="secondary"
          disabled={!consent || active}
          onClick={() => coach(`/workbench/attempt/${a.id}/review`)}
        >
          分析本次错因（不提供答案）
        </button>
      )}
      {a.review && (
        <div className="wb-hint">
          <strong>
            AI 推测 · {a.review.category} · 置信度 {a.review.confidence}
            {a.review.line ? ` · 第 ${a.review.line} 行` : ''}
          </strong>
          <p>{a.review.evidence}</p>
          <p>请自查：{a.review.question}</p>
        </div>
      )}
    </article>
  )
}

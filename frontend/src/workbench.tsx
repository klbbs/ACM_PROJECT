import React, { useEffect, useRef, useState } from 'react'
import { api, date, names } from './api'
const StatementView = React.lazy(() =>
  import('./statement-view').then((m) => ({ default: m.StatementView })),
)
import { EditorShell } from './editor-shell'
import { SubmissionHistory, SubmissionForm } from './submission-history'
import { SelfTestPanel } from './self-test-panel'
import './editor-self-test.css'
import { defaultEditorSettings, type EditorSettings } from './editor-settings'
import { TranslationPanel } from './translation-panel'
import { Code2, Lightbulb, RefreshCw, ExternalLink } from 'lucide-react'
type Row = Record<string, any>
type Props = {
  revision: number
  initialSelection?: Row | null
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

export function WorkbenchPage({
  revision,
  provider,
  model,
  aiPicker,
  jobs,
  run,
  onError,
  initialSelection,
}: Props) {
  const [listCollapsed, setListCollapsed] = useState(false),
    [importHtml, setImportHtml] = useState('')
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
  const [automaticCoach, setAutomaticCoach] = useState<Row>({
    enabled: false,
    provider: 'api',
    model: '',
  })
  const [coachSettingsReady, setCoachSettingsReady] = useState(false)
  const [coachSettingsBusy, setCoachSettingsBusy] = useState(false)
  const [editorSettings, setEditorSettings] = useState<EditorSettings>(defaultEditorSettings)
  const [settingsReady, setSettingsReady] = useState(false),
    [settingsBusy, setSettingsBusy] = useState(false)
  const [editorFullscreen, setEditorFullscreen] = useState(false)
  useEffect(() => {
    let alive = true
    api('/workbench/coach-settings')
      .then((value) => {
        if (alive) {
          setAutomaticCoach(value)
          setCoachSettingsReady(true)
        }
      })
      .catch((e) => {
        if (alive) onError(e.message)
      })
    api<EditorSettings>('/workbench/editor-settings')
      .then((value) => {
        if (alive) {
          setEditorSettings(value)
          setSettingsReady(true)
        }
      })
      .catch((e) => {
        if (alive) onError(e.message)
      })
    return () => {
      alive = false
    }
  }, [])
  const toggleAutomaticCoach = async (enabled: boolean) => {
    const previous = automaticCoach
    setAutomaticCoach({ ...previous, enabled })
    setCoachSettingsBusy(true)
    try {
      setAutomaticCoach(
        await api('/workbench/coach-settings', 'PUT', {
          enabled,
          provider,
          model,
          consent: enabled,
        }),
      )
    } catch (e) {
      setAutomaticCoach(previous)
      onError(e instanceof Error ? e.message : '自动提示设置保存失败')
    } finally {
      setCoachSettingsBusy(false)
    }
  }
  useEffect(() => {
    if (!editorFullscreen) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [editorFullscreen])
  const updateSettings = async (patch: Partial<EditorSettings>) => {
    setSettingsBusy(true)
    try {
      const saved = await api<EditorSettings>('/workbench/editor-settings', 'PUT', {
        ...editorSettings,
        ...patch,
      })
      setEditorSettings(saved)
    } catch (e) {
      onError(e instanceof Error ? e.message : '编辑器设置保存失败')
    } finally {
      setSettingsBusy(false)
    }
  }
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
      setSaved('自动保存失败 · 请保留当前页面并使用 Ctrl/Cmd+S 重试')
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
        onError('草稿尚未自动保存，请使用 Ctrl/Cmd+S 保存后再离开工作台')
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
      if (!state.statement || !state.statement.render_version)
        await run('/workbench/open', { problem: state.id })
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }
  useEffect(() => {
    if (initialSelection?.problem_id) void choose(initialSelection.problem_id)
  }, [initialSelection])
  const submit = async (values: Row) => {
    if (await save())
      await run(`/workbench/problem/${encodeURIComponent(pid)}/submission`, {
        ...values,
        revision: draftRevision.current,
      })
  }
  const coach = (path: string) => {
    if (!consent || !model) {
      onError('请先选择模型，并同意将题面或所选提交代码交给 AI 分析')
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
            <Code2 size={20} /> 刷题工作台 <span className="badge neutral">多平台训练</span>
          </h2>
          <p>读题、独立思考、逐步提示、记录每次尝试。代码保存在你的个人空间。</p>
        </div>
        <p className="muted">
          当前流程：自动保存代码 → 前往题目所属平台提交 → CF
          可即时核验，其他平台先同步账号信息，再用已采集的提交号关联判题。
        </p>
        <div className="wb-metrics">
          <span>
            练习题数 <b>{profile.distinct_problems || 0}</b>
          </span>
          <span>
            关联提交 <b>{profile.local_snapshots || 0}</b>
          </span>
          <span>
            提示次数 <b>{profile.hints_requested || 0}</b>
          </span>
          <span>
            核验通过 <b>{profile.verified_verdicts?.AC || 0}</b>
          </span>
        </div>
      </section>
      <div className={`wb-layout ${listCollapsed ? 'wb-list-collapsed' : ''}`}>
        <button className="secondary wb-list-toggle" onClick={() => setListCollapsed((v) => !v)}>
          {listCollapsed ? '展开选题栏' : '收起选题栏'}
        </button>
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
              aria-label="平台题号或官方题目链接"
              placeholder="4-A / atcoder:abc001_1 / luogu:P1001 / 牛客官方链接"
              value={manual}
              onChange={(e) => setManual(e.target.value)}
              maxLength={300}
            />
            <button disabled={busy}>打开</button>
          </form>
          {!choices.items.length && (
            <p>
              {choices.notice ||
                '暂无符合条件的候选。请检测平台登录状态、同步题库并检查发布日期设置，或输入题号开始。'}
            </p>
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
                {names[p.platform] || p.platform} ·{' '}
                {p.difficulty ?? p.native_difficulty ?? '未标定难度'} · {p.recommendation_source}
              </small>
              {p.difficulty_source && <small>{p.difficulty_source}</small>}
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
                {data?.fetch_status?.status === 'failed' && (
                  <div className="wb-warning" role="alert">
                    {data.fetch_status.error}
                  </div>
                )}
                <details className="wb-import">
                  <summary>获取失败？导入浏览器题面 / 查看教程</summary>
                  <p>
                    打开原题，在 F12 元素面板复制包含完整正文和样例的节点 Outer
                    HTML，粘贴下方。只提取文本和样例；手动导入无法核验题号。
                  </p>
                  <button
                    className="text-button"
                    onClick={() => window.dispatchEvent(new Event('acm:open-guide'))}
                  >
                    查看详细教程
                  </button>
                  <textarea
                    aria-label="题面 HTML"
                    value={importHtml}
                    maxLength={200000}
                    onChange={(e) => setImportHtml(e.target.value)}
                  />
                  <button
                    disabled={!importHtml || busy}
                    onClick={async () => {
                      setBusy(true)
                      try {
                        await api(`/workbench/problem/${encodeURIComponent(pid)}/import`, 'POST', {
                          html: importHtml,
                        })
                        setImportHtml('')
                        await refresh()
                      } catch (e) {
                        fail(e)
                      } finally {
                        setBusy(false)
                      }
                    }}
                  >
                    导入本题文本与样例
                  </button>
                </details>
                {statement?.source === 'user_import' && (
                  <p className="wb-warning">手动导入题面：请自行确认与当前题号一致。</p>
                )}
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
                  <TranslationPanel
                    problemId={pid}
                    statement={statement}
                    provider={provider}
                    model={model}
                    revision={revision}
                    jobs={jobs}
                    run={run}
                    onError={onError}
                  >
                    <>
                      <p className="muted">
                        {statement.time_limit} · {statement.memory_limit} · 抓取于{' '}
                        {date(data?.fetched_at)}
                      </p>
                      <React.Suspense fallback={<p>正在排版题面…</p>}>
                        <StatementView statement={statement} />
                      </React.Suspense>
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
                  </TranslationPanel>
                )}
              </section>
              <section
                className={`card wb-editor ${editorFullscreen ? 'wb-editor-fullscreen' : ''}`}
              >
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
                <div className="wb-editor-controls">
                  <label>
                    键位
                    <select
                      aria-label="编辑器键位"
                      value={editorSettings.keymap}
                      disabled={!settingsReady || settingsBusy}
                      onChange={(e) =>
                        updateSettings({ keymap: e.target.value as 'standard' | 'vim' })
                      }
                    >
                      <option value="standard">普通</option>
                      <option value="vim">Vim</option>
                    </select>
                  </label>
                  <label>
                    字号
                    <select
                      aria-label="编辑器字号"
                      value={editorSettings.font_size}
                      disabled={!settingsReady || settingsBusy}
                      onChange={(e) => updateSettings({ font_size: Number(e.target.value) })}
                    >
                      {Array.from({ length: 13 }, (_, i) => i + 12).map((size) => (
                        <option key={size} value={size}>
                          {size}px
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="wb-wrap">
                    <input
                      type="checkbox"
                      checked={editorSettings.line_wrapping}
                      disabled={!settingsReady || settingsBusy}
                      onChange={(e) => updateSettings({ line_wrapping: e.target.checked })}
                    />
                    自动换行
                  </label>
                  <button
                    className="secondary"
                    aria-pressed={editorFullscreen}
                    onClick={() => setEditorFullscreen((v) => !v)}
                  >
                    {editorFullscreen ? '退出全屏' : '编辑器全屏'}
                  </button>
                  <span className="muted">
                    {settingsBusy
                      ? '保存偏好…'
                      : settingsReady
                        ? '偏好已按用户保存'
                        : '正在读取偏好'}
                  </span>
                </div>
                <EditorShell
                  key={pid}
                  documentId={pid}
                  value={code}
                  language={language}
                  settings={editorSettings}
                  readOnly={busy}
                  onChange={(value) => {
                    setCode(value)
                    dirty.current = true
                    setSaved('尚未保存')
                  }}
                  onSave={save}
                  onError={onError}
                />
                <SelfTestPanel
                  key={pid}
                  code={code}
                  language={language}
                  samples={statement?.samples || []}
                />
                <details className="wb-shortcuts">
                  <summary>编辑器快捷键与补全说明</summary>
                  <p>
                    Ctrl/Cmd+S 保存；Ctrl+Space 补全；方向键选择、Tab 接受、Enter 换行、Esc
                    关闭；无候选时 Tab 缩进或跳转片段占位符；Ctrl/Cmd+F 搜索、Ctrl+H 替换。Vim
                    模式使用 :w 保存，:q、:wq、:x 不退出网站。全屏请使用「退出全屏」按钮，Esc 保留给
                    Vim 和补全。
                  </p>
                  <p>
                    候选来自关键字、常用名称、基础语法片段和当前文件单词，不是类型推断或编译诊断；补全不调用
                    AI，也不发送源码。
                    <a href="/third-party-notices.txt" target="_blank" rel="noreferrer">
                      开源许可
                    </a>
                  </p>
                </details>
                <div className="wb-heading">
                  <button
                    className="secondary"
                    disabled={!code.trim()}
                    onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(code)
                        setSaved('代码已复制，请前往官方提交页粘贴')
                      } catch {
                        onError('浏览器未允许复制，请在编辑器中 Ctrl/Cmd+A、Ctrl/Cmd+C 复制代码')
                      }
                    }}
                  >
                    复制待交代码
                  </button>
                  <a
                    className="button"
                    href={data?.capabilities?.official_submit_url || data?.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    前往官方提交页 ↗
                  </a>
                </div>
                <p className="muted">
                  最多 64 KiB；代码自动保存，自测由专用隔离服务执行。请在题目所属平台
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
                  AI；点击错因复盘时，另发送该次提交代码及判题信息。
                </label>
                <p className="muted">
                  每次只给一个切入点，最多 8 次；先分析题目，再结合已有提示逐步引导。AI
                  可能判断有误，仍需自己验证。
                </p>
                <div className="wb-auto-coach">
                  <label className="wb-consent">
                    <input
                      type="checkbox"
                      aria-label="开启自动错误提示"
                      checked={automaticCoach.enabled}
                      disabled={
                        !coachSettingsReady ||
                        coachSettingsBusy ||
                        (!automaticCoach.enabled && !model)
                      }
                      onChange={(e) => void toggleAutomaticCoach(e.target.checked)}
                    />
                    开启自动错误提示：我同意在关联或刷新官方错误结果后，将题面、当次代码及判题信息发送给
                    AI。
                  </label>
                  <p className="muted" role="status">
                    {coachSettingsBusy
                      ? '正在保存自动提示设置…'
                      : automaticCoach.enabled
                        ? `已开启 · 使用 ${automaticCoach.provider} / ${automaticCoach.model}；更换模型请关闭后重新开启。`
                        : '自动提示已关闭，不会自动发送代码。仍可手动获取小提示或分析错因。'}
                  </p>
                  <p className="muted">
                    仅对已核验的
                    WA、超时、编译或运行错误给一个排查方向，不给答案。同一份代码和判题结果只自动分析一次；关闭会取消在途分析，已发送的数据无法撤回。
                  </p>
                </div>
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
                <h3>本次提交与错因复盘</h3>
                <p className="muted">
                  平台账号、题号和时间可核验；当次代码仅在你明确确认并关联结果时保存，用于错因复盘，无法核验与平台实际源码相同。
                </p>
                <SubmissionForm
                  handles={
                    choices.handles_by_platform?.[pid.split(':')[0]] ||
                    (pid.startsWith('codeforces:') ? choices.handles : [])
                  }
                  code={code}
                  language={language}
                  active={active || busy}
                  onSubmit={submit}
                />
                <SubmissionHistory
                  problemId={pid}
                  revision={revision}
                  selected={initialSelection}
                  onError={onError}
                />
                {data?.attempts.map((attempt: Row) => (
                  <Attempt
                    key={attempt.id}
                    attempt={attempt}
                    handles={
                      choices.handles_by_platform?.[pid.split(':')[0]] ||
                      (pid.startsWith('codeforces:') ? choices.handles : [])
                    }
                    capabilities={data?.capabilities}
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

function Attempt({ attempt: a, handles, capabilities, active, consent, run, coach, onError }: any) {
  const [handle, setHandle] = useState(a.handle || handles[0] || ''),
    [sid, setSid] = useState(a.submission_id ? String(a.submission_id) : '')
  useEffect(() => {
    setHandle((previous: string) => {
      if (a.handle)
        return handles.find((h: string) => h.toLowerCase() === a.handle.toLowerCase()) || a.handle
      return handles.includes(previous) ? previous : handles[0] || ''
    })
  }, [a.handle, handles])
  const [contest, index] = a.problem_id.split(':')[1].split('-')
  const platform = a.problem_id.split(':')[0]
  const platformName = names[platform] || platform
  const canSync = capabilities?.judge_sync ?? platform === 'codeforces'
  return (
    <article className="wb-attempt">
      <div className="wb-warning">
        <strong>当前为官方页面手动提交</strong>
        <p>
          请先登录 {platformName}，确认使用正确的账号，再提交本次代码。关联记录不会远程提交代码。
        </p>
        <a
          href={
            (
              {
                codeforces: 'https://codeforces.com/enter',
                atcoder: 'https://atcoder.jp/login',
                luogu: 'https://www.luogu.com.cn/',
                nowcoder: 'https://ac.nowcoder.com/',
              } as Record<string, string>
            )[platform]
          }
          target="_blank"
          rel="noreferrer"
        >
          先登录 {platformName} ↗
        </a>{' '}
        ·{' '}
        <button
          className="text-button"
          onClick={() => window.dispatchEvent(new Event('acm:open-guide'))}
        >
          绑定与提交教程
        </button>
        {canSync && !handles.length && (
          <p>尚未配置此平台账号，请在「连接与设置」添加 handle 后再关联判题。</p>
        )}
      </div>
      <div className="wb-heading">
        <strong>{a.verdict}</strong>
        <span>
          {date(a.created_at)} · {a.language}
        </span>
      </div>
      <div className="wb-heading">
        <button className="secondary" onClick={() => download(a.code, `attempt-${a.id}.txt`)}>
          下载本次代码
        </button>
        <a
          className="button"
          href={
            capabilities?.official_submit_url ||
            `https://codeforces.com/problemset/submit?contestId=${contest}&submittedProblemIndex=${index}`
          }
          target="_blank"
          rel="noreferrer"
        >
          前往官方提交页 ↗
        </a>
      </div>
      <details>
        <summary>查看当次代码（只读）</summary>
        <p className="muted">
          用户关联时提供的代码，未与官方平台源码核对；编辑器修改不会改变这份记录。
        </p>
        <pre className="wb-snapshot">{a.code}</pre>
      </details>
      {canSync && (
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
            {a.handle &&
              !handles.some((h: string) => h.toLowerCase() === a.handle.toLowerCase()) && (
                <option value={a.handle}>{a.handle}（已关联；需重新配置账号后同步）</option>
              )}
            {handles.map((h: string) => (
              <option key={h} value={h}>
                {h}
              </option>
            ))}
          </select>
          <input
            aria-label="平台提交号"
            inputMode="numeric"
            placeholder={`${platformName} 提交号`}
            value={sid}
            onChange={(e) => setSid(e.target.value)}
            disabled={!!a.submission_id}
          />
          <button className="secondary" disabled={active || !handle || !sid}>
            {a.submission_id ? '刷新判题（间隔 20 秒）' : '关联并核验判题'}
          </button>
        </form>
      )}
      {!canSync && (
        <p className="muted">
          {capabilities?.notice || '请在官方平台查看判题记录；同步账号信息后可更新训练统计。'}
        </p>
      )}
      {a.judge_data && (
        <p className="muted">
          官方结果：{a.judge_data.verdict || '判题中'} · 已过测试{' '}
          {a.judge_data.passedTestCount ?? '未知'} · {a.judge_data.timeConsumedMillis ?? '?'} ms ·{' '}
          {Math.round((a.judge_data.memoryConsumedBytes || 0) / 1024)} KiB{' '}
          {platform === 'codeforces' && (
            <a
              href={`https://codeforces.com/contest/${contest}/submission/${a.submission_id}`}
              target="_blank"
              rel="noreferrer"
            >
              查看记录 ↗
            </a>
          )}
        </p>
      )}
      {[
        'WA',
        'RE',
        'CE',
        'TLE',
        'MLE',
        'OLE',
        'PE',
        'FAIL',
        'PARTIAL',
        'IDLENESS_LIMIT_EXCEEDED',
        'PRESENTATION_ERROR',
        'FAILED',
      ].includes(a.verdict) && (
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

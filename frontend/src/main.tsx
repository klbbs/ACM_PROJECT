import React, { useCallback, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  BarChart3,
  BookOpen,
  Bot,
  Check,
  CheckCheck,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Code2,
  Database,
  ExternalLink,
  Flame,
  Github,
  KeyRound,
  Layers3,
  LoaderCircle,
  LogIn,
  Menu,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Target,
  TrendingUp,
  X,
} from 'lucide-react'
import { api, date, fmt, names, shortDate } from './api'
import './style.css'
import { PlatformGuide } from './platform-guide'
import { ExtensionGuide } from './extension-guide'
import { AnnouncementBanner, AnnouncementPage, useAnnouncements } from './announcements'
import { UserLogin, MemberPage } from './members'
import { WorkbenchPage } from './workbench'
import { OnlineUsers } from './online-users'
import { PlatformConnections } from './platform-connections'
import { AdminPage } from './admin'
import { DailyTasksPage } from './daily-tasks'
import { SocialPage } from './social'
import { nav, navigationGroups, availableNavigation, type Page } from './navigation'
import { MainDirectory } from './main-directory'
import { CatalogSyncPanel } from './catalog-sync'

type Row = Record<string, any>
const statusNames: Record<string, string> = {
  queued: '等待中',
  running: '执行中',
  completed: '已完成',
  partial: '部分完成',
  failed: '执行失败',
  interrupted: '已中断',
}
const jobNames: Record<string, string> = {
  crawl: '同步账号',
  analysis: '训练建议',
  recommend: '智能选题',
  catalog: '同步题库',
  ai_test: 'AI 连接测试',
  wb_statement: '获取题面',
  wb_hint: '生成小提示',
  wb_sync: '核验判题',
  wb_review: '分析错因',
  translation: '翻译题面',
}
const emptyOverview: Row = {
  stats: {},
  platforms: [],
  topics: [],
  recent: [],
  trend: [],
  verdicts: {},
  coverage: [],
}
function IconLabel({ icon: Icon, children }: any) {
  return (
    <span className="inline">
      <Icon size={16} />
      {children}
    </span>
  )
}
function Platform({ platform, small = false }: { platform: string; small?: boolean }) {
  return (
    <span className={`platform-icon ${platform} ${small ? 'small' : ''}`}>
      {platform === 'codeforces' ? (
        <BarChart3 size={small ? 14 : 22} />
      ) : platform === 'luogu' ? (
        '洛'
      ) : platform === 'nowcoder' ? (
        '牛'
      ) : (
        'A'
      )}
    </span>
  )
}
function Badge({ verdict }: { verdict: string }) {
  return (
    <span
      className={`badge ${verdict === 'AC' ? 'success' : verdict === 'PENDING' ? 'warning' : 'neutral'}`}
    >
      {verdict === 'AC' ? <Check size={12} /> : null}
      {verdict}
    </span>
  )
}
function Empty({
  title,
  detail,
  action,
}: {
  title: string
  detail: string
  action?: React.ReactNode
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Layers3 size={28} />
      </div>
      <h3>{title}</h3>
      <p>{detail}</p>
      {action}
    </div>
  )
}
function Button({
  children,
  loading = false,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { loading?: boolean }) {
  return (
    <button {...props} disabled={props.disabled || loading}>
      {loading && <LoaderCircle size={15} className="spin" />}
      {children}
    </button>
  )
}

function App() {
  const [auth, setAuth] = useState<Row | null>(null),
    [token, setToken] = useState('')
  const [page, setPageRaw] = useState<Page>('home'),
    [mobile, setMobile] = useState(false),
    [days, setDays] = useState(30),
    [platform, setPlatform] = useState('')
  const setPage = (next: Page) => {
    if (window.dispatchEvent(new Event('acm:before-navigate', { cancelable: true }))) {
      window.scrollTo({ top: 0, left: 0, behavior: 'instant' })
      setPageRaw(next)
    }
  }
  const [workbenchSelection, setWorkbenchSelection] = useState<Row | null>(null)
  const openSubmission = (row: Row) => {
    if (window.dispatchEvent(new Event('acm:before-navigate', { cancelable: true }))) {
      setWorkbenchSelection({
        ...row,
        problem_id: row.problem_id.startsWith(row.platform + ':')
          ? row.problem_id
          : row.platform + ':' + row.problem_id,
        submission_id: row.id,
      })
      setPageRaw('workbench')
    }
  }
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [guideOpen, setGuideOpen] = useState(false)
  const [guideRequest, setGuideRequest] = useState(0)
  const openGuide = () => {
    if (!window.dispatchEvent(new Event('acm:before-navigate', { cancelable: true }))) return
    setPageRaw('settings')
    setGuideOpen(true)
    setGuideRequest((n) => n + 1)
  }
  const [overview, setOverview] = useState<Row>(emptyOverview),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(''),
    [notice, setNotice] = useState('')
  const [jobs, setJobs] = useState<Row[]>([]),
    [revision, setRevision] = useState(0),
    [search, setSearch] = useState(''),
    [globalSearch, setGlobalSearch] = useState('')
  const [jobsReadError, setJobsReadError] = useState('')
  const seenJobs = useRef<Record<string, string>>({})
  const [provider, setProvider] = useState('codex'),
    [model, setModel] = useState(''),
    [models, setModels] = useState<Row[]>([])
  const [reports, setReports] = useState<Row[]>([]),
    [report, setReport] = useState<Row | null>(null),
    [goal, setGoal] = useState('提升比赛稳定性，发现薄弱算法，并制定可执行的训练计划。')
  const [modelBusy, setModelBusy] = useState(false),
    [modelNote, setModelNote] = useState('')
  const unlocked = auth?.authenticated
  const announcements = useAnnouncements(Boolean(unlocked))
  useEffect(() => {
    const expired = () => {
      setAuth(null)
      window.location.reload()
    }
    window.addEventListener('acm:session-expired', expired)
    return () => window.removeEventListener('acm:session-expired', expired)
  }, [])
  useEffect(() => {
    if (window.location.hash === '#platform-guide') openGuide()
    const guide = () => openGuide()
    const settings = () => setPage('settings')
    window.addEventListener('acm:open-guide', guide)
    window.addEventListener('acm:open-settings', settings)
    api('/auth/status')
      .then(setAuth)
      .catch((e) => setError(e.message))
    return () => {
      window.removeEventListener('acm:open-guide', guide)
      window.removeEventListener('acm:open-settings', settings)
    }
  }, [])
  useEffect(() => {
    if (auth?.authenticated)
      api('/ai/settings')
        .then((c) => {
          if (!c.codex_enabled) chooseProvider('api')
        })
        .catch(() => {})
  }, [auth?.authenticated])
  const reload = useCallback(() => {
    setRevision((n) => n + 1)
  }, [])
  useEffect(() => {
    if (!unlocked) return
    let cancelled = false
    setLoading(true)
    api(`/overview?days=${days}&platform=${platform}`)
      .then((data) => {
        if (!cancelled) setOverview(data)
      })
      .catch((e) => {
        if (!cancelled) setError(e.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [unlocked, days, platform, revision])
  useEffect(() => {
    if (!unlocked) return
    let alive = true
    const poll = () =>
      api('/jobs')
        .then((data) => {
          if (!alive) return
          setJobsReadError('')
          let changed = false
          for (const job of data.items) {
            if (
              seenJobs.current[job.id] &&
              seenJobs.current[job.id] !== job.status &&
              !['running', 'queued'].includes(job.status)
            ) {
              changed = true
              if (job.error || job.result?.review_error)
                setError(job.error || job.result.review_error)
              else setNotice(`${jobNames[job.kind]}${statusNames[job.status]}`)
            }
            seenJobs.current[job.id] = job.status
          }
          setJobs(data.items)
          if (changed) reload()
        })
        .catch((e) => {
          if (alive) setJobsReadError(e.message)
        })
    poll()
    const timer = setInterval(poll, 3500)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [unlocked, reload])
  useEffect(() => {
    if (!unlocked) return
    api('/reports')
      .then((data) => {
        setReports(data.items)
        if (data.items.length) api(`/reports/${data.items[0].id}`).then(setReport)
      })
      .catch((e) => setError(e.message))
  }, [unlocked, revision])
  useEffect(() => {
    if (notice) {
      const t = setTimeout(() => setNotice(''), 6500)
      return () => clearTimeout(t)
    }
  }, [notice])
  const run = async (path: string, payload?: Row) => {
    try {
      setError('')
      const created = await api(path, 'POST', payload ?? {})
      if (created.id) seenJobs.current[created.id] = 'queued'
      setNotice(
        path === '/catalog/refresh'
          ? '题库同步已提交，可在“真实候选题目”查看分平台进度'
          : '任务已加入队列，可在页面底部查看进度',
      )
      try {
        const data = await api('/jobs')
        setJobs(data.items)
        setJobsReadError('')
      } catch (e: any) {
        setJobsReadError(e.message)
        // The POST was accepted. A failed status read must not lose its job id
        // or encourage the user to submit the same work again.
      }
      return created
      // Keep newly queued status until the poll observes completion, including fast jobs.
    } catch (e: any) {
      setError(e.message)
      return { error: e.message }
    }
  }
  const running = (kind: string) =>
    jobs.some((j) => j.kind === kind && ['queued', 'running'].includes(j.status))
  const loadModels = async () => {
    setModelBusy(true)
    setError('')
    try {
      const data = await api(`/ai/models?provider=${provider}`)
      setModels(data.items)
      setModelNote(data.note)
      if (!data.items.some((m: Row) => m.id === model))
        setModel(data.default_model || data.items[0]?.id || '')
    } catch (e: any) {
      setError(e.message)
    } finally {
      setModelBusy(false)
    }
  }
  const chooseProvider = (p: string, defaultModel = '') => {
    setProvider(p)
    setModel(defaultModel)
    setModels([])
    setModelNote('')
  }
  useEffect(() => {
    const changed = (event: Event) => {
      if (provider !== 'api') return
      setModels([])
      setModel((event as CustomEvent).detail?.model || '')
      setModelNote('AI 配置已更新，可使用默认模型或重新读取模型列表')
    }
    window.addEventListener('acm:ai-config-changed', changed)
    return () => window.removeEventListener('acm:ai-config-changed', changed)
  }, [provider])
  const startAnalysis = (recommend = false, extra: Row = {}) =>
    run(recommend ? '/recommend' : '/analysis', { provider, model, days, platform, goal, ...extra })
  const aiPicker = (
    <div className="ai-picker">
      <select
        aria-label="AI 通道"
        value={provider}
        onChange={(e) => chooseProvider(e.target.value)}
      >
        <option value="codex">ChatGPT 官方账号</option>
        <option value="api">API Key 通道</option>
      </select>
      <select aria-label="选择模型" value={model} onChange={(e) => setModel(e.target.value)}>
        <option value="">选择模型</option>
        {models.map((m) => (
          <option key={m.id} value={m.id}>
            {m.name || m.id}
          </option>
        ))}
      </select>
      <Button
        className="icon-btn"
        onClick={loadModels}
        loading={modelBusy}
        title="读取可用模型"
        aria-label="读取可用模型"
      >
        <RefreshCw size={16} />
      </Button>
    </div>
  )
  if (!auth)
    return (
      <div className="boot">
        <Code2 size={38} />
        <p>{error || '正在打开你的训练工作台…'}</p>
      </div>
    )
  if (!unlocked && auth.multi_user) return <UserLogin status={auth} />
  if (!unlocked)
    return (
      <div className="login-page">
        <form
          onSubmit={async (e) => {
            e.preventDefault()
            try {
              await api('/auth/login', 'POST', { token })
              setToken('')
              setAuth(await api('/auth/status'))
              setError('')
            } catch (e: any) {
              setError(e.message)
            }
          }}
        >
          <div className="brand-mark">
            <Code2 size={28} />
          </div>
          <h1>进入 ACM Lab</h1>
          <p>输入部署时设置的访问令牌，打开你的私人训练工作台。</p>
          <label>
            访问令牌
            <input
              type="password"
              autoComplete="current-password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              required
            />
          </label>
          {error && <p className="form-error">{error}</p>}
          <Button className="primary">
            解锁工作台 <ArrowRight size={16} />
          </Button>
        </form>
      </div>
    )
  const current = nav.find((n) => n.id === page)!
  const handle =
    auth.user?.username ||
    (overview.platforms.find((p: Row) => p.platform === 'codeforces') || overview.platforms[0])
      ?.account ||
    '算法练习者'
  const sidebarItem = (n: (typeof nav)[number]) => (
    <button
      key={n.id}
      title={n.name}
      aria-label={n.name}
      aria-current={page === n.id ? 'page' : undefined}
      className={page === n.id ? 'active' : ''}
      onClick={() => {
        setPage(n.id)
        setMobile(false)
      }}
    >
      <n.icon size={19} />
      <span className="nav-label">{n.name}</span>
      {n.id === 'announcements' && announcements.unread.length > 0 && (
        <span className="nav-unread" aria-label="有未读公告" />
      )}
    </button>
  )
  return (
    <div className={`app-shell ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
      <aside className={`sidebar ${mobile ? 'open' : ''}`}>
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault()
            setPage('home')
          }}
        >
          <span className="brand-mark">
            <Code2 size={26} />
          </span>
          <span>
            ACM <b>Lab</b>
            <small>ALGORITHM WORKSPACE</small>
          </span>
        </a>
        <div className="workspace-label">
          个人工作空间 <span>PERSONAL</span>
        </div>
        <nav aria-label="主要功能导航">
          {sidebarItem(nav[0])}
          {navigationGroups.map((group) => (
            <div className="nav-group" role="group" aria-label={group.name} key={group.id}>
              <span className="nav-group-label">{group.name}</span>
              {availableNavigation(Boolean(auth.multi_user), auth.user?.role)
                .filter((n) => n.group === group.id)
                .map(sidebarItem)}
            </div>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-note">
            <span className="pulse-dot" />
            每一次提交，都算数
            <p>
              让数据连接练习，
              <br />
              让下一步更有方向。
            </p>
            <div className="note-bars">
              {[16, 29, 24, 40, 34, 49, 59, 51, 72, 66, 82, 94].map((h, i) => (
                <i key={i} style={{ height: h / 2 }} />
              ))}
            </div>
          </div>
          <a href="https://github.com/klbbs/ACM_PROJECT" target="_blank" rel="noreferrer">
            <Github size={16} />
            项目仓库 <ExternalLink size={12} />
          </a>
          <div className="user">
            <span className="avatar">{handle[0]?.toUpperCase()}</span>
            <div>
              {handle}
              <small>个人算法训练空间</small>
            </div>
            <span className="user-dot" />
          </div>
        </div>
      </aside>
      {mobile && (
        <button className="scrim" aria-label="关闭菜单" onClick={() => setMobile(false)} />
      )}
      <main>
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="mobile-menu icon-btn"
              onClick={() => setMobile(!mobile)}
              aria-label="打开菜单"
            >
              <Menu size={20} />
            </button>
            <button
              className="secondary sidebar-toggle"
              aria-label={sidebarCollapsed ? '展开侧栏' : '收起侧栏'}
              onClick={() => setSidebarCollapsed((v) => !v)}
            >
              <Menu size={18} /> {sidebarCollapsed ? '展开侧栏' : '收起侧栏'}
            </button>
            工作空间 <ChevronRight size={13} />
            <strong>{current.name}</strong>
          </div>
          <div className="topbar-right">
            {auth.multi_user && <OnlineUsers />}
            <form
              className="global-search"
              onSubmit={(e) => {
                e.preventDefault()
                setSearch(globalSearch)
                setPage('submissions')
              }}
            >
              <Search size={16} />
              <input
                aria-label="搜索题目或题号"
                placeholder="搜索题目或题号…"
                value={globalSearch}
                onChange={(e) => setGlobalSearch(e.target.value)}
              />
              <kbd>↵</kbd>
            </form>
            <span className="local-label">
              <span className="pulse-dot" /> {auth.multi_user ? '独立训练空间' : '私人工作台'}
            </span>
            {auth.multi_user && (
              <button
                className="text-button"
                onClick={async () => {
                  try {
                    await api('/auth/logout', 'POST', {})
                    window.location.reload()
                  } catch (e: any) {
                    setError(e.message)
                  }
                }}
              >
                退出登录
              </button>
            )}
          </div>
        </header>
        <div className="content">
          <AnnouncementBanner state={announcements} onOpen={() => setPage('announcements')} />
          {error && (
            <div className="alert" role="alert">
              <span>{error}</span>
              <button className="icon-btn" aria-label="关闭错误提示" onClick={() => setError('')}>
                <X size={16} />
              </button>
            </div>
          )}
          {notice && (
            <div className="toast" role="status">
              <CheckCircle2 size={18} />
              {notice}
            </div>
          )}
          <div
            className={
              page === 'home'
                ? 'directory-heading'
                : page === 'social'
                  ? 'page-heading social-heading'
                  : 'page-heading'
            }
          >
            <div>
              <div className="eyebrow">YOUR NEXT ACCEPTED STARTS HERE</div>
              <h1>
                {page === 'overview' ? '把练习，变成进步。' : current.name}
                <span className="heading-dot">.</span>
              </h1>
              <p>
                {page === 'overview'
                  ? '聚合你的算法足迹，发现薄弱环节，专注下一次突破。'
                  : page === 'submissions'
                    ? '每一次尝试都有价值，在记录中发现你的成长轨迹。'
                    : page === 'practice'
                      ? '从真实题库出发，为下一场比赛积累确定性。'
                      : page === 'workbench'
                        ? '在题目、代码与提示之间，把每次尝试变成下一次进步。'
                        : page === 'social'
                          ? '看看今天谁在进步，结识朋友，来一场公平的算法对战。'
                          : page === 'community'
                            ? '按需共享训练资料，管理成员与账号安全。'
                            : '连接你的平台账号与 AI，让训练工作台持续运转。'}
              </p>
            </div>
            <div className="heading-actions">
              {page === 'overview' && (
                <>
                  <Button
                    className="secondary"
                    loading={running('crawl')}
                    onClick={() => run('/crawl')}
                  >
                    <RefreshCw size={15} />
                    同步账号
                  </Button>
                  <Button
                    className="primary"
                    onClick={() =>
                      document
                        .getElementById('overview-training-advice')
                        ?.scrollIntoView({ behavior: 'smooth' })
                    }
                  >
                    <Sparkles size={16} />
                    更新训练建议
                  </Button>
                </>
              )}
              {page === 'submissions' && (
                <Button
                  className="secondary"
                  onClick={async () => {
                    try {
                      const data = await api(
                        `/submissions?platform=${platform}&search=${encodeURIComponent(search)}&limit=100&days=${days}`,
                      )
                      const url = URL.createObjectURL(
                        new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
                      )
                      const a = document.createElement('a')
                      a.href = url
                      a.download = 'acm-submissions-page.json'
                      a.click()
                      URL.revokeObjectURL(url)
                      setNotice('已导出当前筛选的前 100 条提交')
                    } catch (e: any) {
                      setError(e.message)
                    }
                  }}
                >
                  <ArrowDownToLine size={16} />
                  导出筛选记录
                </Button>
              )}
            </div>
          </div>
          {![
            'home',
            'settings',
            'community',
            'social',
            'practice',
            'workbench',
            'announcements',
          ].includes(page) && (
            <div className="filter-row">
              <div className="tabs">
                {[7, 30, 90, 0].map((d) => (
                  <button
                    key={d}
                    className={days === d ? 'selected' : ''}
                    onClick={() => setDays(d)}
                  >
                    {d ? `近 ${d} 天` : '全部历史'}
                  </button>
                ))}
              </div>
              <select
                aria-label="按平台筛选"
                value={platform}
                onChange={(e) => setPlatform(e.target.value)}
              >
                <option value="">全部平台</option>
                {Object.entries(names).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
              <span className="filter-note">
                <Clock3 size={13} />
                Asia/Shanghai ·{' '}
                {new Date().toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai' })}
              </span>
              {loading && <LoaderCircle size={16} className="spin" />}
            </div>
          )}
          {page === 'home' && (
            <MainDirectory
              multiUser={Boolean(auth.multi_user)}
              role={auth.user?.role}
              username={handle}
              unreadCount={announcements.unread.length}
              onNavigate={setPage}
              onGuide={openGuide}
            />
          )}
          {page === 'overview' && (
            <>
              <div className="stat-grid">
                {[
                  {
                    label: '已通过题目',
                    value: fmt(overview.stats.solved),
                    unit: '题',
                    icon: CheckCheck,
                    color: 'purple',
                    note: '按平台与题号去重',
                  },
                  {
                    label: '提交通过率',
                    value: overview.stats.acceptance || 0,
                    unit: '%',
                    icon: Target,
                    color: 'green',
                    note: `${fmt(overview.stats.accepted)} 次 Accepted`,
                  },
                  {
                    label: '训练提交',
                    value: fmt(overview.stats.submissions),
                    unit: '次',
                    icon: Code2,
                    color: 'blue',
                    note: `累计记录 ${fmt(overview.stats.all_submissions)} 条`,
                  },
                  {
                    label: '连续训练',
                    value: overview.stats.streak || 0,
                    unit: '天',
                    icon: Flame,
                    color: 'orange',
                    note: `当前窗口活跃 ${overview.stats.active_days || 0} 天`,
                  },
                ].map((s) => (
                  <article className="stat-card" key={s.label}>
                    <div>
                      <span>{s.label}</span>
                      <span className={`stat-icon ${s.color}`}>
                        <s.icon size={19} />
                      </span>
                    </div>
                    <h2>
                      {s.value}
                      <small>{s.unit}</small>
                    </h2>
                    <p>{s.note}</p>
                  </article>
                ))}
              </div>
              <div className="overview-columns">
                <section className="card trend-card">
                  <div className="card-heading">
                    <div>
                      <h2>训练节奏</h2>
                      <p>持续的小步前进，也会形成清晰的曲线。</p>
                    </div>
                    <div className="chart-legend">
                      <span>
                        <i className="legend-purple" />
                        提交
                      </span>
                      <span>
                        <i className="legend-green" />
                        通过
                      </span>
                    </div>
                  </div>
                  <Trend data={overview.trend} />
                </section>
                <section className="coach-teaser">
                  <span className="teaser-icon">
                    <Sparkles size={25} />
                  </span>
                  <span className="mini-label">YOUR AI COACH</span>
                  <h2>
                    下一步，
                    <br />
                    练得更有方向。
                  </h2>
                  <p>
                    {report
                      ? '你的最新能力报告已就绪。回顾数据依据，把建议落实到下一次练习。'
                      : '连接模型，从提交模式中发现薄弱环节，生成适合自己的训练路线。'}
                  </p>
                  <Button
                    onClick={() =>
                      document
                        .getElementById('overview-training-advice')
                        ?.scrollIntoView({ behavior: 'smooth' })
                    }
                  >
                    查看训练建议 <ArrowRight size={17} />
                  </Button>
                  <span className="teaser-footer">
                    <ShieldCheck size={13} />
                    仅发送统计摘要与题目信息
                  </span>
                  <div className="orbit orbit-one" />
                  <div className="orbit orbit-two" />
                </section>
              </div>
              <div className="section-title">
                <h2>
                  已连接的平台 <span>{overview.platforms.length}</span>
                </h2>
                <button className="text-button" onClick={() => setPage('settings')}>
                  管理连接 <ArrowRight size={14} />
                </button>
              </div>
              <div className="platform-grid">
                {overview.platforms.map((p: Row) => (
                  <PlatformCard key={p.platform + p.account} data={p} />
                ))}
                {!overview.platforms.length && (
                  <Empty
                    title="连接你的第一个账号"
                    detail="在连接与设置中添加平台账号，点击同步即可建立训练档案。"
                    action={<Button onClick={() => setPage('settings')}>查看配置方式</Button>}
                  />
                )}
              </div>
              <div className="bottom-columns">
                <section className="card">
                  <div className="card-heading">
                    <div>
                      <h2>值得关注的知识点</h2>
                      <p>按提交通过率排序 · 标签来自平台</p>
                    </div>
                    <button
                      className="text-button"
                      onClick={() =>
                        document
                          .getElementById('overview-training-advice')
                          ?.scrollIntoView({ behavior: 'smooth' })
                      }
                    >
                      深入分析 <ArrowRight size={14} />
                    </button>
                  </div>
                  <Topics topics={overview.topics.slice(0, 5)} />
                </section>
                <section className="card">
                  <div className="card-heading">
                    <div>
                      <h2>最近的尝试</h2>
                      <p>每一次提交都是积累</p>
                    </div>
                    <button className="text-button" onClick={() => setPage('submissions')}>
                      全部记录 <ArrowRight size={14} />
                    </button>
                  </div>
                  <div className="recent-list">
                    {overview.recent.slice(0, 5).map((r: Row, i: number) => (
                      <div key={i}>
                        <Platform platform={r.platform} small />
                        <div>
                          <button className="text-button" onClick={() => openSubmission(r)}>
                            {r.problem?.title || r.problem_id}
                          </button>
                          <span>
                            {names[r.platform]} · {r.problem_id}
                          </span>
                        </div>
                        <Badge verdict={r.verdict} />
                      </div>
                    ))}
                    {!overview.recent.length && (
                      <p className="quiet">当前窗口暂无提交，请扩大时间范围。</p>
                    )}
                  </div>
                </section>
              </div>
            </>
          )}
          {page === 'submissions' && (
            <SubmissionPage
              onOpen={openSubmission}
              days={days}
              platform={platform}
              search={search}
              setSearch={setSearch}
              revision={revision}
              onError={setError}
            />
          )}
          {page === 'overview' && (
            <>
              <section className="card">
                <h2>近期实际训练用时</h2>
                <p>
                  {(overview.actual_time_evidence || []).length} 次已通过题目的自报用时，共{' '}
                  {(overview.actual_time_evidence || []).reduce(
                    (sum: number, r: Row) => sum + r.duration_minutes,
                    0,
                  )}{' '}
                  分钟
                </p>
                <p className="help">完成每日题目后填写；用时为用户自报，不作为通过或签到证明。</p>
              </section>
              <section className="card analysis-start" id="overview-training-advice">
                <div className="analysis-heading">
                  <div className="icon-tile">
                    <Bot size={25} />
                  </div>
                  <div>
                    <h2>让数据成为训练建议</h2>
                    <p>分析最近的尝试、通过率与知识点分布，制定有依据的训练计划。</p>
                  </div>
                </div>
                <div className="ai-controls">
                  {aiPicker}
                  <Button
                    className="primary"
                    disabled={!model}
                    loading={running('analysis')}
                    onClick={() => startAnalysis()}
                  >
                    <Sparkles size={16} />
                    开始分析
                  </Button>
                </div>
                {modelNote && <p className="help">{modelNote}</p>}
                <label className="goal-label">
                  这一次，你想重点提升什么？
                  <textarea
                    value={goal}
                    onChange={(e) => setGoal(e.target.value)}
                    maxLength={1000}
                  />
                </label>
                <p className="help">
                  <ShieldCheck size={13} />
                  分析只发送统计摘要和最近 30 次提交的题目信息，Cookie 与密钥不会发送给模型。
                </p>
              </section>
              <div className="report-top">
                <h2>训练建议与记录</h2>
                <select
                  aria-label="历史分析报告"
                  value={report?.id || ''}
                  onChange={(e) =>
                    api(`/reports/${e.target.value}`)
                      .then(setReport)
                      .catch((e) => setError(e.message))
                  }
                >
                  <option value="">选择历史报告</option>
                  {reports.map((r) => (
                    <option key={r.id} value={r.id}>
                      {date(r.created_at)} · {r.kind === 'analysis' ? '训练建议' : '智能选题'}
                    </option>
                  ))}
                </select>
              </div>
              {report ? (
                <>
                  {auth.multi_user && (
                    <button
                      className="secondary"
                      onClick={async () => {
                        try {
                          await api(`/shared/report/${report.id}`, 'POST', {})
                          setNotice('报告摘要已发布到共享资料库，本站成员可见')
                        } catch (e: any) {
                          setError(e.message)
                        }
                      }}
                    >
                      向所有成员发布这份报告的摘要
                    </button>
                  )}
                  <Report data={report} />
                </>
              ) : (
                <section className="card">
                  <Empty
                    title="你的第一份训练报告，等你开启"
                    detail="先在连接与设置中接入 AI，再选择模型生成基于真实数据的训练建议。"
                    action={
                      <Button className="secondary" onClick={() => setPage('settings')}>
                        配置 AI 接入 <ArrowRight size={15} />
                      </Button>
                    }
                  />
                </section>
              )}
            </>
          )}
          {page === 'practice' && (
            <PracticePage
              aiPicker={aiPicker}
              overview={overview}
              model={model}
              provider={provider}
              busy={running('recommend')}
              catalogBusy={running('catalog')}
              jobs={jobs}
              jobsReadError={jobsReadError}
              start={(extra: Row) => startAnalysis(true, extra)}
              run={run}
              report={report}
              revision={revision}
              onError={setError}
              onNotice={setNotice}
            />
          )}
          {page === 'announcements' && <AnnouncementPage state={announcements} />}
          {page === 'workbench' && (
            <WorkbenchPage
              initialSelection={workbenchSelection}
              revision={revision}
              provider={provider}
              model={model}
              aiPicker={aiPicker}
              jobs={jobs}
              run={run}
              onError={setError}
            />
          )}
          {page === 'social' && auth.multi_user && (
            <SocialPage
              onOpenProblem={(pid: string) => {
                if (window.dispatchEvent(new Event('acm:before-navigate', { cancelable: true }))) {
                  setWorkbenchSelection({ problem_id: pid, request_id: Date.now() })
                  setPageRaw('workbench')
                }
              }}
            />
          )}
          {page === 'community' && auth.multi_user && (
            <MemberPage user={auth.user} onError={setError} onNotice={setNotice} />
          )}
          {page === 'admin' && auth.user?.role === 'admin' && (
            <AdminPage user={auth.user} onError={setError} onNotice={setNotice} />
          )}
          {page === 'settings' && (
            <SettingsPage
              guideOpen={guideOpen}
              setGuideOpen={setGuideOpen}
              guideRequest={guideRequest}
              provider={provider}
              setProvider={chooseProvider}
              onError={setError}
              onNotice={setNotice}
              revision={revision}
              jobs={jobs}
              run={run}
              model={model}
            />
          )}
          <section className="jobs-strip">
            <div className="jobs-title">
              <Activity size={16} />
              <strong>任务动态</strong>
              <span>
                {jobs.some((j) => ['running', 'queued'].includes(j.status))
                  ? '正在执行'
                  : '一切就绪'}
              </span>
            </div>
            {jobs.slice(0, 3).map((j) => (
              <div className="job-item" key={j.id}>
                <span className={`job-dot ${j.status}`} />
                <span>{jobNames[j.kind]}</span>
                <small>{statusNames[j.status]}</small>
                <time>{shortDate(j.created_at)}</time>
              </div>
            ))}
            {!jobs.length && (
              <span className="quiet">同步账号或生成分析后，进度会显示在这里。</span>
            )}
          </section>
          <footer>
            <span>
              <Code2 size={14} />
              ACM Lab <i>/</i> 为持续进步而构建
            </span>
            <span>
              数据有边界，进步有方向。 <span className="version">v0.15.1</span>
            </span>
          </footer>
        </div>
      </main>
    </div>
  )
}

function Trend({ data }: { data: Row[] }) {
  const width = 750,
    height = 198,
    pad = 24,
    max = Math.max(4, ...data.map((d) => d.submissions)),
    steps = [0, 1, 2, 3, 4]
  const points = (field: string) =>
    data
      .map(
        (d, i) =>
          `${pad + (i * (width - pad * 2)) / Math.max(1, data.length - 1)},${height - pad - (d[field] / max) * (height - pad * 2)}`,
      )
      .join(' ')
  const values = points('submissions'),
    accepted = points('accepted')
  return (
    <div className="trend">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`训练趋势，最近${data.length}天提交${data.reduce((s, r) => s + r.submissions, 0)}次`}
      >
        <defs>
          <linearGradient id="area" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#8b79f0" stopOpacity=".26" />
            <stop offset="100%" stopColor="#8b79f0" stopOpacity=".015" />
          </linearGradient>
        </defs>
        {steps.map((s) => (
          <g key={s}>
            <line
              x1={pad}
              x2={width - pad}
              y1={height - pad - (s * (height - pad * 2)) / 4}
              y2={height - pad - (s * (height - pad * 2)) / 4}
              stroke="#eceef3"
              strokeDasharray="4 5"
            />
            <text
              x="2"
              y={height - pad - (s * (height - pad * 2)) / 4 - 4}
              fontSize="10"
              fill="#a0a5b4"
            >
              {Math.round((s * max) / 4)}
            </text>
          </g>
        ))}
        {data.length > 0 && (
          <>
            <polygon
              points={`${pad},${height - pad} ${values} ${width - pad},${height - pad}`}
              fill="url(#area)"
            />
            <polyline
              points={values}
              fill="none"
              stroke="#8270e7"
              strokeWidth="2.5"
              strokeLinejoin="round"
            />
            <polyline
              points={accepted}
              fill="none"
              stroke="#55b99c"
              strokeWidth="2"
              strokeLinejoin="round"
            />
          </>
        )}
      </svg>
      <div className="chart-labels">
        {data
          .filter(
            (_, i) =>
              i === 0 ||
              i === data.length - 1 ||
              i % Math.max(1, Math.floor(data.length / 5)) === 0,
          )
          .map((d) => (
            <span key={d.date}>{d.date.slice(5).replace('-', ' / ')}</span>
          ))}
      </div>
    </div>
  )
}
function Topics({ topics }: { topics: Row[] }) {
  return (
    <div className="topics">
      {topics.map((t, i) => (
        <div key={t.tag}>
          <span className="topic-rank">0{i + 1}</span>
          <div className="topic-info">
            <div>
              <strong>{t.tag}</strong>
              <span>{t.attempts} 次提交</span>
            </div>
            <div className="progress">
              <i style={{ width: `${t.acceptance}%` }} />
            </div>
          </div>
          <b>
            {t.acceptance}
            <small>%</small>
          </b>
        </div>
      ))}
      {!topics.length && (
        <p className="quiet">当前窗口暂无带标签的提交；扩大范围或开启题目元信息采集。</p>
      )}
    </div>
  )
}
function PlatformCard({ data: p }: { data: Row }) {
  return (
    <article className="platform-card">
      <div className="platform-head">
        <Platform platform={p.platform} />
        <span className={p.complete ? 'connected' : 'sync-warning'}>
          <span /> {p.complete ? '已同步' : '待检查'}
        </span>
      </div>
      <h3>{p.name}</h3>
      <p className="account-name">{p.account}</p>
      <div className="platform-stats">
        <div>
          <span>通过题目</span>
          <b>{fmt(p.solved)}</b>
        </div>
        <div>
          <span>平台 Rating</span>
          <b>{p.rating ?? '—'}</b>
        </div>
      </div>
      <div className="sync-time">
        <RefreshCw size={11} />
        {date(p.last_sync)}
      </div>
      {p.error && <p className="form-error">{p.error}</p>}
    </article>
  )
}

function SubmissionPage({ days, platform, search, setSearch, revision, onError, onOpen }: any) {
  const [rows, setRows] = useState<Row>({ items: [], total: 0 }),
    [offset, setOffset] = useState(0),
    [verdict, setVerdict] = useState(''),
    [busy, setBusy] = useState(false)
  useEffect(() => setOffset(0), [days, platform, search, verdict])
  useEffect(() => {
    let alive = true
    setBusy(true)
    const timer = setTimeout(
      () =>
        api(
          `/submissions?days=${days}&platform=${platform}&search=${encodeURIComponent(search)}&verdict=${verdict}&offset=${offset}&limit=25`,
        )
          .then((data) => {
            if (alive) setRows(data)
          })
          .catch((e) => onError(e.message))
          .finally(() => {
            if (alive) setBusy(false)
          }),
      200,
    )
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [days, platform, search, verdict, offset, revision])
  return (
    <section className="card submission-card">
      <div className="table-toolbar">
        <div className="table-search">
          <Search size={16} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索题目名称、题号…"
            aria-label="筛选提交记录"
          />
        </div>
        <select
          value={verdict}
          onChange={(e) => setVerdict(e.target.value)}
          aria-label="筛选判题结果"
        >
          <option value="">全部结果</option>
          {['AC', 'WA', 'TLE', 'RE', 'CE', 'MLE'].map((v) => (
            <option key={v}>{v}</option>
          ))}
        </select>
        <span>
          {busy ? <LoaderCircle size={16} className="spin" /> : `共 ${fmt(rows.total)} 条记录`}
        </span>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>题目</th>
              <th>平台</th>
              <th>结果</th>
              <th>难度</th>
              <th>语言</th>
              <th>提交时间</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.items.map((r: Row) => (
              <tr key={r.platform + r.account + r.id}>
                <td>
                  <button className="text-button" onClick={() => onOpen(r)}>
                    {r.problem?.title || r.problem_id}
                  </button>
                  <small>
                    {r.problem_id} · #{r.id}
                  </small>
                </td>
                <td>
                  <span className="inline">
                    <Platform platform={r.platform} small />
                    {names[r.platform]}
                  </span>
                </td>
                <td>
                  <Badge verdict={r.verdict} />
                </td>
                <td>{r.problem?.difficulty ?? '—'}</td>
                <td className="language-cell">{r.language || '—'}</td>
                <td className="date-cell">{date(r.submitted_at)}</td>
                <td>
                  <button className="secondary" onClick={() => onOpen(r)}>
                    在工作台查看
                  </button>
                  {r.url &&
                    /^https:\/\/(codeforces\.com|atcoder\.jp|www\.luogu\.com\.cn|ac\.nowcoder\.com)\//.test(
                      r.url,
                    ) && (
                      <a
                        href={r.url}
                        className="icon-link"
                        target="_blank"
                        rel="noreferrer"
                        aria-label="查看提交"
                      >
                        <ExternalLink size={15} />
                      </a>
                    )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows.items.length && !busy && (
        <Empty title="没有符合条件的记录" detail="试试更换时间窗口、平台或搜索词。" />
      )}
      <div className="pagination">
        <span>
          第 {Math.floor(offset / 25) + 1} 页，共 {Math.max(1, Math.ceil(rows.total / 25))} 页
        </span>
        <div>
          <button
            className="icon-btn"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 25))}
            aria-label="上一页"
          >
            <ChevronLeft size={18} />
          </button>
          <button
            className="icon-btn"
            disabled={offset + 25 >= rows.total}
            onClick={() => setOffset(offset + 25)}
            aria-label="下一页"
          >
            <ChevronRight size={18} />
          </button>
        </div>
      </div>
    </section>
  )
}

function Report({ data: r }: { data: Row }) {
  const data = r.data
  return (
    <div className="report">
      <section className="card report-summary">
        <div className="report-label">
          <Sparkles size={15} />
          AI TRAINING REPORT <span>{r.model}</span>
        </div>
        <h2>{data.title}</h2>
        <p>{data.summary}</p>
        <div className="report-meta">
          <Clock3 size={13} />
          {date(r.created_at)}
          <span>
            数据窗口：{data.data_window?.days ? `近 ${data.data_window.days} 天` : '全部历史'} ·{' '}
            {data.data_window?.submissions} 次提交
          </span>
        </div>
        <div className="confidence">
          <ShieldCheck size={14} />
          {data.confidence}
        </div>
      </section>
      <div className="report-columns">
        <section className="card">
          <div className="card-heading">
            <h2>
              <span className="inline">
                <TrendingUp size={18} />
                已经建立的优势
              </span>
            </h2>
          </div>
          <ul className="strength-list">
            {data.strengths.map((s: string, i: number) => (
              <li key={i}>
                <CheckCircle2 size={17} />
                <span>{s}</span>
              </li>
            ))}
          </ul>
        </section>
        <section className="card">
          <div className="card-heading">
            <h2>
              <span className="inline">
                <Target size={18} />
                下一步的突破口
              </span>
            </h2>
          </div>
          {data.weaknesses.map((w: Row, i: number) => (
            <div className="weakness" key={i}>
              <h3>{w.topic}</h3>
              <p>{w.evidence}</p>
              <div>
                <ArrowRight size={14} />
                {w.action}
              </div>
            </div>
          ))}
        </section>
      </div>
      <section className="card plan-card">
        <div className="card-heading">
          <div>
            <h2>接下来，按计划前进</h2>
            <p>把建议拆成可以完成的小任务。</p>
          </div>
          <BookOpen size={22} />
        </div>
        <div className="plan-grid">
          {data.plan.map((d: Row, i: number) => (
            <article key={i}>
              <span>DAY {String(i + 1).padStart(2, '0')}</span>
              <h3>{d.focus}</h3>
              <p>{d.task}</p>
              <small>{d.day}</small>
            </article>
          ))}
        </div>
      </section>
    </div>
  )
}

function PracticePage({
  aiPicker,
  overview,
  model,
  provider,
  busy,
  catalogBusy,
  jobs,
  jobsReadError,
  start,
  run,
  report,
  revision,
  onError,
  onNotice,
}: any) {
  const [minimum, setMinimum] = useState(800),
    [maximum, setMaximum] = useState(1600),
    [tag, setTag] = useState(''),
    [publishedAfter, setPublishedAfter] = useState(''),
    [catalog, setCatalog] = useState<Row>({ items: [] }),
    [removed, setRemoved] = useState<string[]>([])
  const [catalogSyncJob, setCatalogSyncJob] = useState<Row | null>(null)
  const [catalogStarting, setCatalogStarting] = useState(false)
  const [catalogSyncError, setCatalogSyncError] = useState('')
  const [catalogReadError, setCatalogReadError] = useState('')
  const [catalogLoading, setCatalogLoading] = useState(true)
  const [catalogQueryRevision, setCatalogQueryRevision] = useState(0)
  const syncRequestPending = useRef(false)
  const syncPanel = useRef<HTMLDivElement>(null)
  const latestCatalogJob = catalogSyncJob
    ? jobs.find((job: Row) => job.id === catalogSyncJob.id) || catalogSyncJob
    : jobs.find((job: Row) => job.kind === 'catalog')
  const catalogSyncBusy =
    catalogStarting || catalogBusy || ['queued', 'running'].includes(latestCatalogJob?.status)
  const syncCatalog = async () => {
    syncPanel.current?.scrollIntoView({ block: 'start' })
    if (syncRequestPending.current || catalogSyncBusy) return
    syncRequestPending.current = true
    setCatalogStarting(true)
    setCatalogSyncError('')
    try {
      const created = await run('/catalog/refresh')
      if (!created?.id) {
        setCatalogSyncError(created?.error || '未能启动题库同步，请检查网络后重试。')
        return
      }
      setCatalogSyncJob({ ...created, kind: 'catalog', created_at: Math.floor(Date.now() / 1000) })
    } catch (e: any) {
      setCatalogSyncError(e.message || '未能启动题库同步，请稍后重试。')
    } finally {
      syncRequestPending.current = false
      setCatalogStarting(false)
    }
  }
  useEffect(() => {
    api('/training')
      .then((p) => {
        setMinimum(p.minimum)
        setMaximum(p.maximum)
        setPublishedAfter(p.published_after || '')
      })
      .catch((e) => onError(e.message))
  }, [])
  useEffect(() => {
    let current = true
    setCatalogReadError('')
    setCatalogLoading(true)
    if (minimum > maximum) {
      setCatalog({ items: [] })
      setCatalogReadError('最低练习难度高于最高难度，请先调整筛选范围。')
      setCatalogLoading(false)
      return
    }
    api(
      `/catalog?minimum=${minimum}&maximum=${maximum}&tags=${encodeURIComponent(tag)}${publishedAfter ? `&published_after=${publishedAfter}` : ''}`,
    )
      .then((data) => {
        if (current) setCatalog(data)
      })
      .catch((e) => {
        if (!current) return
        setCatalogReadError(e.message)
        setCatalog({ items: [] })
      })
      .finally(() => {
        if (current) setCatalogLoading(false)
      })
    return () => {
      current = false
    }
  }, [minimum, maximum, tag, publishedAfter, revision, catalogQueryRevision])
  const selected =
    report?.kind === 'recommend'
      ? report.data.selected_problems.filter((item: Row) =>
          catalog.items.some((candidate: Row) => candidate.id === item.id),
        )
      : []
  const mark = async (p: Row, status: string) => {
    try {
      await api('/feedback', 'POST', {
        platform: p.platform,
        problem_id: p.problem_id,
        status,
        note: '',
      })
      setRemoved((r) => [...r, p.id])
      onNotice(
        status === 'done' ? '已记录完成，后续选题会排除这道题' : '已跳过，后续推荐会排除这道题',
      )
    } catch (e: any) {
      onError(e.message)
    }
  }
  return (
    <>
      <section className="card practice-setup">
        <div className="practice-heading">
          <div className="icon-tile">
            <Target size={24} />
          </div>
          <div>
            <h2>给下一次突破，选一道好题</h2>
            <p>已提交的题目自动排除，AI 只会从下面的真实候选中选择。</p>
          </div>
          <Button className="secondary" loading={catalogSyncBusy} onClick={syncCatalog}>
            <RefreshCw size={15} />
            同步平台题库
          </Button>
        </div>
        {!catalogLoading && !catalogReadError && (
          <div className="catalog-eligibility" aria-label="平台选题状态">
            {(catalog.eligibility?.platforms || []).map((item: Row) => (
              <article
                className={`catalog-platform ${item.login_verified ? 'ready' : ''}`}
                key={item.platform}
              >
                <strong>
                  {names[item.platform]} · {item.login_verified ? '已验证登录' : '待检测登录'}
                </strong>
                <p>
                  {item.login_verified
                    ? `${item.date_eligible_count} 道符合日期要求`
                    : '暂不参与推荐'}
                </p>
                <p>
                  缓存 {item.cached_count ?? 0} 题 · 日期未知 {item.unknown_publication_count ?? 0}{' '}
                  题
                </p>
                <p>{item.notice}</p>
                {catalog.eligibility?.refresh?.find((row: Row) => row.platform === item.platform)
                  ?.status === 'failed' && (
                  <p>同步失败，已保留旧缓存；可重试或查看平台访问限制。</p>
                )}
                {item.updated_at && <p>最近同步：{date(item.updated_at)}</p>}
              </article>
            ))}
          </div>
        )}
        {!catalogLoading &&
          !catalogReadError &&
          !catalog.eligibility?.platforms?.some((item: Row) => item.login_verified) && (
            <p className="social-note">
              先到连接与设置绑定平台，再同步平台题库；点击 AI 选题时也会重新检测已保存的过期会话。
              <button
                className="text-button"
                onClick={() => window.dispatchEvent(new Event('acm:open-settings'))}
              >
                管理连接
              </button>
            </p>
          )}
        {!catalogLoading && !catalogReadError && catalog.eligibility?.requires_publication_date && (
          <p className="social-note">
            {catalog.eligibility.notice}
            <button
              className="text-button"
              onClick={() => window.dispatchEvent(new Event('acm:open-settings'))}
            >
              设置真实发布日期
            </button>
          </p>
        )}
        <div className="practice-filters">
          <label>
            最低 CF 难度
            <select value={minimum} onChange={(e) => setMinimum(Number(e.target.value))}>
              {Array.from({ length: 28 }, (_, i) => 800 + i * 100).map((n) => (
                <option key={n}>{n}</option>
              ))}
            </select>
          </label>
          <label>
            最高 CF 难度
            <select value={maximum} onChange={(e) => setMaximum(Number(e.target.value))}>
              {Array.from({ length: 28 }, (_, i) => 800 + i * 100).map((n) => (
                <option key={n}>{n}</option>
              ))}
            </select>
          </label>
          <label>
            强化知识点
            <select value={tag} onChange={(e) => setTag(e.target.value)}>
              <option value="">不限知识点</option>
              {overview.topics
                .filter((t: Row) => !/^\d+$/.test(t.tag))
                .map((t: Row) => (
                  <option key={t.tag}>{t.tag}</option>
                ))}
            </select>
          </label>
          <label>
            最早题目发布日期
            <input type="date" aria-label="选题发布日期下限" value={publishedAfter} readOnly />
            <button
              className="text-button"
              onClick={() => window.dispatchEvent(new Event('acm:open-settings'))}
            >
              在训练偏好中修改
            </button>
          </label>
        </div>
        <div className="ai-controls">
          {aiPicker}
          <Button
            className="primary"
            loading={busy}
            disabled={!model || minimum > maximum || catalog.eligibility?.requires_publication_date}
            onClick={() =>
              start({
                minimum,
                maximum,
                tags: tag ? [tag] : [],
                published_after: publishedAfter || null,
              })
            }
          >
            <Sparkles size={16} />
            AI 为我选题
          </Button>
        </div>
        <p className="help">
          {catalog.source || '题库'} · 本次最多 60 道候选 ·{' '}
          随机混合已验证登录平台，发布时间早于下限或无法确认的题不会进入日期受限推荐；不同平台不换算
          CF 难度。
          {catalog.updated_at ? `更新于 ${date(catalog.updated_at)}` : '尚未同步题库'}
        </p>
      </section>
      <DailyTasksPage
        revision={revision}
        provider={provider}
        model={model}
        aiPicker={aiPicker}
        onError={onError}
        onNotice={onNotice}
      />
      {selected.length > 0 && (
        <>
          <div className="section-title">
            <h2>
              为你挑选的练习 <span>{selected.length}</span>
            </h2>
            <span className="help">来自最近一次 AI 选题报告</span>
          </div>
          <div className="problem-grid">
            {selected
              .filter((p: Row) => !removed.includes(p.id))
              .map((p: Row, i: number) => (
                <article className="card problem-card" key={p.id}>
                  <div className="problem-number">
                    <span>CHALLENGE / 0{i + 1}</span>
                    <b>{p.difficulty}</b>
                  </div>
                  <h3>{p.title}</h3>
                  <p className="problem-id">
                    {names[p.platform] || p.platform} · {p.problem_id}
                  </p>
                  <div className="tags">
                    {p.tags.map((t: string) => (
                      <span key={t}>{t}</span>
                    ))}
                  </div>
                  <div className="problem-reason">
                    <Sparkles size={15} />
                    <p>{p.reason}</p>
                  </div>
                  <div className="problem-actions">
                    <a className="button primary" href={p.url} target="_blank" rel="noreferrer">
                      开始练习 <ExternalLink size={14} />
                    </a>
                    <Button
                      className="icon-btn"
                      title="标记完成"
                      aria-label="标记完成"
                      onClick={() => mark(p, 'done')}
                    >
                      <Check size={18} />
                    </Button>
                    <Button className="text-button" onClick={() => mark(p, 'skip')}>
                      跳过
                    </Button>
                  </div>
                </article>
              ))}
          </div>
        </>
      )}
      <div className="section-title">
        <h2>
          真实候选题目{' '}
          <span>{catalogLoading ? '…' : catalogReadError ? '—' : catalog.items.length}</span>
        </h2>
        <span className="help">排除平台历史与已完成/跳过记录</span>
      </div>
      <div ref={syncPanel} className="catalog-sync-anchor">
        {jobsReadError &&
          (catalogStarting || ['queued', 'running'].includes(latestCatalogJob?.status)) && (
            <p className="alert" role="alert">
              暂时无法读取题库同步进度：{jobsReadError}。系统会自动重试，请勿重复提交同步任务。
            </p>
          )}
        <CatalogSyncPanel
          job={latestCatalogJob}
          eligibility={catalog.eligibility}
          candidateCount={catalog.items.length}
          busy={catalogStarting}
          requestError={catalogSyncError}
          candidateError={catalogReadError}
          loadingCandidates={catalogLoading}
          onSettings={() => window.dispatchEvent(new Event('acm:open-settings'))}
        />
      </div>
      <section className="card candidate-list">
        {catalogLoading ? (
          <Empty title="正在读取候选题目…" detail="正在检查平台登录与当前筛选条件，请稍候。" />
        ) : catalogReadError ? (
          <Empty
            title="暂时无法读取候选题目"
            detail={catalogReadError}
            action={
              <Button className="secondary" onClick={() => setCatalogQueryRevision((n) => n + 1)}>
                重新读取候选
              </Button>
            }
          />
        ) : catalog.items.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>题目</th>
                  <th>原平台难度 / 分数</th>
                  <th>知识点</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {catalog.items.map((p: Row) => (
                  <tr key={p.id}>
                    <td>
                      <strong>{p.title}</strong>
                      <small>{p.problem_id}</small>
                    </td>
                    <td>
                      <span className="difficulty">{p.difficulty ?? '未提供'}</span>
                    </td>
                    <td>
                      <div className="tags">
                        {p.tags.slice(0, 4).map((t: string) => (
                          <span key={t}>{t}</span>
                        ))}
                      </div>
                    </td>
                    <td>
                      <a
                        href={p.url}
                        className="icon-link"
                        target="_blank"
                        rel="noreferrer"
                        aria-label="打开题目"
                      >
                        <ExternalLink size={15} />
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="还没有合适的候选题目"
            detail="请查看上方同步状态与候选条件说明；同步完成后仍需验证平台登录，才能将该平台加入推荐。"
            action={
              <Button className="secondary" loading={catalogSyncBusy} onClick={syncCatalog}>
                <RefreshCw size={15} />
                同步平台题库
              </Button>
            }
          />
        )}
      </section>
    </>
  )
}

function SettingsPage({
  guideOpen,
  setGuideOpen,
  guideRequest,
  provider,
  setProvider,
  onError,
  onNotice,
  revision,
  jobs,
  run,
  model,
}: any) {
  const guideRef = useRef<HTMLDetailsElement>(null)
  useEffect(() => {
    if (guideRequest && guideOpen) guideRef.current?.scrollIntoView({ block: 'start' })
  }, [guideRequest])
  const [config, setConfig] = useState<Row>({
      base_url: 'https://api.openai.com/v1',
      protocol: 'responses',
      model: '',
      has_key: false,
    }),
    [key, setKey] = useState(''),
    [saving, setSaving] = useState(false)
  const [presets, setPresets] = useState<Row[]>([])
  const [apiModels, setApiModels] = useState<Row[]>([])
  const [apiModelNote, setApiModelNote] = useState('')
  const [apiModelSource, setApiModelSource] = useState('')
  const [readingModels, setReadingModels] = useState(false)
  useEffect(() => {
    api('/ai/presets')
      .then((d) => setPresets(d.items))
      .catch((e: Error) => onError(e.message))
  }, [])
  const [account, setAccount] = useState<Row | null>(null),
    [login, setLogin] = useState<Row | null>(null),
    [connecting, setConnecting] = useState(false)
  const [training, setTraining] = useState<Row>({
    goal_rating: 2000,
    minimum: 1000,
    maximum: 1600,
    min_contest: 0,
    preferred_tags: [],
    avoid_tags: [],
    prefer_recent: true,
  })
  const [accounts, setAccounts] = useState<Row[]>([])
  const [accountRevision, setAccountRevision] = useState(0)
  useEffect(() => {
    api('/training')
      .then(setTraining)
      .catch((e) => onError(e.message))
    api('/ai/settings')
      .then(setConfig)
      .catch((e) => onError(e.message))
    api('/accounts')
      .then((d) => setAccounts(d.items))
      .catch((e) => onError(e.message))
  }, [revision])
  const [editingAccount, setEditingAccount] = useState<Row>({
    platform: 'codeforces',
    handle: '',
    cookie: '',
    enrich: false,
  })
  const [accountSaving, setAccountSaving] = useState(false)
  const saveAccounts = async (items: Row[]) => {
    setAccountSaving(true)
    try {
      const result = await api('/accounts', 'PUT', { items })
      setAccounts(result.items)
      setAccountRevision((value) => value + 1)
      setEditingAccount({ platform: 'codeforces', handle: '', cookie: '', enrich: false })
      onNotice('平台账号已保存，可立即同步；Cookie 不会返回浏览器')
    } catch (e: any) {
      onError(e.message)
    } finally {
      setAccountSaving(false)
    }
  }
  const accountPayload = (a: Row) => ({
    platform: a.platform,
    handle: a.handle,
    enrich: !!a.enrich,
  })
  const saveAccount = () => {
    const payload = accounts
      .filter(
        (a) =>
          !(a.platform === editingAccount.platform && a.handle === editingAccount.originalHandle),
      )
      .map(accountPayload)
    const item: Row = {
      platform: editingAccount.platform,
      handle: editingAccount.handle,
      enrich: !!editingAccount.enrich,
    }
    if (editingAccount.clearCookie) item.cookie = ''
    else if (editingAccount.cookie) item.cookie = editingAccount.cookie
    saveAccounts([...payload, item])
  }
  const readAccount = async () => {
    setConnecting(true)
    try {
      setAccount(await api('/ai/account'))
    } catch (e: any) {
      onError(e.message)
    } finally {
      setConnecting(false)
    }
  }
  const doLogin = async (mode: string) => {
    setConnecting(true)
    try {
      const d = await api(`/ai/login?mode=${mode}`, 'POST', {})
      setLogin(d)
      if (d.authUrl) window.open(d.authUrl, '_blank', 'noopener,noreferrer')
    } catch (e: any) {
      onError(e.message)
    } finally {
      setConnecting(false)
    }
  }
  useEffect(() => {
    if (!login) return
    const timer = setInterval(
      () =>
        api('/ai/account')
          .then((a) => {
            setAccount(a)
            if (a.connected && a.login?.success) {
              setLogin(null)
              onNotice('官方账号已连接，可以读取模型目录')
            } else if (a.login?.completed && !a.login?.success) {
              setLogin(null)
              onError('登录未完成或设备码已过期，请重新申请')
            }
          })
          .catch(() => {}),
      5000,
    )
    return () => clearInterval(timer)
  }, [login])
  const save = async (clearKey = false) => {
    setSaving(true)
    try {
      const payload: Row = {
        base_url: config.base_url,
        protocol: config.protocol,
        model: config.model || '',
        vendor: config.vendor || 'custom',
        output_mode: config.output_mode || 'auto',
        thinking: config.thinking || 'auto',
        reasoning_effort: config.reasoning_effort || 'auto',
        max_output_tokens: config.max_output_tokens || 8192,
        timeout: config.timeout || 180,
        temperature: config.temperature ?? null,
      }
      if (clearKey) payload.api_key = ''
      else if (key) payload.api_key = key
      const saved = await api('/ai/settings', 'PUT', payload)
      setConfig(saved)
      setKey('')
      window.dispatchEvent(
        new CustomEvent('acm:ai-config-changed', { detail: { model: saved.model } }),
      )
      onNotice(clearKey ? '当前 API 地址的密钥已清除' : 'AI API 配置已保存，密钥仅保存在服务端')
      return true
    } catch (e: any) {
      onError(e.message)
      return false
    } finally {
      setSaving(false)
    }
  }
  return (
    <>
      <div className="settings-shortcuts">
        <button
          className="secondary"
          onClick={() =>
            document.getElementById('platform-accounts')?.scrollIntoView({ block: 'start' })
          }
        >
          配置平台账号
        </button>
        <button
          className="secondary"
          onClick={() => {
            setGuideOpen(true)
            guideRef.current?.scrollIntoView({ block: 'start' })
          }}
        >
          安装油猴脚本与绑定教程
        </button>
      </div>
      <details
        className="settings-guide"
        ref={guideRef}
        open={guideOpen}
        onToggle={(e) => setGuideOpen(e.currentTarget.open)}
      >
        <summary>平台绑定教程与油猴脚本</summary>
        <ExtensionGuide />
        <PlatformGuide />
      </details>
      <div className="settings-grid">
        <section className="card settings-card">
          <div className="card-heading">
            <div>
              <h2>
                <span className="inline">
                  <KeyRound size={19} />
                  AI API 接入
                </span>
              </h2>
              <p>
                选择提供方并填写 Key，点击“保存并读取模型列表”，再从下拉栏选择模型并保存。
                连接测试会实际调用模型并按提供方规则计费。
              </p>
            </div>
            <span className={`badge ${config.has_key ? 'success' : 'neutral'}`}>
              {config.has_key ? '已配置' : '未配置'}
            </span>
          </div>
          <label>
            模型提供方
            <select
              aria-label="模型提供方"
              value={config.vendor || 'custom'}
              onChange={(e) => {
                const preset = presets.find((p) => p.id === e.target.value)
                if (!preset) return
                setKey('')
                setApiModels([])
                setApiModelNote('')
                setApiModelSource('')
                setConfig({
                  ...config,
                  vendor: preset.id,
                  base_url: preset.base_url,
                  protocol: preset.protocol,
                  model: '',
                  has_key: false,
                  thinking: 'auto',
                  reasoning_effort: 'auto',
                  output_mode: 'auto',
                  temperature: null,
                })
              }}
            >
              {presets.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                  {p.allowed ? '' : '（需管理员批准域名）'}
                </option>
              ))}
            </select>
          </label>
          <p className="help">
            密钥按 API 地址分别保存；切换提供方不会把旧密钥发送给新地址。区域和 Key 必须匹配。Gemini
            使用官方兼容接口；智谱按量 API 与 Coding Plan 地址不同。
          </p>
          {presets.find((p) => p.id === config.vendor)?.docs && (
            <a
              href={presets.find((p) => p.id === config.vendor)?.docs}
              target="_blank"
              rel="noreferrer"
            >
              提供方官方接入文档 ↗
            </a>
          )}
          <label>
            API 地址
            <input
              value={config.base_url}
              onChange={(e) => {
                setConfig({ ...config, base_url: e.target.value, has_key: false })
                setKey('')
                setApiModels([])
                setApiModelNote('')
                setApiModelSource('')
              }}
              placeholder="https://api.openai.com/v1"
              type="url"
            />
          </label>
          <label>
            接入协议
            <select
              value={config.protocol}
              onChange={(e) =>
                setConfig({
                  ...config,
                  protocol: e.target.value,
                  thinking: 'auto',
                  reasoning_effort: 'auto',
                  output_mode: 'auto',
                })
              }
            >
              <option value="responses">Responses API · OpenAI 官方</option>
              <option value="chat">Chat Completions · 兼容提供方</option>
              <option value="anthropic">Claude 原生 Messages</option>
            </select>
          </label>
          <label>
            API Key
            <input
              type="password"
              autoComplete="new-password"
              value={key}
              onChange={(e) => {
                setKey(e.target.value)
                setApiModels([])
                setApiModelNote('')
                setApiModelSource('')
              }}
              placeholder={config.has_key ? '已保存；留空保持原密钥' : '输入提供方的 API Key'}
            />
          </label>
          <p className="help">
            <ShieldCheck size={13} />
            密钥不会返回浏览器，不会进入 Git 或 AI 提示词。
          </p>
          <label>
            默认模型
            <select
              aria-label="默认模型 ID"
              value={config.model || ''}
              onChange={(e) => setConfig({ ...config, model: e.target.value })}
            >
              <option value="">先保存 Key 并读取模型</option>
              {apiModels.map((m) => (
                <option key={m.id} value={m.id}>
                  {apiModelSource === 'manual' ? '未验证 · ' : ''}
                  {m.name || m.id}
                </option>
              ))}
              {config.model && !apiModels.some((m) => m.id === config.model) && (
                <option value={config.model}>{config.model}（当前填写；未验证）</option>
              )}
            </select>
          </label>
          <details>
            <summary>目录未包含可用模型？手动填写模型 ID</summary>
            <label>
              手动模型 ID
              <input
                aria-label="手动模型 ID"
                value={config.model || ''}
                onChange={(e) => setConfig({ ...config, model: e.target.value })}
                maxLength={150}
                placeholder="填写服务商提供的准确模型 ID；保存后可测试"
              />
            </label>
          </details>
          <Button
            className="text-button"
            loading={readingModels}
            disabled={saving}
            onClick={async () => {
              setReadingModels(true)
              try {
                if (!(await save())) return
                setApiModels([])
                setApiModelSource('')
                setApiModelNote('正在向当前提供方读取模型目录…')
                const data = await api('/ai/models?provider=api')
                setApiModels(data.items)
                setApiModelNote(data.note)
                setApiModelSource(data.source || 'remote')
                if (!data.items.some((m: Row) => m.id === config.model)) {
                  const nextModel = data.default_model || data.items[0]?.id || ''
                  setConfig((current) => ({ ...current, model: nextModel }))
                }
              } catch (e: any) {
                setApiModels([])
                setApiModelSource('')
                setApiModelNote('读取失败；请检查提供方、Key、API 地址和网络后重试。')
                onError(e.message)
              } finally {
                setReadingModels(false)
              }
            }}
          >
            保存并读取模型列表
          </Button>
          {apiModelNote && (
            <p className="help" role="status">
              {apiModels.length} 个模型；{apiModelNote}
            </p>
          )}
          <label>
            结构化输出
            <select
              aria-label="结构化输出"
              value={config.output_mode || 'auto'}
              onChange={(e) => setConfig({ ...config, output_mode: e.target.value })}
            >
              <option value="auto">按协议默认</option>
              <option value="schema">JSON Schema（须模型支持）</option>
              <option value="json" disabled={config.protocol === 'anthropic'}>
                JSON 对象
              </option>
              <option value="prompt">仅提示约束（仍严格校验结果）</option>
            </select>
          </label>
          {config.protocol === 'chat' &&
            ['deepseek', 'qwen', 'qwen_global'].includes(config.vendor) && (
              <label>
                思考模式
                <select
                  aria-label="思考模式"
                  value={config.thinking || 'auto'}
                  onChange={(e) => setConfig({ ...config, thinking: e.target.value })}
                >
                  <option value="auto">使用模型默认</option>
                  <option value="enabled">启用（须模型支持）</option>
                  <option value="disabled">关闭（须模型支持）</option>
                </select>
              </label>
            )}
          {config.protocol !== 'anthropic' &&
            ['openai', 'deepseek', 'moonshot', 'gemini'].includes(config.vendor) && (
              <label>
                推理强度
                <select
                  value={config.reasoning_effort || 'auto'}
                  onChange={(e) => setConfig({ ...config, reasoning_effort: e.target.value })}
                >
                  {(config.vendor === 'deepseek' || config.vendor === 'moonshot'
                    ? ['auto', 'low', 'high', 'max']
                    : ['auto', 'low', 'medium', 'high']
                  ).map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </label>
            )}
          <details>
            <summary>输出与超时参数</summary>
            <label>
              最大输出 token
              <input
                type="number"
                min={256}
                max={32768}
                value={config.max_output_tokens || 8192}
                onChange={(e) =>
                  setConfig({ ...config, max_output_tokens: Number(e.target.value) })
                }
              />
            </label>
            <label>
              超时（秒）
              <input
                type="number"
                min={30}
                max={240}
                value={config.timeout || 180}
                onChange={(e) => setConfig({ ...config, timeout: Number(e.target.value) })}
              />
            </label>
            <label>
              Temperature（空白为不发送）
              <input
                type="number"
                min={0}
                max={2}
                step={0.1}
                value={config.temperature ?? ''}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    temperature: e.target.value === '' ? null : Number(e.target.value),
                  })
                }
              />
            </label>
            <p>
              思考模型可能不支持 temperature，默认不发送。推理 token
              会占输出额度，截断时需提高上限。
            </p>
          </details>
          <div className="settings-actions">
            <Button className="primary" loading={saving} onClick={() => save()}>
              保存 API 配置 <Check size={16} />
            </Button>
            <button
              disabled={jobs.some(
                (j: Row) => j.kind === 'ai_test' && ['queued', 'running'].includes(j.status),
              )}
              onClick={() => run('/ai/test')}
            >
              测试已保存配置（会计费）
            </button>
            <button
              className="text-button"
              onClick={() => {
                setProvider('api', config.model || '')
                onNotice('已切换到 API Key 通道，可使用默认模型或读取模型列表')
              }}
            >
              使用此通道 <ArrowRight size={14} />
            </button>
          </div>
          <button
            className="text-button"
            disabled={saving || !config.has_key}
            onClick={() => save(true)}
          >
            清除当前地址密钥
          </button>
          {jobs
            .filter((j: Row) => j.kind === 'ai_test')
            .slice(0, 1)
            .map((j: Row) => (
              <p key={j.id} role="status">
                最近连接测试：{statusNames[j.status]} {j.result?.model}{' '}
                {j.result?.latency_ms ? `${j.result.latency_ms} ms` : ''}{' '}
                {j.error || j.result?.note}
              </p>
            ))}
        </section>
        <section className="card settings-card chatgpt-settings">
          <div className="card-heading">
            <div>
              <h2>
                <span className="inline">
                  <Bot size={20} />
                  ChatGPT 官方账号
                </span>
              </h2>
              <p>通过官方 Codex App Server 读取登录状态和模型目录。</p>
            </div>
            <span className="mini-label">OFFICIAL LOGIN</span>
          </div>
          <div className="official-account">
            <span className="official-icon">
              <Bot size={30} />
            </span>
            <h3>{account?.connected ? '官方账号已连接' : '连接你的 ChatGPT 账号'}</h3>
            <p>
              {account?.connected
                ? `${account.email || 'ChatGPT 官方账号 会话'} · ${account.plan || account.type || ''}`
                : '已登录 Codex 时可以直接读取，无需复制 ChatGPT Cookie。'}
            </p>
          </div>
          {account?.message && <p className="help">{account.message}</p>}
          <div className="settings-actions">
            <Button className="secondary" loading={connecting} onClick={readAccount}>
              <RefreshCw size={15} />
              读取现有登录
            </Button>
            <Button
              className="dark"
              loading={connecting}
              disabled={config.codex_enabled === false}
              onClick={() => doLogin('device')}
            >
              <LogIn size={16} />
              申请官方登录设备码
            </Button>
          </div>
          {!config.codex_device_only && (
            <button
              className="text-button device-link"
              disabled={config.codex_enabled === false}
              onClick={() => doLogin('browser')}
            >
              本机浏览器回调登录 <ArrowRight size={13} />
            </button>
          )}
          {account?.connected && (
            <button
              className="text-button"
              onClick={async () => {
                try {
                  await api('/ai/logout', 'POST', {})
                  setAccount(null)
                  setLogin(null)
                  onNotice('官方账号已退出')
                } catch (e: any) {
                  onError(e.message)
                }
              }}
            >
              退出官方账号
            </button>
          )}
          {login?.userCode && (
            <div className="device-code">
              <p>在你的电脑或手机浏览器打开官方页面，输入设备码</p>
              <strong>{login.userCode}</strong>
              <a href={login.verificationUrl} target="_blank" rel="noreferrer">
                打开官方验证页 <ExternalLink size={13} />
              </a>
              <button
                className="text-button"
                onClick={async () => {
                  try {
                    await api('/ai/login/cancel', 'POST', {})
                    setLogin(null)
                  } catch (e: any) {
                    onError(e.message)
                  }
                }}
              >
                取消本次登录
              </button>
              <p>等待官方授权完成，页面自动刷新状态。过期或失败后可重新申请。</p>
            </div>
          )}
          <p className="help">
            服务器无需桌面。请先在 ChatGPT 安全设置中启用设备码登录；管理员需启用服务器官方通道。
            登录绑定当前用户，授权保存在服务器的独立目录。模型以实际调用权限为准。
          </p>
        </section>
      </div>
      <PlatformConnections
        revision={`${revision}:${accountRevision}`}
        run={run}
        onError={onError}
      />
      <section className="card settings-card" id="platform-accounts">
        <div className="card-heading">
          <div>
            <h2>
              <span className="inline">
                <Database size={19} />
                算法平台连接
              </span>
            </h2>
            <p>填写用户名、数字 UID 或个人主页链接即可；支持无桌面服务器，保存后立即生效。</p>
          </div>
          <Button
            className="secondary"
            loading={jobs.some(
              (j: Row) => j.kind === 'crawl' && ['running', 'queued'].includes(j.status),
            )}
            onClick={() => run('/crawl')}
          >
            <RefreshCw size={15} />
            同步全部账号
          </Button>
        </div>
        <div className="account-settings">
          {accounts.map((a: Row) => (
            <div key={a.platform + a.handle}>
              <Platform platform={a.platform} />
              <div>
                <strong>{names[a.platform]}</strong>
                <small>{a.handle}</small>
              </div>
              <span className="badge neutral">{a.has_cookie ? '会话已配置' : '公开接口'}</span>
              <button
                className="text-button"
                disabled={accountSaving}
                onClick={() =>
                  setEditingAccount({
                    ...a,
                    originalHandle: a.handle,
                    cookie: '',
                    clearCookie: false,
                  })
                }
              >
                编辑
              </button>
              <button
                className="text-button"
                disabled={accountSaving}
                onClick={() =>
                  saveAccounts(accounts.filter((item) => item !== a).map(accountPayload))
                }
              >
                移除配置
              </button>
            </div>
          ))}
        </div>
        <button
          className="text-button"
          onClick={() => window.dispatchEvent(new Event('acm:open-guide'))}
        >
          查看平台绑定与 Cookie 获取教程
        </button>
        {!accounts.length && (
          <p className="help">尚未配置账号，请在下方添加。移除配置不会删除已采集的提交历史。</p>
        )}
        <div className="account-editor">
          <h3>{editingAccount.originalHandle ? '编辑平台账号' : '添加平台账号'}</h3>
          <div className="practice-filters">
            <label>
              平台
              <select
                value={editingAccount.platform}
                disabled={!!editingAccount.originalHandle}
                onChange={(e) => setEditingAccount({ ...editingAccount, platform: e.target.value })}
              >
                {Object.entries(names).map(([id, name]) => (
                  <option key={id} value={id}>
                    {String(name)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              用户名 / UID / 主页链接
              <input
                value={editingAccount.handle}
                maxLength={500}
                onChange={(e) => setEditingAccount({ ...editingAccount, handle: e.target.value })}
                placeholder={
                  ['luogu', 'nowcoder'].includes(editingAccount.platform)
                    ? '数字 UID 或个人主页链接'
                    : '用户名或个人主页链接'
                }
              />
            </label>
          </div>
          <label>
            Cookie（可选）
            <input
              type="password"
              autoComplete="new-password"
              value={editingAccount.cookie}
              maxLength={16384}
              onChange={(e) => setEditingAccount({ ...editingAccount, cookie: e.target.value })}
              placeholder="name=value; name2=value2；留空保留已保存的会话"
            />
          </label>
          <p className="help">
            公开数据可以先不填 Cookie。需要登录时，先用本页提供的油猴脚本获取并复制 Cookie，或按 F12
            教程手动获取，粘贴后保存并点击检测。无需服务器安装浏览器；脚本只在你主动点击后尝试读取当前平台，不支持时请使用
            F12。
          </p>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={!!editingAccount.enrich}
              onChange={(e) => setEditingAccount({ ...editingAccount, enrich: e.target.checked })}
            />
            补充题目信息（AtCoder，可能增加请求）
          </label>
          {editingAccount.originalHandle && (
            <label className="checkbox">
              <input
                type="checkbox"
                checked={!!editingAccount.clearCookie}
                onChange={(e) =>
                  setEditingAccount({ ...editingAccount, clearCookie: e.target.checked })
                }
              />
              清除已保存 Cookie，使用公开接口
            </label>
          )}
          <div className="settings-actions">
            <Button
              className="primary"
              loading={accountSaving}
              disabled={!editingAccount.handle.trim()}
              onClick={saveAccount}
            >
              保存平台账号
            </Button>
            {editingAccount.originalHandle && (
              <button
                className="text-button"
                onClick={() =>
                  setEditingAccount({
                    platform: 'codeforces',
                    handle: '',
                    cookie: '',
                    enrich: false,
                  })
                }
              >
                取消编辑
              </button>
            )}
          </div>
        </div>
      </section>
      <section className="card settings-card">
        <div className="card-heading">
          <div>
            <h2>
              <span className="inline">
                <Target size={19} />
                训练偏好
              </span>
            </h2>
            <p>让 AI 了解你的目标，优先选择更有价值的新题。</p>
          </div>
        </div>
        <div className="practice-filters">
          <label>
            目标 CF Rating
            <input
              type="number"
              min="800"
              max="4000"
              value={training.goal_rating}
              onChange={(e) => setTraining({ ...training, goal_rating: Number(e.target.value) })}
            />
          </label>
          <label>
            最低练习难度
            <input
              type="number"
              min="800"
              max="3500"
              step="100"
              value={training.minimum}
              onChange={(e) => setTraining({ ...training, minimum: Number(e.target.value) })}
            />
          </label>
          <label>
            最高练习难度
            <input
              type="number"
              min="800"
              max="3500"
              step="100"
              value={training.maximum}
              onChange={(e) => setTraining({ ...training, maximum: Number(e.target.value) })}
            />
          </label>
          <label>
            最早题目发布日期
            <input
              type="date"
              aria-label="最早题目发布日期"
              value={training.published_after || ''}
              onChange={(e) =>
                setTraining({
                  ...training,
                  published_after: e.target.value || null,
                  min_contest: 0,
                })
              }
            />
          </label>
        </div>
        <button
          className="text-button"
          type="button"
          disabled={!training.published_after && !training.min_contest}
          onClick={() => {
            setTraining({ ...training, published_after: null, min_contest: 0 })
            onNotice('已清除日期与旧编号限制，请保存训练偏好生效')
          }}
        >
          不限制发布日期（清除旧编号限制）
        </button>
        <div className="preference-fields">
          <label>
            优先知识点（英文标签，逗号分隔）
            <input
              value={training.preferred_tags.join(',')}
              onChange={(e) =>
                setTraining({ ...training, preferred_tags: e.target.value.split(',') })
              }
            />
          </label>
          <label>
            避免仅包含这些标签的题目
            <input
              value={training.avoid_tags.join(',')}
              onChange={(e) => setTraining({ ...training, avoid_tags: e.target.value.split(',') })}
            />
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={training.prefer_recent}
              onChange={(e) => setTraining({ ...training, prefer_recent: e.target.checked })}
            />
            同难度优先较新比赛
          </label>
        </div>
        <Button
          className="primary"
          onClick={async () => {
            try {
              setTraining(
                await api('/training', 'PUT', {
                  ...training,
                  preferred_tags: training.preferred_tags
                    .map((t: string) => t.trim())
                    .filter(Boolean),
                  avoid_tags: training.avoid_tags.map((t: string) => t.trim()).filter(Boolean),
                }),
              )
              onNotice('训练偏好已保存')
            } catch (e: any) {
              onError(e.message)
            }
          }}
        >
          保存训练偏好
        </Button>
        <p className="help">
          使用题目真实发布日期控制新旧；缺少可靠日期的题不会进入有日期下限的推荐。题库会排除已有提交，以及本地导入的已练习记录。
        </p>
      </section>
    </>
  )
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)

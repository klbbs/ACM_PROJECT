import React, { useEffect, useId, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  BookOpen,
  Check,
  ChevronDown,
  ExternalLink,
  RefreshCw,
  Send,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { api, names } from './api'
import { SelectionReportButton } from './selection-report'
import { TrainingCandidates } from './training-candidates'
import './specialist-training.css'

type Topic = {
  id: string
  title: string
  description: string
  source_url: string
  path?: string | string[]
  keywords?: string[]
  depth?: number
}
type Mode = { id: string; title: string; description: string }
const defaultModes: Mode[] = [
  { id: 'learn', title: '新学', description: '从模板题起步，逐步建立专题基础。' },
  { id: 'consolidate', title: '巩固', description: '练习常见用法，巩固已有知识与实现。' },
  { id: 'variation', title: '变式', description: '通过不同题型与建模，迁移专题方法。' },
  { id: 'strengthen', title: '加强', description: '挑战更复杂的综合应用与边界。' },
]
const topicPath = (topic: Topic) =>
  Array.isArray(topic.path) ? topic.path.join(' / ') : topic.path || ''
type Group = { id: string; title: string; topics: Topic[] }
type Item = {
  position: number
  problem_id: string
  title: string
  platform: string
  url: string
  difficulty?: number | string | null
  difficulty_label?: string
  stage: string
  reason: string
  completed: boolean
  ai_report_id?: string
  cf_rating_estimate?: {
    kind: 'official' | 'estimate' | 'unknown'
    minimum: number | null
    maximum: number | null
    notice: string
    source_urls: string[]
  }
}
type Plan = {
  id: string
  topic_id: string
  topic_title: string
  title: string
  created_at: number
  mode?: string
  mode_title?: string
  rating_request?: { minimum: number; maximum: number }
  items: Item[]
}
type GenerationJob = {
  id: string
  kind: 'specialist_generate'
  status: string
  result?: {
    stage?:
      | 'queued'
      | 'validating'
      | 'catalog'
      | 'method_check'
      | 'selecting'
      | 'reading'
      | 'saving'
      | 'completed'
    requested_count?: number
    actual_count?: number
    plan?: Plan
  }
  error?: string
}
const generationStages: Record<string, string> = {
  queued: '等待开始',
  validating: '检查平台连接与训练条件',
  catalog: '准备本专题候选题',
  method_check: '核查细类方法适用性',
  selecting: 'AI 正在选择题目',
  reading: 'AI 正在读取题面并整理题目报告',
  saving: '保存题单',
  completed: '题单已保存',
}
const pendingGeneration = (job: GenerationJob | null) =>
  !!job && ['queued', 'running', 'pending'].includes(job.status)
type Source = {
  attempt_id?: string | null
  submission_id: string
  account: string
  platform: string
  language: string
  verdict: string
  source_available: boolean
  can_fetch_source?: boolean
  disabled_reason: string
}
type Review = {
  completed: boolean
  sources: Source[]
  assessment: null | {
    result: {
      summary: string
      dimensions: {
        category: string
        finding: string
        evidence: string
        suggestion: string
        confidence: string
      }[]
      strengths: string[]
      next_steps: string[]
      overall_confidence: string
    }
    submission?: Source
  }
  messages: { role: string; content: string; created_at?: number }[]
}
type Props = {
  account?: string
  provider: string
  model: string
  aiPicker: React.ReactNode
  revision: number
  onOpenProblem: (id: string) => void
  onError: (message: string) => void
  onNotice: (message: string) => void
  aiReady?: boolean
}
const stages: Record<string, string> = {
  template: '模板入门',
  foundation: '基础巩固',
  application: '综合应用',
  challenge: '进阶挑战',
  consolidate: '巩固练习',
  variation: '变式练习',
  strengthen: '加强练习',
}
const dimensionNames: Record<string, string> = {
  code_quality: '代码规范',
  boundaries: '边界条件',
  correctness: '正确性',
  complexity: '复杂度与资源',
  strategy: '解题策略',
}
const confidenceNames: Record<string, string> = {
  high: '高',
  medium: '中',
  low: '低',
  uncertain: '无法确定',
}
const message = (error: unknown) => (error instanceof Error ? error.message : '请求未完成，请重试')
const sourceKey = (source: Source) =>
  JSON.stringify([source.attempt_id, source.platform, source.account, source.submission_id])
function SafeLink({ href, children }: { href?: string; children: React.ReactNode }) {
  return href && /^https?:\/\//i.test(href) ? (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ) : (
    <span>{children}</span>
  )
}
function RichText({ children }: { children: string }) {
  return (
    <div className="specialist-markdown">
      <Markdown remarkPlugins={[remarkGfm]}>{children}</Markdown>
    </div>
  )
}

function readGenerationRequest(account: string): string | null {
  try {
    const key = sessionStorage.getItem(`acm:specialist-generation:${account}`)
    return key && /^[a-f0-9]{32}$/.test(key) ? key : null
  } catch {
    return null
  }
}
function saveGenerationRequest(account: string, key: string | null) {
  try {
    if (key) sessionStorage.setItem(`acm:specialist-generation:${account}`, key)
    else sessionStorage.removeItem(`acm:specialist-generation:${account}`)
  } catch {
    /* Current-page recovery remains available if browser storage is disabled. */
  }
}

export function SpecialistTrainingPage({
  account = 'local',
  provider,
  model,
  aiPicker,
  revision,
  onOpenProblem,
  onError,
  onNotice,
  aiReady = true,
}: Props) {
  const [groups, setGroups] = useState<Group[]>([])
  const [groupId, setGroupId] = useState('')
  const [topicId, setTopicId] = useState('')
  const [count, setCount] = useState('4')
  const [ratingMinimum, setRatingMinimum] = useState('')
  const [ratingMaximum, setRatingMaximum] = useState('')
  const [ratingDefaults, setRatingDefaults] = useState({ minimum: 800, maximum: 3500 })
  const [learnerRatings, setLearnerRatings] = useState<number[]>([])
  const [mode, setMode] = useState('learn')
  const [modes, setModes] = useState<Mode[]>(defaultModes)
  const [maxCount, setMaxCount] = useState(50)
  const [search, setSearch] = useState('')
  const [plans, setPlans] = useState<Plan[]>([])
  const [selected, setSelected] = useState('')
  const [expanded, setExpanded] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [generationJob, setGenerationJob] = useState<GenerationJob | null>(null)
  const [checkingGeneration, setCheckingGeneration] = useState(true)
  const [generationQueryError, setGenerationQueryError] = useState('')
  const [generationError, setGenerationError] = useState('')
  const [unconfirmedRequest, setUnconfirmedRequest] = useState(
    () => !!readGenerationRequest(account),
  )
  const [error, setError] = useState('')
  const [failed, setFailed] = useState<'load' | 'generate' | ''>('')
  const disclosureId = useId()
  const stateId = useId()
  const generation = useRef(0)
  const writing = useRef(false)
  const alive = useRef(true)
  const queryingGeneration = useRef(false)
  const jobQuerySequence = useRef(0)
  const completedJob = useRef('')
  const completedPlan = useRef<Plan | null>(null)
  const busyRef = useRef(false)
  const queryGeneration = useRef<() => Promise<void>>(async () => {})
  const generationRequestKey = useRef<string | null>(readGenerationRequest(account))
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      generation.current += 1
      jobQuerySequence.current += 1
      queryingGeneration.current = false
    }
  }, [])
  const load = async () => {
    if (writing.current) return
    const token = ++generation.current
    setLoading(true)
    try {
      const [catalog, saved] = await Promise.all([
        api<{
          groups: Group[]
          modes?: Mode[]
          max_count?: number
          default_cf_minimum?: number
          default_cf_maximum?: number
          learner_evidence?: { cf_ratings: { rating: number }[] }
        }>('/training/topics'),
        api<{ plans: Plan[] }>('/training/plans'),
      ])
      if (!alive.current || token !== generation.current) return
      setGroups(catalog.groups)
      const supported = catalog.modes?.filter((entry) =>
        defaultModes.some((known) => known.id === entry.id),
      )
      setModes(supported?.length === 4 ? supported : defaultModes)
      setMaxCount(Math.max(1, Math.min(50, catalog.max_count || 50)))
      setRatingDefaults({
        minimum: catalog.default_cf_minimum || 800,
        maximum: catalog.default_cf_maximum || 3500,
      })
      setLearnerRatings((catalog.learner_evidence?.cf_ratings || []).map((entry) => entry.rating))
      setGroupId((old) =>
        catalog.groups.some((group) => group.id === old) ? old : catalog.groups[0]?.id || '',
      )
      const restored =
        completedPlan.current && !saved.plans.some((plan) => plan.id === completedPlan.current?.id)
          ? [completedPlan.current, ...saved.plans]
          : saved.plans
      setPlans(restored)
      setSelected((old) => (restored.some((plan) => plan.id === old) ? old : restored[0]?.id || ''))
      setError('')
      setFailed('')
    } catch (caught) {
      if (alive.current && token === generation.current) {
        setError(message(caught))
        setFailed('load')
        onError(message(caught))
      }
    } finally {
      if (alive.current && token === generation.current) setLoading(false)
    }
  }
  useEffect(() => {
    void load()
  }, [revision])
  queryGeneration.current = async () => {
    if (queryingGeneration.current || writing.current || !alive.current) return
    queryingGeneration.current = true
    const token = ++jobQuerySequence.current
    setCheckingGeneration(true)
    try {
      const requestKey = generationRequestKey.current
      const response = await api<{ job: GenerationJob | null }>(
        `/training/generation${requestKey ? `?request_key=${requestKey}` : ''}`,
      )
      if (!alive.current || token !== jobQuerySequence.current) return
      const job = response.job
      setGenerationJob(job)
      setGenerationQueryError('')
      setUnconfirmedRequest(!!requestKey && !job)
      if (requestKey && job && !pendingGeneration(job)) {
        generationRequestKey.current = null
        saveGenerationRequest(account, null)
      }
      busyRef.current = pendingGeneration(job)
      setBusy(busyRef.current)
      if (job?.status === 'completed' && job.result?.plan) {
        const generated = job.result.plan
        completedPlan.current = generated
        setPlans((old) => [generated, ...old.filter((entry) => entry.id !== generated.id)])
        if (completedJob.current !== job.id) {
          completedJob.current = job.id
          setSelected(generated.id)
          setExpanded(null)
          setGenerationError('')
          onNotice('专项题单已保存，可以继续练习。')
        }
      } else if (job?.status === 'completed') {
        setGenerationError(
          '后台任务已结束，但没有返回可读取的题单。已有题单保留，请重新查询状态或刷新训练列表，不会自动重新调用 AI。',
        )
      } else if (job && ['failed', 'partial', 'interrupted', 'cancelled'].includes(job.status)) {
        setGenerationError(
          job.error ||
            '本次生成未完成，已有题单保留。不会自动重新请求 AI；请检查模型设置后再主动生成。',
        )
      } else if (pendingGeneration(job)) {
        setGenerationError('')
      } else if (!job && requestKey) {
        setGenerationError(
          '本次生成请求尚未被后台确认。已有题单保留，请稍后重新查询生成状态；状态确认前不会再次生成或调用 AI。',
        )
      } else if (!job) {
        setGenerationError((old) =>
          old
            ? '未发现可恢复的专项生成任务。已有题单保留；如需重新生成，请确认模型设置后主动点击生成按钮。'
            : '',
        )
      }
    } catch (caught) {
      if (alive.current && token === jobQuerySequence.current) {
        setGenerationQueryError(
          `生成状态查询失败：${message(caught)}。任务可能仍在后台处理，请重新查询，不要重复生成。`,
        )
      }
    } finally {
      if (token === jobQuerySequence.current) {
        queryingGeneration.current = false
        if (alive.current) setCheckingGeneration(false)
      }
    }
  }
  useEffect(() => {
    void queryGeneration.current()
  }, [revision])
  useEffect(() => {
    if (!busy || generationQueryError) return
    const timer = window.setInterval(() => void queryGeneration.current(), 3000)
    return () => window.clearInterval(timer)
  }, [busy, generationQueryError])
  const group = groups.find((candidate) => candidate.id === groupId)
  const topics = group?.topics || []
  useEffect(() => {
    setTopicId((old) => (topics.some((topic) => topic.id === old) ? old : topics[0]?.id || ''))
  }, [groupId, groups])
  const allTopics = groups.flatMap((entry) =>
    entry.topics.map((candidate) => ({ ...candidate, group: entry })),
  )
  const query = search.trim().toLocaleLowerCase()
  const matches = query
    ? allTopics.filter((candidate) =>
        [
          candidate.title,
          candidate.id,
          topicPath(candidate),
          candidate.group.title,
          candidate.description,
          ...(candidate.keywords || []),
        ]
          .join(' ')
          .toLocaleLowerCase()
          .includes(query),
      )
    : topics
  const topic = allTopics.find((candidate) => candidate.id === topicId)
  const visibleTopics =
    topic && !matches.some((entry) => entry.id === topic.id) ? [topic, ...matches] : matches
  const amount = Number(count)
  const countValid =
    count.trim() !== '' && Number.isInteger(amount) && amount >= 1 && amount <= maxCount
  const minimum = Number(ratingMinimum),
    maximum = Number(ratingMaximum)
  const ratingSpecified = ratingMinimum.trim() !== '' || ratingMaximum.trim() !== ''
  const ratingValid =
    !ratingSpecified ||
    (ratingMinimum.trim() !== '' &&
      ratingMaximum.trim() !== '' &&
      Number.isInteger(minimum) &&
      Number.isInteger(maximum) &&
      minimum >= 800 &&
      maximum <= 3500 &&
      minimum <= maximum)
  const modeTitle = (value = 'learn') =>
    modes.find((entry) => entry.id === value)?.title ||
    defaultModes.find((entry) => entry.id === value)?.title ||
    value
  const plan = plans.find((candidate) => candidate.id === selected)
  const generate = async () => {
    if (
      writing.current ||
      queryingGeneration.current ||
      busyRef.current ||
      checkingGeneration ||
      generationQueryError ||
      unconfirmedRequest ||
      !topicId ||
      !model ||
      !aiReady ||
      !countValid ||
      !ratingValid
    )
      return
    writing.current = true
    busyRef.current = true
    const token = ++jobQuerySequence.current
    generation.current += 1
    setBusy(true)
    setError('')
    setGenerationError('')
    setGenerationQueryError('')
    setFailed('')
    setLoading(false)
    let queryAfterWrite = true
    try {
      const requestKey = Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) =>
        value.toString(16).padStart(2, '0'),
      ).join('')
      generationRequestKey.current = requestKey
      saveGenerationRequest(account, requestKey)
      setUnconfirmedRequest(true)
      const job = await api<GenerationJob>('/training/plans/generate', 'POST', {
        topic_id: topicId,
        count: amount,
        mode,
        provider,
        model,
        request_key: requestKey,
        ...(ratingSpecified ? { cf_minimum: minimum, cf_maximum: maximum } : {}),
      })
      if (!alive.current || token !== jobQuerySequence.current) return
      if (!job?.id) throw new Error('后台未返回可识别的生成任务')
      setGenerationJob(job)
      setUnconfirmedRequest(false)
      onNotice('专项生成已进入后台，离开页面后任务仍会继续；重新进入可恢复进度。')
    } catch (caught) {
      if (alive.current && token === jobQuerySequence.current) {
        const rejected =
          caught instanceof Error &&
          'status' in caught &&
          typeof caught.status === 'number' &&
          [400, 401, 403, 409, 422, 429].includes(caught.status)
        if (rejected) {
          generationRequestKey.current = null
          saveGenerationRequest(account, null)
          setUnconfirmedRequest(false)
          busyRef.current = false
          setBusy(false)
          queryAfterWrite = false
          setGenerationError(
            `生成请求被拒绝：${message(caught)}。已有题单保留；请处理提示的问题后再主动生成，不会自动重试。`,
          )
        } else {
          setGenerationError(
            `生成请求未得到确认：${message(caught)}。正在查询后台状态，不会自动重新提交生成请求。`,
          )
        }
        onError(message(caught))
      }
    } finally {
      writing.current = false
      if (queryAfterWrite && alive.current && token === jobQuerySequence.current)
        void queryGeneration.current()
    }
  }
  const modelReason = !model || !aiReady ? '请先在连接与设置中保存默认 AI 模型' : ''
  const countReason = countValid ? '' : `题目数量需为 1–${maxCount} 的整数`
  const ratingReason = ratingValid
    ? ''
    : '请同时填写 CF rating 上下限，范围需为 800–3500 的整数，且下限不能大于上限'
  const topicTitle = topic?.title || '专项训练'
  const generateReason =
    unconfirmedRequest && !busy
      ? '本次生成请求尚未确认，请点击“重新查询生成状态”；不会重复发送生成请求。'
      : generationQueryError
        ? '生成状态尚未确认，请先点击“重新查询生成状态”；查询不会再次请求 AI。'
        : checkingGeneration && !busy
          ? '正在恢复后台生成状态…'
          : loading
            ? '正在读取训练专题与已保存题单，读取完成后才能生成。'
            : busy
              ? '本次题单正在后台生成；可以离开页面，稍后回来继续查看进度。'
              : failed === 'load' && !groups.length
                ? '训练专题读取失败，请点击“刷新训练”后重试。'
                : !topicId
                  ? '请先选择训练专题，生成按钮才可用。'
                  : modelReason || countReason || ratingReason
  const generateLabel = busy
    ? 'AI 正在安排训练路线…'
    : loading
      ? '正在读取训练专题…'
      : countValid
        ? `AI 生成「${topicTitle}」${amount} 道题单`
        : `AI 生成「${topicTitle}」题单`
  return (
    <section className="card specialist-training">
      <div className="card-heading">
        <div>
          <h2>
            <BookOpen size={22} /> 专项训练
          </h2>
          <p>选择专题、学习阶段和题量，安排适合自己的训练路线。</p>
        </div>
        <button className="button secondary" disabled={loading || busy} onClick={() => void load()}>
          <RefreshCw size={16} />
          刷新训练
        </button>
      </div>
      <label className="specialist-search">
        搜索训练专题
        <input
          type="search"
          aria-label="搜索训练专题"
          value={search}
          disabled={busy}
          placeholder="搜索洛谷分类、算法名称或 Codeforces tag"
          onChange={(event) => setSearch(event.target.value)}
        />
      </label>
      {query && (
        <p className="help" role="status">
          找到 {matches.length} 个匹配专题；当前选择会保留。
        </p>
      )}
      <div className="specialist-setup">
        <label>
          专题分类
          <select
            aria-label="专题分类"
            value={groupId}
            disabled={!groups.length || busy}
            onChange={(event) => {
              setGroupId(event.target.value)
              setSearch('')
            }}
          >
            {groups.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          训练专题
          <select
            aria-label="训练专题"
            value={topicId}
            disabled={!visibleTopics.length || busy}
            onChange={(event) => {
              setTopicId(event.target.value)
              const chosen = allTopics.find((candidate) => candidate.id === event.target.value)
              if (chosen) setGroupId(chosen.group.id)
            }}
          >
            {visibleTopics.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {query && topicPath(candidate) ? `${topicPath(candidate)} · ` : ''}
                {'　'.repeat(Math.min(6, candidate.depth || 0))}
                {candidate.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          题目数量
          <input
            type="number"
            aria-label="专项题量"
            min={1}
            max={maxCount}
            step={1}
            value={count}
            disabled={busy}
            onChange={(event) => setCount(event.target.value)}
          />
          <span className="help">1–{maxCount} 道，自由设置</span>
        </label>
        <label>
          本次 CF rating 下限（选填）
          <input
            type="number"
            aria-label="专项 CF rating 下限"
            min={800}
            max={3500}
            step={100}
            value={ratingMinimum}
            placeholder={String(ratingDefaults.minimum)}
            disabled={busy}
            onChange={(event) => setRatingMinimum(event.target.value)}
          />
        </label>
        <label>
          本次 CF rating 上限（选填）
          <input
            type="number"
            aria-label="专项 CF rating 上限"
            min={800}
            max={3500}
            step={100}
            value={ratingMaximum}
            placeholder={String(ratingDefaults.maximum)}
            disabled={busy}
            onChange={(event) => setRatingMaximum(event.target.value)}
          />
        </label>
      </div>
      <p className="help">
        {learnerRatings.length
          ? `本人最近同步的 CF rating：${learnerRatings.join(' / ')}。`
          : '尚无本人已绑定 CF 账号的有效 rating 快照，请同步账号数据；AI 不会猜测个人 rating。'}
        上下限均留空时沿用全局 CF {ratingDefaults.minimum}–{ratingDefaults.maximum}
        ，其他平台仍按原生难度。 填入两项后，本次范围独立生效，不修改全局训练偏好或每日任务。
      </p>
      <fieldset className="specialist-modes" disabled={busy}>
        <legend>训练阶段</legend>
        <p className="help">按学习目标选择阶段；这不是平台的题目评分。仅“新学”要求首题为模板题。</p>
        <div className="specialist-mode-options">
          {modes.map((entry) => (
            <label
              key={entry.id}
              className={mode === entry.id ? 'specialist-mode selected' : 'specialist-mode'}
            >
              <input
                type="radio"
                name="specialist-mode"
                value={entry.id}
                checked={mode === entry.id}
                onChange={() => setMode(entry.id)}
              />
              <strong>{entry.title}</strong>
              <span>{entry.description}</span>
            </label>
          ))}
        </div>
      </fieldset>
      {topic && (
        <p className="help">
          {topic.description}{' '}
          <SafeLink href={topic.source_url}>
            查看专题资料 <ExternalLink size={12} />
          </SafeLink>
        </p>
      )}
      <p className="help specialist-scope">
        <ShieldCheck size={14} />
        选题范围：从你已验证登录的所有平台中一起筛选。请先在“连接与设置”保存账号并检测登录状态，生成时会检查平台连接。
      </p>
      <p className="help">
        CF 使用官方 rating；洛谷按官方等级作启发式宽区间，AtCoder 参考公开难度统计或 ABC 分值估计。
        非 CF
        题按估计区间与目标范围重叠筛选，可能存在偏差，并非官方等价；牛客等缺少可信映射的题会排除并说明。
      </p>
      <TrainingCandidates
        topicId={topicId}
        topicTitle={topic?.title}
        minimum={ratingSpecified && ratingValid ? minimum : undefined}
        maximum={ratingSpecified && ratingValid ? maximum : undefined}
        valid={ratingValid}
        mode={mode}
        disabled={loading || busy}
        revision={revision}
        aiReady={!!model && aiReady}
      />
      <div className="ai-controls specialist-generate">
        {aiPicker}
        <button
          className="button primary specialist-generate-action"
          aria-busy={busy}
          aria-describedby={`${stateId} ${disclosureId}`}
          disabled={!!generateReason}
          onClick={() => void generate()}
        >
          <Sparkles size={16} />
          {generateLabel}
        </button>
      </div>
      <p className="help specialist-generate-state" id={stateId} role="status">
        {generateReason ||
          `按「${modeTitle(mode)}」生成${countValid ? ` ${amount} 道` : ''}题；生成时检查平台连接并筛选符合条件的候选题。`}
      </p>
      <p className="help specialist-ai-note" id={disclosureId}>
        点击生成会将训练偏好、专题及公开候选题信息发送给所选
        AI，可能产生模型费用。选题失败时保留已有题单。 细类专题候选不足时，也会使用默认 AI
        额外读取最多 6
        道候选官方题面，核查所选方法是否可行，可能增加费用；核查结论不是官方标签或唯一解，不会自动批准为模板题。
        慢速或思考模型会在后台继续处理，可离开页面后再回来查看。失败不会自动重试收费；如耗时过长，可在“连接与设置”调整模型的思考和输出
        token 配置，再主动开始新任务。
      </p>
      {(busy || checkingGeneration || generationQueryError || generationError) && (
        <div className="specialist-generation-progress" aria-live="polite" aria-busy={busy}>
          <strong>
            {busy
              ? generationStages[generationJob?.result?.stage || 'queued'] || '正在后台生成专项题单'
              : checkingGeneration
                ? '正在查询后台生成状态…'
                : '专项生成状态'}
          </strong>
          {busy && (
            <p className="help">
              {generationJob?.result?.requested_count != null
                ? `目标题量 ${generationJob.result.requested_count} 道。`
                : ''}
              {generationJob?.result?.stage === 'reading' &&
              generationJob.result.actual_count != null
                ? `已完成阅读 ${generationJob.result.actual_count}${generationJob.result.requested_count != null ? ` / ${generationJob.result.requested_count}` : ''} 道。`
                : ''}
              进度每 3 秒自动查询，不会重复请求 AI；旧题单仍可使用。
            </p>
          )}
          {generationError && (
            <p className="help specialist-error" role="alert">
              {generationError}
            </p>
          )}
          {generationQueryError && (
            <p className="help specialist-error" role="alert">
              {generationQueryError}
            </p>
          )}
          {(generationQueryError || generationError) && (
            <button
              className="button secondary"
              disabled={checkingGeneration}
              onClick={() => void queryGeneration.current()}
            >
              <RefreshCw size={16} /> 重新查询生成状态
            </button>
          )}
        </div>
      )}
      {error && (
        <div role="alert" className="help specialist-error">
          {error}。
          {failed === 'generate'
            ? '本次没有保存新题单，已有题单保留；可先点击“查看本专题候选”核对缓存、平台与过滤原因，再决定补抓或调整选项。失败请求不会自动重试。'
            : '已显示的题单保留；可点击“刷新训练”重试读取，读取不会消耗 AI 额度。'}
        </div>
      )}
      {loading && <p role="status">正在恢复已保存的专项训练…</p>}
      {plans.length > 0 && (
        <label className="specialist-history">
          已保存训练
          <select
            aria-label="已保存训练"
            value={selected}
            onChange={(event) => {
              setSelected(event.target.value)
              setExpanded(null)
            }}
          >
            {plans.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.title || candidate.topic_title} ·{' '}
                {new Date(candidate.created_at * 1000).toLocaleDateString('zh-CN', {
                  timeZone: 'Asia/Shanghai',
                })}
              </option>
            ))}
          </select>
        </label>
      )}
      {plan ? (
        <div className="specialist-route">
          <div className="specialist-route-title">
            <h3>{plan.title || plan.topic_title}</h3>
            <span className="badge neutral">{plan.mode_title || modeTitle(plan.mode)}</span>
            <span className="badge neutral">
              {plan.items.filter((item) => item.completed).length}/{plan.items.length} 已完成
            </span>
            {plan.rating_request && (
              <span className="badge neutral">
                目标 CF {plan.rating_request.minimum}–{plan.rating_request.maximum}
              </span>
            )}
          </div>
          <ol>
            {plan.items.map((item) => (
              <li
                key={item.position}
                className={item.completed ? 'specialist-step completed' : 'specialist-step'}
              >
                <div className="specialist-step-index">
                  {item.completed ? <Check size={18} /> : item.position + 1}
                </div>
                <div className="specialist-step-body">
                  <div className="specialist-item-heading">
                    <h4>{item.title}</h4>
                    <span className={`badge ${item.stage === 'template' ? 'success' : 'neutral'}`}>
                      {stages[item.stage] || item.stage}
                    </span>
                  </div>
                  <p className="help">
                    {names[item.platform] || item.platform} ·{' '}
                    {item.difficulty != null
                      ? `难度 ${item.difficulty}`
                      : item.difficulty_label || '平台未标注难度'}{' '}
                    {item.difficulty != null && item.difficulty_label
                      ? `（${item.difficulty_label}）`
                      : ''}{' '}
                    · {item.problem_id}
                  </p>
                  {item.cf_rating_estimate && (
                    <details className="help">
                      <summary>
                        {item.cf_rating_estimate.kind === 'official'
                          ? 'CF 官方 rating'
                          : item.cf_rating_estimate.kind === 'estimate'
                            ? `估计 CF ${item.cf_rating_estimate.minimum}–${item.cf_rating_estimate.maximum}（低可信）`
                            : 'CF 对照难度未知'}{' '}
                        · 查看依据
                      </summary>
                      <p>{item.cf_rating_estimate.notice}</p>
                      {item.cf_rating_estimate.source_urls.map((url) => (
                        <p key={url}>
                          <SafeLink href={url}>查看难度来源</SafeLink>
                        </p>
                      ))}
                    </details>
                  )}
                  <p>{item.reason}</p>
                  <div className="specialist-actions">
                    <button className="button" onClick={() => onOpenProblem(item.problem_id)}>
                      前往工作台
                    </button>
                    <SafeLink href={item.url}>
                      官方题目 <ExternalLink size={13} />
                    </SafeLink>
                    <button
                      className="button secondary"
                      aria-expanded={expanded === item.position}
                      onClick={() =>
                        setExpanded((old) => (old === item.position ? null : item.position))
                      }
                    >
                      <ChevronDown size={15} />
                      提交评估与讨论
                    </button>
                  </div>
                  <SelectionReportButton
                    key={`${plan.id}:${item.problem_id}:${item.ai_report_id || 'legacy'}`}
                    reportId={item.ai_report_id}
                    problemId={item.problem_id}
                    title={item.title}
                  />
                  {expanded === item.position && (
                    <ItemReview
                      key={`${plan.id}:${item.position}`}
                      planId={plan.id}
                      revision={revision}
                      item={item}
                      provider={provider}
                      model={model}
                      aiReady={aiReady}
                      onError={onError}
                    />
                  )}
                </div>
              </li>
            ))}
          </ol>
        </div>
      ) : (
        !loading && (
          <div className="specialist-empty">
            <BookOpen size={30} />
            <h3>你的第一条专项训练路线</h3>
            <p>选择专题并生成题单；题目和评估会保存在账号中，重新进入后可继续练习。</p>
          </div>
        )
      )}
    </section>
  )
}

function ItemReview({
  planId,
  revision,
  item,
  provider,
  model,
  aiReady,
  onError,
}: {
  planId: string
  revision: number
  item: Item
  provider: string
  model: string
  aiReady: boolean
  onError: Props['onError']
}) {
  const path = `/training/plans/${encodeURIComponent(planId)}/items/${item.position}`
  const [data, setData] = useState<Review | null>(null)
  const [selected, setSelected] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<'assessment' | 'chat' | null>(null)
  const [error, setError] = useState('')
  const [draft, setDraft] = useState('')
  const alive = useRef(true)
  const pending = useRef(false)
  const generation = useRef(0)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      generation.current += 1
    }
  }, [])
  const apply = (value: Review) => {
    setData(value)
    setSelected((old) =>
      value.sources.some((source) => sourceKey(source) === old)
        ? old
        : sourceKey(
            value.sources.find((source) => source.source_available || source.can_fetch_source) ||
              value.sources[0] ||
              ({ submission_id: '', platform: '', account: '' } as Source),
          ),
    )
  }
  const load = async () => {
    if (pending.current) return
    const token = ++generation.current
    setLoading(true)
    try {
      const value = await api<Review>(`${path}/assessment`)
      if (alive.current && token === generation.current) {
        apply(value)
        setError('')
      }
    } catch (caught) {
      if (alive.current && token === generation.current) setError(message(caught))
    } finally {
      if (alive.current && token === generation.current) setLoading(false)
    }
  }
  useEffect(() => {
    void load()
  }, [revision])
  const source = data?.sources.find((candidate) => sourceKey(candidate) === selected)
  const modelReason = !model || !aiReady ? '请先在连接与设置中保存默认 AI 模型' : ''
  const blocked = !data?.completed
    ? '本题尚未完成 AC，请先通过官方判题并同步提交记录'
    : !source
      ? '没有可匹配的官方 AC 提交，请先同步本题提交'
      : (!source.source_available && !source.can_fetch_source
          ? source.disabled_reason || '平台无法读取该次官方源码，不能使用编辑器或其他提交替代'
          : '') ||
        (!['AC', 'Accepted', 'OK'].includes(source.verdict) ? '仅支持评估本题已通过的官方提交' : '')
  const assess = async () => {
    if (pending.current || loading || blocked || modelReason || !source) return
    pending.current = true
    setBusy('assessment')
    setError('')
    const token = ++generation.current
    try {
      const value = await api<Review>(`${path}/assessment`, 'POST', {
        ...(source.attempt_id
          ? { attempt_id: source.attempt_id }
          : { submission_id: source.submission_id, handle: source.account }),
        provider,
        model,
      })
      if (alive.current && token === generation.current) apply(value)
    } catch (caught) {
      if (alive.current && token === generation.current) {
        setError(message(caught))
        onError(message(caught))
      }
    } finally {
      pending.current = false
      if (alive.current) setBusy(null)
    }
  }
  const chat = async (prompt = draft) => {
    if (pending.current || !data?.assessment || !prompt.trim() || modelReason) return
    pending.current = true
    setBusy('chat')
    setError('')
    const token = ++generation.current
    try {
      const value = await api<Review>(`${path}/chat`, 'POST', {
        message: prompt.trim(),
        provider,
        model,
      })
      if (alive.current && token === generation.current) {
        apply(value)
        setDraft('')
      }
    } catch (caught) {
      if (alive.current && token === generation.current) {
        setError(message(caught))
        onError(message(caught))
      }
    } finally {
      pending.current = false
      if (alive.current) setBusy(null)
    }
  }
  const result = data?.assessment?.result
  return (
    <div className="specialist-review">
      <div className="specialist-item-heading">
        <h4>提交评估与 AI 讨论</h4>
        <button
          className="button secondary"
          disabled={loading || !!busy}
          onClick={() => void load()}
        >
          <RefreshCw size={14} />
          刷新本题提交
        </button>
      </div>
      <p className="help">
        仅分析你选中的本题官方提交，使用匹配的源码、题面、语言与判题信息；不读取编辑器草稿。AI
        评估是建议，不改变成绩或签到。
      </p>
      {loading && <p role="status">正在读取本题提交与已有评估…</p>}
      {error && (
        <div className="specialist-error" role="alert">
          {error}
          {!data && (
            <button className="button secondary" onClick={() => void load()} disabled={loading}>
              重试读取提交
            </button>
          )}
        </div>
      )}
      {data && (
        <>
          <label>
            选择本题提交
            <select
              aria-label="选择本题提交"
              value={selected}
              disabled={!!busy}
              onChange={(event) => setSelected(event.target.value)}
            >
              <option value="">请选择提交</option>
              {data.sources.map((candidate) => (
                <option key={sourceKey(candidate)} value={sourceKey(candidate)}>
                  {names[candidate.platform] || candidate.platform} · {candidate.account} · #
                  {candidate.submission_id} · {candidate.verdict} · {candidate.language}
                  {candidate.disabled_reason ? ` · ${candidate.disabled_reason}` : ''}
                </option>
              ))}
            </select>
          </label>
          {source && !source.source_available && source.can_fetch_source && (
            <p className="help">
              点击评估后尝试获取这次提交的官方源码；无法获取时会说明原因，不会使用其他代码。
            </p>
          )}
          <p className="help specialist-ai-note">
            点击评估或发送追问会将本题题面、匹配提交源码和判题信息及讨论内容发送至当前
            AI，可能产生费用。
          </p>
          <button
            className="button"
            disabled={!!busy || loading || !!blocked || !!modelReason}
            title={blocked || modelReason}
            onClick={() => void assess()}
          >
            <Sparkles size={15} />
            {busy === 'assessment'
              ? 'AI 正在评估本次提交…'
              : result
                ? '重新评估所选提交'
                : '递交 AI 评估'}
          </button>
          {(blocked || modelReason) && <p className="help">{blocked || modelReason}</p>}
          {result && (
            <div className="specialist-assessment">
              <p className="help">
                评估对应提交 #{data.assessment?.submission?.submission_id || '已记录'} · 可信度：
                {confidenceNames[result.overall_confidence] ||
                  result.overall_confidence ||
                  '未标注'}
                ；AI 判断不等同官方解释。
              </p>
              <RichText>{result.summary}</RichText>
              <div className="specialist-dimensions">
                {result.dimensions?.map((dimension, index) => (
                  <article key={index}>
                    <h5>
                      {dimensionNames[dimension.category] || dimension.category}{' '}
                      <small>{confidenceNames[dimension.confidence] || dimension.confidence}</small>
                    </h5>
                    <RichText>{dimension.finding}</RichText>
                    <p>
                      <strong>代码与题面证据：</strong>
                      {dimension.evidence}
                    </p>
                    <p>
                      <strong>建议检查：</strong>
                      {dimension.suggestion}
                    </p>
                  </article>
                ))}
              </div>
              {result.strengths?.length > 0 && (
                <div>
                  <h5>做得好的地方</h5>
                  <ul>
                    {result.strengths.map((strength, index) => (
                      <li key={index}>{strength}</li>
                    ))}
                  </ul>
                </div>
              )}
              {result.next_steps?.length > 0 && (
                <div>
                  <h5>下一步练习</h5>
                  <ul>
                    {result.next_steps.map((step, index) => (
                      <li key={index}>{step}</li>
                    ))}
                  </ul>
                </div>
              )}
              <div className="specialist-conversation" aria-label="专项训练讨论">
                {data.messages?.map((entry, index) => (
                  <article
                    key={index}
                    className={`specialist-message ${entry.role === 'user' ? 'user' : 'assistant'}`}
                  >
                    <strong>{entry.role === 'user' ? '你' : 'AI 教练'}</strong>
                    <RichText>{entry.content}</RichText>
                  </article>
                ))}
                {busy === 'chat' && (
                  <p className="specialist-thinking" role="status">
                    AI 教练正在思考<span>…</span>
                  </p>
                )}
              </div>
              <div className="specialist-actions">
                <button
                  className="button secondary"
                  disabled={!!busy || !!modelReason}
                  onClick={() =>
                    void chat(
                      '这道题还有哪些不同的解法或思路？请比较适用条件和复杂度，先给引导，不要直接给出完整代码。',
                    )
                  }
                >
                  探讨其他思路
                </button>
              </div>
              <form
                className="specialist-chat"
                onSubmit={(event) => {
                  event.preventDefault()
                  void chat()
                }}
              >
                <label>
                  追问 AI 教练
                  <textarea
                    aria-label="追问 AI 教练"
                    value={draft}
                    maxLength={2000}
                    disabled={!!busy}
                    onChange={(event) => setDraft(event.target.value)}
                    placeholder="例如：我的边界处理有哪些隐患？还有更简洁的解法吗？"
                  />
                </label>
                <button
                  className="button"
                  type="submit"
                  disabled={!!busy || !draft.trim() || !!modelReason}
                >
                  <Send size={15} />
                  发送追问
                </button>
              </form>
            </div>
          )}
        </>
      )}
    </div>
  )
}

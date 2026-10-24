import React, { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { BookOpen, Check, ChevronDown, ExternalLink, RefreshCw, Send, Sparkles } from 'lucide-react'
import { api, names } from './api'
import './specialist-training.css'

type Topic = { id: string; title: string; description: string; source_url: string }
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
}
type Plan = {
  id: string
  topic_id: string
  topic_title: string
  title: string
  created_at: number
  items: Item[]
}
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

export function SpecialistTrainingPage({
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
  const [count, setCount] = useState(4)
  const [plans, setPlans] = useState<Plan[]>([])
  const [selected, setSelected] = useState('')
  const [expanded, setExpanded] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [consent, setConsent] = useState(false)
  const generation = useRef(0)
  const writing = useRef(false)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      generation.current += 1
    }
  }, [])
  const load = async () => {
    if (writing.current) return
    const token = ++generation.current
    setLoading(true)
    try {
      const [catalog, saved] = await Promise.all([
        api<{ groups: Group[] }>('/training/topics'),
        api<{ plans: Plan[] }>('/training/plans'),
      ])
      if (!alive.current || token !== generation.current) return
      setGroups(catalog.groups)
      setGroupId((old) =>
        catalog.groups.some((group) => group.id === old) ? old : catalog.groups[0]?.id || '',
      )
      setPlans(saved.plans)
      setSelected((old) =>
        saved.plans.some((plan) => plan.id === old) ? old : saved.plans[0]?.id || '',
      )
      setError('')
    } catch (caught) {
      if (alive.current && token === generation.current) {
        setError(message(caught))
        onError(message(caught))
      }
    } finally {
      if (alive.current && token === generation.current) setLoading(false)
    }
  }
  useEffect(() => {
    void load()
  }, [revision])
  const group = groups.find((candidate) => candidate.id === groupId)
  const topics = group?.topics || []
  useEffect(() => {
    setTopicId((old) => (topics.some((topic) => topic.id === old) ? old : topics[0]?.id || ''))
  }, [groupId, groups])
  const topic = topics.find((candidate) => candidate.id === topicId)
  const plan = plans.find((candidate) => candidate.id === selected)
  const generate = async () => {
    if (writing.current || !topicId || !model || !aiReady || !consent) return
    writing.current = true
    const token = ++generation.current
    setBusy(true)
    setError('')
    setLoading(false)
    try {
      const generated = await api<Plan>('/training/plans', 'POST', {
        topic_id: topicId,
        count,
        provider,
        model,
      })
      if (!alive.current || token !== generation.current) return
      setPlans((old) => [generated, ...old.filter((candidate) => candidate.id !== generated.id)])
      setSelected(generated.id)
      setExpanded(null)
      onNotice('专项题单已保存，按路线从模板题开始练习。')
    } catch (caught) {
      if (alive.current && token === generation.current) {
        setError(message(caught))
        onError(message(caught))
      }
    } finally {
      writing.current = false
      if (alive.current) setBusy(false)
    }
  }
  const modelReason = !model || !aiReady ? '请先在连接与设置中配置 AI，并选择可用模型' : ''
  return (
    <section className="card specialist-training">
      <div className="card-heading">
        <div>
          <h2>
            <BookOpen size={22} /> 专项训练
          </h2>
          <p>选择一个专题，从模板入门到综合应用，循序渐进建立解题能力。</p>
        </div>
        <button className="button secondary" disabled={loading || busy} onClick={() => void load()}>
          <RefreshCw size={16} />
          刷新训练
        </button>
      </div>
      <div className="specialist-setup">
        <label>
          专题分类
          <select
            aria-label="专题分类"
            value={groupId}
            disabled={!groups.length || busy}
            onChange={(event) => setGroupId(event.target.value)}
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
            disabled={!topics.length || busy}
            onChange={(event) => setTopicId(event.target.value)}
          >
            {topics.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          题目数量
          <select
            aria-label="专项题量"
            value={count}
            disabled={busy}
            onChange={(event) => setCount(Number(event.target.value))}
          >
            {[3, 4, 5, 6, 7, 8].map((value) => (
              <option key={value} value={value}>
                {value} 道
              </option>
            ))}
          </select>
        </label>
      </div>
      {topic && (
        <p className="help">
          {topic.description}{' '}
          <SafeLink href={topic.source_url}>
            查看专题资料 <ExternalLink size={12} />
          </SafeLink>
        </p>
      )}
      {aiPicker}
      <label className="specialist-consent">
        <input
          type="checkbox"
          checked={consent}
          disabled={busy}
          onChange={(event) => setConsent(event.target.checked)}
        />
        同意将训练偏好和候选题资料发送至当前 AI 生成题单，可能产生模型费用。
      </label>
      <div className="specialist-actions">
        <button
          className="button"
          disabled={busy || loading || !topicId || !!modelReason || !consent}
          title={modelReason || (!consent ? '请先确认 AI 数据发送范围' : '')}
          onClick={() => void generate()}
        >
          <Sparkles size={17} />
          {busy ? 'AI 正在安排训练路线…' : 'AI 生成专项题单'}
        </button>
        {modelReason && <span className="help">{modelReason}</span>}
      </div>
      {error && (
        <div role="alert" className="help specialist-error">
          {error}。已有题单保留，可刷新或重试。
        </div>
      )}
      {loading && <p role="status">正在恢复已保存的专项训练…</p>}
      {plans.length > 0 && (
        <label className="specialist-history">
          已保存训练
          <select
            aria-label="已保存训练"
            value={selected}
            disabled={busy}
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
            <span className="badge neutral">
              {plan.items.filter((item) => item.completed).length}/{plan.items.length} 已完成
            </span>
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
                    <span className={`badge ${item.position === 0 ? 'success' : 'neutral'}`}>
                      {stages[item.stage] || item.stage}
                    </span>
                  </div>
                  <p className="help">
                    {names[item.platform] || item.platform} ·{' '}
                    {item.difficulty_label ||
                      (item.difficulty != null ? `难度 ${item.difficulty}` : '平台未标注难度')}{' '}
                    · {item.problem_id}
                  </p>
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
  const [consent, setConsent] = useState(false)
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
  const modelReason = !model || !aiReady ? '请先配置并选择可用 AI 模型' : ''
  const blocked = !data?.completed
    ? '本题尚未完成 AC，请先通过官方判题并同步提交记录'
    : !source
      ? '没有可匹配的官方 AC 提交，请先同步本题提交'
      : (!source.source_available && !source.can_fetch_source
          ? source.disabled_reason || '平台无法读取该次官方源码，不能使用编辑器或其他提交替代'
          : '') ||
        (!['AC', 'Accepted', 'OK'].includes(source.verdict) ? '仅支持评估本题已通过的官方提交' : '')
  const assess = async () => {
    if (pending.current || loading || blocked || modelReason || !consent || !source) return
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
        consent: true,
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
    if (pending.current || !data?.assessment || !prompt.trim() || modelReason || !consent) return
    pending.current = true
    setBusy('chat')
    setError('')
    const token = ++generation.current
    try {
      const value = await api<Review>(`${path}/chat`, 'POST', {
        message: prompt.trim(),
        provider,
        model,
        consent: true,
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
                  {candidate.platform} · {candidate.account} · #{candidate.submission_id} ·{' '}
                  {candidate.verdict} · {candidate.language}
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
          <label className="specialist-consent">
            <input
              type="checkbox"
              checked={consent}
              disabled={!!busy}
              onChange={(event) => setConsent(event.target.checked)}
            />
            同意将本题题面、所选提交源码及判题信息和讨论内容发送至当前 AI，可能产生费用。
          </label>
          <button
            className="button"
            disabled={!!busy || loading || !!blocked || !!modelReason || !consent}
            title={blocked || modelReason || (!consent ? '请先同意发送分析资料' : '')}
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
                {result.overall_confidence || '未标注'}；AI 判断不等同官方解释。
              </p>
              <RichText>{result.summary}</RichText>
              <div className="specialist-dimensions">
                {result.dimensions?.map((dimension, index) => (
                  <article key={index}>
                    <h5>
                      {dimension.category} <small>{dimension.confidence}</small>
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
                  disabled={!!busy || !consent || !!modelReason}
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
                    maxLength={4000}
                    disabled={!!busy}
                    onChange={(event) => setDraft(event.target.value)}
                    placeholder="例如：我的边界处理有哪些隐患？还有更简洁的解法吗？"
                  />
                </label>
                <button
                  className="button"
                  type="submit"
                  disabled={!!busy || !draft.trim() || !consent || !!modelReason}
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

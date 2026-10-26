import React, { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { RefreshCw, Sparkles, ExternalLink, Code2 } from 'lucide-react'
import { SelectionReportButton } from './selection-report'
import { TrainingCandidates } from './training-candidates'

type Row = Record<string, any>
type Props = {
  revision: number
  provider: string
  model: string
  aiPicker: React.ReactNode
  onError: (s: string) => void
  onNotice: (s: string) => void
  onOpenProblem: (problemId: string) => void
}

export function DailyTasksPage({
  revision,
  provider,
  model,
  aiPicker,
  onError,
  onNotice,
  onOpenProblem,
}: Props) {
  const [data, setData] = useState<Row | null>(null)
  const [readError, setReadError] = useState('')
  const [formReady, setFormReady] = useState(false)
  const dirty = useRef(false)
  const mutationVersion = useRef(0)
  const writing = useRef(false)
  const [goal, setGoal] = useState(1),
    [days, setDays] = useState(30)
  const [selectionType, setSelectionType] = useState('random')
  const [topicId, setTopicId] = useState('')
  const [trainingMode, setTrainingMode] = useState('learn')
  const [groups, setGroups] = useState<Row[]>([])
  const [topicSearch, setTopicSearch] = useState('')
  const [groupId, setGroupId] = useState('')
  const [topicsError, setTopicsError] = useState('')
  const [topicsRevision, setTopicsRevision] = useState(0)
  const modes: Record<string, string> = {
    learn: '新学',
    consolidate: '巩固',
    variation: '变式',
    strengthen: '加强',
  }
  useEffect(() => {
    if (selectionType !== 'specialist' && data?.locked_options?.selection_type !== 'specialist')
      return
    let alive = true
    api('/training/topics')
      .then((value) => {
        if (alive) {
          setGroups(value.groups || [])
          setTopicsError('')
        }
      })
      .catch((error) => {
        if (alive) setTopicsError(error instanceof Error ? error.message : '专题读取失败')
      })
    return () => {
      alive = false
    }
  }, [selectionType, data?.locked_options?.selection_type, topicsRevision])
  const topics = groups.flatMap((group) => group.topics || [])
  const visibleGroups = groups.filter((group) => !groupId || group.id === groupId)
  const matchesSearch = (topic: Row) =>
    `${topic.title} ${topic.description || ''} ${Array.isArray(topic.path) ? topic.path.join(' ') : topic.path || ''} ${(topic.keywords || []).join(' ')} ${topic.id}`
      .toLowerCase()
      .includes(topicSearch.toLowerCase()) || topic.id === topicId
  const validPreferences =
    Number.isInteger(goal) &&
    goal >= 1 &&
    goal <= 50 &&
    Number.isInteger(days) &&
    days >= 1 &&
    days <= 365 &&
    (selectionType === 'random' || topics.some((topic) => topic.id === topicId))
  const [durations, setDurations] = useState<Record<number, string>>({})
  const [busy, setBusy] = useState(false)
  const [rollingTaskId, setRollingTaskId] = useState<number | null>(null)
  const [rollErrors, setRollErrors] = useState<Record<number, string>>({})
  const disclosureId = React.useId()
  const fail = (error: unknown) =>
    onError(error instanceof Error ? error.message : '每日任务请求失败')
  const applyRead = (value: Row) => {
    setData(value)
    setReadError('')
    setFormReady(true)
    // Background progress refreshes must not replace a preference the user is editing.
    if (!dirty.current) {
      setGoal(value.settings.daily_goal)
      setDays(value.settings.duration_days)
      setSelectionType(value.settings.selection_type || 'random')
      setTopicId(value.settings.topic_id || '')
      setTrainingMode(value.settings.training_mode || 'learn')
    }
  }
  const load = async () => {
    const version = mutationVersion.current
    try {
      const value = await api('/daily')
      if (version === mutationVersion.current && !writing.current) applyRead(value)
      return value
    } catch (error) {
      if (version === mutationVersion.current)
        setReadError(error instanceof Error ? error.message : '每日任务读取失败')
      throw error
    }
  }
  useEffect(() => {
    let alive = true
    const version = mutationVersion.current
    api('/daily')
      .then((value) => {
        if (!alive || version !== mutationVersion.current || writing.current) return
        applyRead(value)
      })
      .catch((error) => {
        if (alive && version === mutationVersion.current) {
          setReadError(error instanceof Error ? error.message : '每日任务读取失败')
          fail(error)
        }
      })
    return () => {
      alive = false
    }
  }, [revision])
  const save = async () => {
    if (!formReady || writing.current || !validPreferences) return
    setBusy(true)
    writing.current = true
    mutationVersion.current += 1
    try {
      const value = await api('/daily/settings', 'PUT', {
        daily_goal: goal,
        duration_days: days,
        selection_type: selectionType,
        topic_id: selectionType === 'specialist' ? topicId : null,
        training_mode: selectionType === 'specialist' ? trainingMode : 'learn',
      })
      dirty.current = false
      applyRead(value)
      onNotice('每日偏好已保存；已经生成的今日题量与训练方式保持锁定。')
    } catch (error) {
      fail(error)
    } finally {
      mutationVersion.current += 1
      writing.current = false
      setBusy(false)
    }
  }
  const select = async (task_id?: number) => {
    if (!formReady || writing.current || !model || !data?.active) return
    const replacing = task_id !== undefined
    if (task_id !== undefined) {
      const task = data?.plan?.tasks?.find((item: Row) => item.task_id === task_id)
      if (
        data?.plan?.status !== 'ready' ||
        !task ||
        task.completed ||
        (data.rerolls_remaining ?? 0) <= 0
      )
        return
      setRollingTaskId(task_id)
      setRollErrors((old) => ({ ...old, [task_id]: '' }))
    } else if (['ready', 'generating'].includes(data?.plan?.status)) {
      return
    }
    setBusy(true)
    writing.current = true
    mutationVersion.current += 1
    try {
      setData(
        await api('/daily/roll', 'POST', { provider, model, ...(replacing ? { task_id } : {}) }),
      )
      onNotice(
        replacing
          ? 'AI 已替换指定题目，消耗 1 次 Roll，其余每日题目保持不变。'
          : '今日题单已生成，题量与 Roll 总额度已锁定。',
      )
    } catch (error) {
      if (task_id !== undefined) {
        setRollErrors((old) => ({
          ...old,
          [task_id]: error instanceof Error ? error.message : 'AI 换题失败',
        }))
      }
      fail(error)
    } finally {
      mutationVersion.current += 1
      writing.current = false
      setBusy(false)
      setRollingTaskId(null)
      await load().catch(() => {})
    }
  }
  const saveTime = async (task: Row) => {
    if (writing.current) return
    writing.current = true
    mutationVersion.current += 1
    setBusy(true)
    try {
      const value = Number(durations[task.task_id] ?? task.duration_minutes ?? '')
      setData(await api('/daily/time', 'POST', { task_id: task.task_id, duration_minutes: value }))
      onNotice('实际用时已保存，可修改；自行填写的用时不会改变签到状态。')
    } catch (error) {
      fail(error)
    } finally {
      mutationVersion.current += 1
      writing.current = false
      setBusy(false)
    }
  }
  const ready = data?.plan?.status === 'ready'
  const generating = data?.plan?.status === 'generating'
  const check = data?.checkin || {}
  const lockedGoal = data?.locked_goal || data?.settings?.daily_goal || goal
  const rerollsLimit = data?.rerolls_limit ?? lockedGoal
  const rerollsRemaining = data?.rerolls_remaining ?? 0
  const rollDisabledReason = (task: Row) => {
    if (!ready) return '题单尚未生成成功，请先重试生成'
    if (task.completed) return '已完成题目不能 Roll'
    if (!model) return '请先在连接与设置中保存默认 AI 模型'
    if (!data?.active) return '训练周期未开启，请先保存每日任务'
    if (rerollsRemaining <= 0) return '今日 Roll 机会已用完'
    if (busy && rollingTaskId !== task.task_id) return '请等待当前操作完成'
    return ''
  }
  return (
    <section className="card daily-mission">
      <div className="card-heading">
        <div>
          <h2>每日任务与签到</h2>
          <p>
            平台当日通过一题即可普通签到；完成今日整张题单升级为 K
            签。每日题单有几道题，当天就有几次 Roll 机会；每次由 AI 只替换你点击的那一道题。
          </p>
        </div>
        <span className={`badge ${check.kind === 'k' ? 'success' : 'neutral'}`}>
          {check.kind === 'k' ? '今日 K 签' : check.kind === 'normal' ? '今日普通签到' : '尚未签到'}
        </span>
      </div>
      {!formReady && !readError && <p role="status">正在读取每日任务设置；加载完成后可编辑。</p>}
      {readError && (
        <div role="alert" className="help">
          <p>
            每日任务读取失败：{readError}。
            {data ? '已显示的题单已保留。' : '尚未读取到题单，请重试；这不表示已保存的题目丢失。'}
          </p>
          <button className="button secondary" disabled={busy} onClick={() => load().catch(fail)}>
            重试读取每日任务
          </button>
        </div>
      )}
      <div className="practice-filters">
        <label>
          每日训练方式
          <select
            value={selectionType}
            disabled={busy || !formReady}
            onChange={(e) => {
              dirty.current = true
              setSelectionType(e.target.value)
            }}
          >
            <option value="random">随机训练 · AI 根据近期情况选题</option>
            <option value="specialist">专项训练 · 选择专题练习</option>
          </select>
        </label>
        <label>
          每日题量
          <input
            type="number"
            min={1}
            max={50}
            step={1}
            value={goal || ''}
            disabled={busy || !formReady}
            onChange={(e) => {
              dirty.current = true
              setGoal(Number(e.target.value))
            }}
          />
        </label>
        <label>
          持续天数
          <input
            type="number"
            min={1}
            max={365}
            value={days}
            disabled={busy || !formReady}
            onChange={(e) => {
              dirty.current = true
              setDays(Number(e.target.value))
            }}
          />
        </label>
        <button
          className="button secondary"
          disabled={busy || !formReady || !validPreferences}
          onClick={save}
        >
          保存每日任务
        </button>
      </div>
      {selectionType === 'specialist' && (
        <div className="practice-filters">
          <label>
            训练分类
            <select
              aria-label="训练分类"
              value={groupId}
              disabled={busy || !formReady}
              onChange={(e) => setGroupId(e.target.value)}
            >
              <option value="">全部分类</option>
              {groups.map((group) => (
                <option value={group.id} key={group.id}>
                  {group.title}
                </option>
              ))}
            </select>
          </label>
          <label>
            搜索专题
            <input
              aria-label="搜索专题"
              type="search"
              value={topicSearch}
              placeholder="输入名称或算法标签"
              onChange={(e) => setTopicSearch(e.target.value)}
            />
          </label>
          <label>
            训练专题
            <select
              aria-label="训练专题"
              value={topicId}
              disabled={busy || !formReady}
              onChange={(e) => {
                dirty.current = true
                setTopicId(e.target.value)
              }}
            >
              <option value="">请选择专题</option>
              {visibleGroups.map((group) => (
                <optgroup label={group.title} key={group.id}>
                  {(group.topics || []).filter(matchesSearch).map((topic: Row) => (
                    <option key={topic.id} value={topic.id}>
                      {'　'.repeat(Math.min(6, topic.depth || 0))}
                      {topic.title}
                    </option>
                  ))}
                </optgroup>
              ))}
              {topicId &&
                !visibleGroups.some((group) =>
                  (group.topics || []).some((topic: Row) => topic.id === topicId),
                ) && (
                  <option value={topicId}>
                    {topics.find((topic) => topic.id === topicId)?.title || topicId}
                  </option>
                )}
            </select>
          </label>
          <label>
            训练难度
            <select
              aria-label="训练难度"
              value={trainingMode}
              disabled={busy || !formReady}
              onChange={(e) => {
                dirty.current = true
                setTrainingMode(e.target.value)
              }}
            >
              {Object.entries(modes).map(([id, title]) => (
                <option key={id} value={id}>
                  {title}
                </option>
              ))}
            </select>
          </label>
          <p className="help">
            新学从模板题起步；巩固、变式、加强按所选专题安排递进练习。保存偏好后再点击生成今日题单。
          </p>
          {topicsError && (
            <div role="alert">
              专题读取失败：{topicsError}{' '}
              <button className="button secondary" onClick={() => setTopicsRevision((v) => v + 1)}>
                重试读取专题
              </button>
            </div>
          )}
        </div>
      )}
      {selectionType === 'specialist' && (
        <TrainingCandidates
          topicId={topicId}
          topicTitle={topics.find((topic) => topic.id === topicId)?.title}
          mode={trainingMode}
          disabled={busy || !formReady}
          revision={revision}
          aiReady={!!model}
        />
      )}
      {data?.plan && (
        <p className="help">
          今日已锁定：
          {data.locked_options?.selection_type === 'specialist'
            ? `专项训练 · ${topics.find((topic) => topic.id === data.locked_options.topic_id)?.title || data.locked_options.topic_id} · ${modes[data.locked_options.training_mode] || '新学'}`
            : '随机训练 · AI 根据近期情况选题'}
          。修改训练方式、专题及题量从下次生成生效，换题仍遵循今日设置。
        </p>
      )}
      <p className="help">
        {data?.active
          ? `当前周期 ${data.started_at} 至 ${data.ends_at}（北京时间）。`
          : '保存设置开始新的训练周期。'}{' '}
        今日目标 {data?.locked_goal || goal}{' '}
        题；当日题单生成后，修改偏好从下次生成起生效。完成后可记录实际用时；修改持续天数会在下个训练周期生效。
      </p>
      <div className="checkin-month">
        <span>
          本月 K 签 <strong>{check.month_k || 0}</strong>
        </span>
        <span>
          本月普通签到 <strong>{check.month_normal || 0}</strong>
        </span>
        <span>
          本月总签到 <strong>{check.month_total || 0}</strong>
        </span>
        <span>
          累计 K 签 <strong>{check.all_k || 0}</strong>
        </span>
        <span>
          累计普通签到 <strong>{check.all_normal || 0}</strong>
        </span>
        <span>
          累计签到 <strong>{check.all_total || 0}</strong>
        </span>
        <span>
          今日题单进度{' '}
          <strong>
            {data?.plan?.completed_ids?.length || 0}/{data?.locked_goal || goal}
          </strong>
        </span>
      </div>
      {data?.legacy_notice && <p className="help">{data.legacy_notice}</p>}
      <p className="help">
        签到依据采集或工作台同步的官方 AC
        记录；“标记完成”和代码自测不计签到。每天按北京时间结算，同一天只记一种签到，K
        签会替代普通签。
      </p>
      <div className="ai-controls">
        {aiPicker}
        {!ready && (
          <button
            className="button primary"
            disabled={busy || generating || !data?.active || !model}
            aria-describedby={disclosureId}
            onClick={() => select()}
          >
            <Sparkles size={16} />
            {busy || generating
              ? '正在生成题单…'
              : `AI 生成今日 ${data?.settings?.daily_goal || goal} 道题`}
          </button>
        )}
        <button className="button secondary" disabled={busy} onClick={() => load().catch(fail)}>
          <RefreshCw size={15} />
          刷新已验证进度
        </button>
      </div>
      <p className="help" id={disclosureId}>
        点击“AI 生成”或“Roll ·
        换这道题”会将训练偏好、每日目标、练习用时资料和公开候选题信息发送给所选 AI，可能产生 API
        费用。 查看题单、修改偏好和刷新进度不会触发 AI 选题。
        {selectionType === 'specialist' &&
          '专项细类候选不足时，生成或换题还可能用默认 AI 额外读取最多 6 道候选官方题面，核查所选方法是否可行，可能增加费用；核查不等于官方标签或唯一解，也不能替代可信模板证据。'}
      </p>
      {data?.plan?.status === 'failed' && (
        <p className="help" role="alert">
          上次生成失败：{data.plan.failure?.message || '题单未生成成功，请检查设置后手动重试。'}{' '}
          不会自动重试或产生新的 AI 费用；点击“AI 生成”才会重新请求。
        </p>
      )}
      {ready && (
        <p className="help">
          今日 Roll 总额度 {rerollsLimit} 次，剩余 {rerollsRemaining}{' '}
          次（随今日题量锁定）。每次只替换点击的那道题，其余题目不变；失败不消耗机会，已完成题目不能换题，今日出现过的题目不会重复。
        </p>
      )}
      <div className="daily-task-list">
        {(data?.plan?.tasks || []).map((task: Row) => (
          <article className="daily-problem" key={task.task_id}>
            <div>
              <strong>
                {task.problem_id} · {task.title}
              </strong>
              <p>{task.reason}</p>
              <small>
                {task.difficulty} 分 · {task.completed ? '平台已通过' : '待完成'}
              </small>
            </div>
            {task.completed && (
              <div>
                <label>
                  实际用时（分钟）
                  <input
                    type="number"
                    min={1}
                    max={1440}
                    step={1}
                    value={durations[task.task_id] ?? task.duration_minutes ?? ''}
                    disabled={busy}
                    onChange={(e) =>
                      setDurations((old) => ({ ...old, [task.task_id]: e.target.value }))
                    }
                  />
                </label>
                <button
                  className="button secondary"
                  disabled={
                    busy ||
                    !Number.isInteger(Number(durations[task.task_id] ?? task.duration_minutes)) ||
                    Number(durations[task.task_id] ?? task.duration_minutes) < 1 ||
                    Number(durations[task.task_id] ?? task.duration_minutes) > 1440
                  }
                  onClick={() => saveTime(task)}
                >
                  {task.duration_minutes ? '修改实际用时' : '保存实际用时'}
                </button>
                <p className="help">
                  填写做题实际花费的分钟数，可修改；仅用于训练分析，不作为通过证明。
                </p>
              </div>
            )}
            <div className="daily-task-actions">
              <button className="button primary" onClick={() => onOpenProblem(task.id)}>
                <Code2 size={14} /> 去工作台
              </button>
              <a className="button secondary" href={task.url} target="_blank" rel="noreferrer">
                打开原题
                <ExternalLink size={14} />
              </a>
              <SelectionReportButton
                key={`${task.id}:${task.ai_report_id || 'legacy'}`}
                reportId={task.ai_report_id}
                problemId={task.id}
                title={task.title}
              />
              <div>
                <button
                  className="button primary"
                  aria-label={`Roll · 换这道题：${task.problem_id}`}
                  aria-describedby={`${disclosureId} ${disclosureId}-roll-${task.task_id}`}
                  aria-busy={rollingTaskId === task.task_id}
                  title={rollDisabledReason(task) || '消耗 1 次机会，让 AI 重新选择这道题'}
                  disabled={busy || Boolean(rollDisabledReason(task))}
                  onClick={() => select(task.task_id)}
                >
                  <RefreshCw size={15} />
                  {rollingTaskId === task.task_id
                    ? 'AI 正在 Roll 这道题…'
                    : rollErrors[task.task_id]
                      ? '重试 Roll · 换这道题'
                      : 'Roll · 换这道题'}
                </button>
                <p className="help" id={`${disclosureId}-roll-${task.task_id}`}>
                  {rollingTaskId === task.task_id
                    ? '正在重新选这道题，其余题目保持不变，请稍候。'
                    : rollDisabledReason(task) || `仅换本题 · 剩余 ${rerollsRemaining} 次`}
                </p>
                {rollErrors[task.task_id] && (
                  <p className="help" role="alert">
                    本题 Roll 失败：{rollErrors[task.task_id]}。原题保留，请重试；失败不消耗机会。
                  </p>
                )}
              </div>
            </div>
          </article>
        ))}
      </div>
    </section>
  )
}

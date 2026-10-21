import React, { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { RefreshCw, Sparkles, ExternalLink, Code2 } from 'lucide-react'

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

export function DailyTasksPage({ revision, provider, model, aiPicker, onError, onNotice, onOpenProblem }: Props) {
  const [data, setData] = useState<Row | null>(null)
  const [formReady, setFormReady] = useState(false)
  const dirty = useRef(false)
  const mutationVersion = useRef(0)
  const writing = useRef(false)
  const [goal, setGoal] = useState(1),
    [days, setDays] = useState(30)
  const [durations, setDurations] = useState<Record<number, string>>({})
  const [busy, setBusy] = useState(false)
  const disclosureId = React.useId()
  const fail = (error: unknown) =>
    onError(error instanceof Error ? error.message : '每日任务请求失败')
  const applyRead = (value: Row) => {
    setData(value)
    setFormReady(true)
    // Background progress refreshes must not replace a preference the user is editing.
    if (!dirty.current) {
      setGoal(value.settings.daily_goal)
      setDays(value.settings.duration_days)
    }
  }
  const load = async () => {
    const version = mutationVersion.current
    const value = await api('/daily')
    if (version === mutationVersion.current && !writing.current) applyRead(value)
    return value
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
        if (alive) fail(error)
      })
    return () => {
      alive = false
    }
  }, [revision])
  const save = async () => {
    if (!formReady || writing.current) return
    setBusy(true)
    writing.current = true
    mutationVersion.current += 1
    try {
      const value = await api('/daily/settings', 'PUT', {
        daily_goal: goal,
        duration_days: days,
      })
      dirty.current = false
      applyRead(value)
      onNotice('每日偏好已保存；已经生成的今日题量保持锁定。')
    } catch (error) {
      fail(error)
    } finally {
      mutationVersion.current += 1
      writing.current = false
      setBusy(false)
    }
  }
  const select = async (task_id?: number) => {
    if (!formReady || writing.current) return
    setBusy(true)
    writing.current = true
    mutationVersion.current += 1
    try {
      setData(
        await api('/daily/roll', 'POST', { provider, model, ...(task_id ? { task_id } : {}) }),
      )
      onNotice(task_id ? '已替换一道题目，其余每日题目保持不变。' : '今日题单已生成，题量已锁定。')
    } catch (error) {
      fail(error)
    } finally {
      mutationVersion.current += 1
      writing.current = false
      setBusy(false)
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
  return (
    <section className="card daily-mission">
      <div className="card-heading">
        <div>
          <h2>每日任务与签到</h2>
          <p>
            平台当日通过一题即可普通签到；完成今日整张题单升级为 K
            签。每天可以换题两次，每次替换一道。
          </p>
        </div>
        <span className={`badge ${check.kind === 'k' ? 'success' : 'neutral'}`}>
          {check.kind === 'k' ? '今日 K 签' : check.kind === 'normal' ? '今日普通签到' : '尚未签到'}
        </span>
      </div>
      {!formReady && <p role="status">正在读取每日任务设置；加载完成后可编辑。</p>}
      <div className="practice-filters">
        <label>
          每日题量
          <select
            value={goal}
            disabled={busy || !formReady}
            onChange={(e) => {
              dirty.current = true
              setGoal(Number(e.target.value))
            }}
          >
            {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
              <option value={n} key={n}>
                {n} 题
              </option>
            ))}
          </select>
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
          disabled={busy || !formReady || !Number.isInteger(days) || days < 1 || days > 365}
          onClick={save}
        >
          保存每日任务
        </button>
      </div>
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
        点击“AI 生成”或“换这道题”会将训练偏好、每日目标、练习用时资料和公开候选题信息发送给所选
        AI，可能产生 API 费用。 查看题单、修改偏好和刷新进度不会触发 AI 选题。
      </p>
      {ready && (
        <p className="help">
          今日还可换题 {data.rerolls_remaining}{' '}
          次。失败不消耗机会；已完成题目不能换题，今日出现过的题目不会重复。
        </p>
      )}
      <div className="daily-task-list">
        {ready &&
          data.plan.tasks.map((task: Row) => (
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
                <button
                  className="button secondary"
                  aria-describedby={disclosureId}
                  disabled={busy || task.completed || !model || data.rerolls_remaining === 0}
                  onClick={() => select(task.task_id)}
                >
                  换这道题
                </button>
              </div>
            </article>
          ))}
      </div>
    </section>
  )
}

import React from 'react'

type Statistics = {
  submitted_count?: number
  acceptance_rate?: number | null
  rate_unit?: string | null
  scope?: string
  count_kind?: string
  source?: string
  participant_unit?: string
}
export function SubmissionStatistics({ value }: { value?: Statistics | null }) {
  const validCount = Number.isSafeInteger(value?.submitted_count) && value!.submitted_count! >= 0
  const rate = value?.acceptance_rate
  const validRate = typeof rate === 'number' && Number.isFinite(rate) && rate >= 0 && rate <= 1 && ['submission','participant'].includes(value?.rate_unit || '')
  if (!validCount) return <small className="muted">提交统计尚未同步</small>
  const scope = value?.scope === 'all_time' ? '累计' : '赛时'
  const kind = value?.count_kind === 'scoring_attempts_until_first_solve' ? '计分提交' : '提交'
  const rateLabel = value?.rate_unit === 'participant' ? (value?.participant_unit === 'team' ? '队伍通过率' : '人数通过率') : '提交通过率'
  return (
    <small className="problem-submission-statistics" title={`${scope}统计；来源：${value?.source || '未知'}`}>
      {scope}{kind} {value!.submitted_count!.toLocaleString('zh-CN')} 次 · {rateLabel}{' '}
      {validRate ? `${(rate * 100).toFixed(1)}%` : '未知（无有效分母）'}
    </small>
  )
}

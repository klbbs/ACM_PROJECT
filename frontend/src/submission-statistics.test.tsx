import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it } from 'vitest'
import { SubmissionStatistics } from './submission-statistics'
const text = (value?: React.ComponentProps<typeof SubmissionStatistics>['value']) => {
  const markup = renderToStaticMarkup(<SubmissionStatistics value={value} />)
  return new DOMParser().parseFromString(markup, 'text/html').body.textContent || ''
}
it('distinguishes submission rates and participant rates', () => {
  expect(text({submitted_count:100,acceptance_rate:.25,rate_unit:'submission',scope:'all_time'})).toContain('累计提交 100 次 · 提交通过率 25.0%')
  expect(text({submitted_count:120,acceptance_rate:.5,rate_unit:'participant',scope:'contest',count_kind:'scoring_attempts_until_first_solve'})).toContain('赛时计分提交 120 次 · 人数通过率 50.0%')
})
it('does not replace unknown counts and rates with zero', () => {
  expect(text()).toBe('提交统计尚未同步')
  expect(text({submitted_count:0,acceptance_rate:null})).toContain('未知（无有效分母）')
  expect(text({submitted_count:0,acceptance_rate:null})).not.toContain('0.0%')
})

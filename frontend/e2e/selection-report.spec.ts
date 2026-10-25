import { test, expect } from '@playwright/test'

// Browser-only synthetic report transport; no real OJ or model is called.
test('mobile report confirmation, read retry and Escape stay within the viewport', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  const pid = 'codeforces:4-A'
  const reportId = 'd'.repeat(32)
  let reads = 0
  const report = {
    id: reportId, problem_id: pid, created_at: 1792850000, provider: 'api', model: 'synthetic-model',
    result: {
      problem_id: pid, suitable: true, selection_fit: '用于移动端流程检查。',
      problem_summary: '**隔离报告正文**', constraints: ['1 <= n <= 100'],
      approach: ['只讨论合成题面提供的要求。'],
      implementation_notes: ['```text\n' + 'long_token'.repeat(100) + '\n```'],
      complexity: '材料不足，无法确定。', limitations: ['未运行代码，不是官方题解。'],
    },
  }
  await page.route('**/api/daily', (route) => route.fulfill({ json: {
    settings: { daily_goal: 1, duration_days: 14 }, active: true, locked_goal: 1,
    rerolls_limit: 1, rerolls_remaining: 1, checkin: {},
    plan: { status: 'ready', tasks: [{
      task_id: 101, id: pid, problem_id: '4-A', platform: 'codeforces',
      title: '移动端报告示例', ai_report_id: reportId, completed: false,
      url: 'https://codeforces.com/problemset/problem/4/A',
    }] },
  } }))
  await page.route(`**/api/selection-reports/${reportId}`, (route) => {
    reads += 1
    return reads === 1
      ? route.fulfill({ status: 503, json: { detail: '隔离读取失败，请重试' } })
      : route.fulfill({ json: report })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '打开菜单', exact: true }).click()
  await page.getByRole('button', { name: '训练', exact: true }).click()
  const entry = page.locator('.daily-mission').getByRole('button', { name: '查看 AI 报告' })
  await entry.click()
  const modal = page.getByRole('dialog')
  await expect(modal).toContainText('此报告为ai生成的代码解析报告，你确定要现在看吗？')
  expect(reads).toBe(0)
  await modal.getByRole('button', { name: '取消', exact: true }).click()
  await expect(modal).toHaveCount(0)
  expect(reads).toBe(0)
  await entry.click()
  await modal.getByRole('button', { name: '确定查看', exact: true }).click()
  await expect(modal.getByRole('alert')).toContainText('隔离读取失败')
  await modal.getByRole('button', { name: '重试读取', exact: true }).click()
  await expect(modal.locator('strong')).toHaveText('隔离报告正文')
  expect(reads).toBe(2)
  const bounds = await modal.boundingBox()
  expect(bounds).not.toBeNull()
  expect(bounds!.x).toBeGreaterThanOrEqual(0)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(375)
  expect(await modal.evaluate((node) => node.scrollWidth <= node.clientWidth + 2)).toBe(true)
  await page.keyboard.press('Escape')
  await expect(modal).toHaveCount(0)
  await expect(entry).toBeFocused()
  expect(reads).toBe(2)
})

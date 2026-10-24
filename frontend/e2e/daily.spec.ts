import { test, expect } from '@playwright/test'

test('daily plan locks k Roll chances, replaces only the selected problem, and cannot claim a fake check-in', async ({
  page,
}) => {
  const rollRequests: unknown[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith('/api/daily/roll'))
      rollRequests.push(request.postDataJSON())
  })
  await page.route('**/api/ai/models?provider=api', (route) =>
    route.fulfill({
      json: {
        items: [{ id: 'fixture-daily', name: '隔离验收模型' }],
        default_model: 'fixture-daily',
        source: 'fixture',
        note: '仅测试模型',
      },
    }),
  )
  await page.goto('/')
  await page.getByRole('button', { name: '训练', exact: true }).click()
  const daily = page.locator('.daily-mission')
  await expect(daily.getByLabel('每日题量')).toBeEnabled()
  await daily.getByLabel('每日题量').selectOption('2')
  await expect(daily.getByLabel('每天训练时间（分钟）')).toHaveCount(0)
  await daily.getByLabel('持续天数').fill('14')
  await daily.getByRole('button', { name: '保存每日任务', exact: true }).click()
  await expect(daily).toContainText('今日目标 2 题')
  await daily.getByLabel('AI 通道').selectOption('api')
  await daily.getByRole('button', { name: '读取可用模型', exact: true }).click()
  await daily.getByLabel('选择模型').selectOption('fixture-daily')
  await expect(daily.getByRole('checkbox', { name: /同意将训练偏好/ })).toHaveCount(0)
  await expect(daily).toContainText('点击“AI 生成”或“Roll · 换这道题”会将训练偏好')
  await expect(daily.getByRole('button', { name: 'AI 生成今日 2 道题' })).toBeEnabled()
  expect(rollRequests).toHaveLength(0)
  await daily.getByRole('button', { name: 'AI 生成今日 2 道题' }).click()
  await expect(daily.locator('.daily-problem')).toHaveCount(2)
  expect(rollRequests).toEqual([{ provider: 'api', model: 'fixture-daily' }])
  await expect(daily).toContainText('今日 Roll 总额度 2 次，剩余 2 次')
  const selectedTask = (await (await page.request.get('/api/daily')).json()).plan.tasks[0]
  const original = await daily.locator('.daily-problem').first().locator('strong').innerText()
  const unchanged = await daily.locator('.daily-problem').nth(1).locator('strong').innerText()
  const rollSelected = daily.locator('.daily-problem').first().getByRole('button', { name: /^Roll · 换这道题：/ })
  await rollSelected.click()
  await expect(daily).toContainText('今日 Roll 总额度 2 次，剩余 1 次')
  await expect(daily.locator('.daily-problem').first().locator('strong')).not.toHaveText(original)
  await expect(daily.locator('.daily-problem').nth(1).locator('strong')).toHaveText(unchanged)
  const replacementTask = (await (await page.request.get('/api/daily')).json()).plan.tasks[0]
  await rollSelected.click()
  await expect(daily).toContainText('今日 Roll 总额度 2 次，剩余 0 次')
  await expect(daily.locator('.daily-problem').nth(1).locator('strong')).toHaveText(unchanged)
  expect(rollRequests).toEqual([
    { provider: 'api', model: 'fixture-daily' },
    { provider: 'api', model: 'fixture-daily', task_id: selectedTask.task_id },
    { provider: 'api', model: 'fixture-daily', task_id: replacementTask.task_id },
  ])
  await expect(rollSelected).toBeDisabled()
  await expect(daily.locator('.daily-problem').nth(1).getByRole('button', { name: /^Roll · 换这道题：/ })).toBeDisabled()
  await expect(daily.getByRole('button', { name: '记录完成', exact: true })).toHaveCount(0)
  const forged = await page.request.post('/api/daily/complete', {
    data: { problem_id: 'codeforces:999-A' },
  })
  expect(forged.status()).toBe(409)
  await expect(daily).toContainText('尚未签到')
  await daily.getByLabel('每日题量').selectOption('1')
  await daily.getByRole('button', { name: '保存每日任务', exact: true }).click()
  await expect(daily).toContainText('今日目标 2 题')
  await expect(daily.locator('.daily-problem')).toHaveCount(2)
  await page.screenshot({ path: 'test-results/daily-plan.png', fullPage: true })
  const task = (await (await page.request.get('/api/daily')).json()).plan.tasks[0]
  const state = await (await page.request.get(`/api/workbench/problem/${encodeURIComponent(task.id)}`)).json()
  const draft = `// today's practice: ${task.id}`
  await page.request.put(`/api/workbench/problem/${encodeURIComponent(task.id)}/draft`, {
    data: { code: draft, language: 'cpp20', revision: state.draft.revision },
  })
  await daily.getByRole('button', { name: '去工作台', exact: true }).first().click()
  await expect(page.locator('.wb-statement')).toContainText(task.title)
  await expect(page.locator('.cm-content')).toHaveText(draft)
  await expect(page.getByLabel('代码语言')).toHaveValue('cpp20')
  expect(rollRequests).toHaveLength(3)
  await page.getByRole('button', { name: '训练', exact: true }).click()
  await expect(daily.locator('.daily-problem')).toHaveCount(2)
  await expect(daily).toContainText('今日 Roll 总额度 2 次，剩余 0 次')
  const savedTitles = await daily.locator('.daily-problem strong').allTextContents()
  await page.reload()
  await page.getByRole('button', { name: '训练', exact: true }).click()
  await expect(daily.locator('.daily-problem')).toHaveCount(2)
  await expect(daily.locator('.daily-problem strong')).toHaveText(savedTitles)
  await expect(daily).toContainText('今日 Roll 总额度 2 次，剩余 0 次')
  expect(rollRequests).toHaveLength(3)
})

test('verified completed task accepts actual minutes and allows corrections without changing check-in', async ({
  page,
}) => {
  let minutes: number | null = null
  const payloads: unknown[] = []
  const state = () => ({
    settings: { daily_goal: 1, duration_days: 14 },
    active: true,
    started_at: '2026-10-20',
    ends_at: '2026-11-02',
    locked_goal: 1,
    rerolls_limit: 1,
    rerolls_remaining: 1,
    checkin: { kind: 'k', month_k: 1, all_k: 1, month_total: 1, all_total: 1 },
    plan: {
      status: 'ready',
      completed_ids: ['atcoder:abc300_a'],
      tasks: [
        {
          task_id: 42,
          id: 'atcoder:abc300_a',
          problem_id: 'abc300_a',
          title: 'Actual-time fixture',
          completed: true,
          difficulty: 1000,
          url: 'https://atcoder.jp/contests/abc300/tasks/abc300_a',
          duration_minutes: minutes,
        },
      ],
    },
  })
  await page.route('**/api/daily', (route) => route.fulfill({ json: state() }))
  await page.route('**/api/daily/time', async (route) => {
    const body = route.request().postDataJSON()
    payloads.push(body)
    minutes = body.duration_minutes
    await route.fulfill({ json: state() })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '训练', exact: true }).click()
  const daily = page.locator('.daily-mission')
  await expect(daily.getByLabel('每天训练时间（分钟）')).toHaveCount(0)
  await expect(daily.getByRole('button', { name: '保存实际用时', exact: true })).toBeDisabled()
  await daily.getByLabel('实际用时（分钟）').fill('45')
  await daily.getByRole('button', { name: '保存实际用时', exact: true }).click()
  await expect(daily.getByRole('button', { name: '修改实际用时', exact: true })).toBeVisible()
  await daily.getByLabel('实际用时（分钟）').fill('30')
  await daily.getByRole('button', { name: '修改实际用时', exact: true }).click()
  await expect.poll(() => payloads.length).toBe(2)
  expect(payloads).toEqual([
    { task_id: 42, duration_minutes: 45 },
    { task_id: 42, duration_minutes: 30 },
  ])
  await expect(daily).toContainText('今日 K 签')
  await page.reload()
  await page.getByRole('button', { name: '训练', exact: true }).click()
  await expect(daily.getByLabel('实际用时（分钟）')).toHaveValue('30')
})

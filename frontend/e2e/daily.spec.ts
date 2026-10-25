import { test, expect } from '@playwright/test'

test('daily plan locks k Roll chances, replaces only the selected problem, and cannot claim a fake check-in', async ({
  page,
}) => {
  const rollRequests: unknown[] = []
  const reportRequests: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith('/api/daily/roll'))
      rollRequests.push(request.postDataJSON())
    if (request.method() === 'GET' && request.url().includes('/api/selection-reports/'))
      reportRequests.push(request.url())
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
  await daily.getByLabel('每日题量').fill('2')
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
  expect(selectedTask.ai_report_id).toMatch(/^[a-f0-9]{32}$/)
  const reportAction = daily.locator('.daily-problem').first().getByRole('button', { name: '查看 AI 报告' })
  await expect(reportAction).toBeEnabled()
  await reportAction.click()
  const reportDialog = page.getByRole('dialog')
  await expect(reportDialog).toContainText('此报告为ai生成的代码解析报告，你确定要现在看吗？')
  expect(reportRequests).toHaveLength(0)
  await reportDialog.getByRole('button', { name: '取消', exact: true }).click()
  await expect(reportDialog).toHaveCount(0)
  expect(reportRequests).toHaveLength(0)
  const readingsBeforeView = (await (await page.request.post('/_fixture/selection-report-count')).json()).count
  await reportAction.click()
  await reportDialog.getByRole('button', { name: '确定查看', exact: true }).click()
  await expect(reportDialog.locator('strong')).toContainText('合成题面阅读报告')
  await expect(reportDialog).toContainText(selectedTask.id)
  expect(reportRequests).toHaveLength(1)
  expect((await (await page.request.post('/_fixture/selection-report-count')).json()).count).toBe(readingsBeforeView)
  await reportDialog.getByRole('button', { name: '关闭 AI 报告' }).click()
  await expect(reportDialog).toHaveCount(0)
  const original = await daily.locator('.daily-problem').first().locator('strong').innerText()
  const unchanged = await daily.locator('.daily-problem').nth(1).locator('strong').innerText()
  const rollSelected = daily
    .locator('.daily-problem')
    .first()
    .getByRole('button', { name: /^Roll · 换这道题：/ })
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
  await expect(
    daily
      .locator('.daily-problem')
      .nth(1)
      .getByRole('button', { name: /^Roll · 换这道题：/ }),
  ).toBeDisabled()
  await expect(daily.getByRole('button', { name: '记录完成', exact: true })).toHaveCount(0)
  const forged = await page.request.post('/api/daily/complete', {
    data: { problem_id: 'codeforces:999-A' },
  })
  expect(forged.status()).toBe(409)
  await expect(daily).toContainText('尚未签到')
  await daily.getByLabel('每日题量').fill('1')
  await daily.getByRole('button', { name: '保存每日任务', exact: true }).click()
  await expect(daily).toContainText('今日目标 2 题')
  await expect(daily.locator('.daily-problem')).toHaveCount(2)
  await page.screenshot({ path: 'test-results/daily-plan.png', fullPage: true })
  const task = (await (await page.request.get('/api/daily')).json()).plan.tasks[0]
  const state = await (
    await page.request.get(`/api/workbench/problem/${encodeURIComponent(task.id)}`)
  ).json()
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

test('daily specialist preferences allow a custom count and keep today locked after switching back to random', async ({
  page,
}) => {
  const saved: any[] = []
  let settings = {
    daily_goal: 4,
    duration_days: 14,
    selection_type: 'random',
    topic_id: null as string | null,
    training_mode: 'learn',
  }
  let plan: any = null
  const state = () => ({
    settings,
    active: true,
    locked_goal: plan?.goal_count || settings.daily_goal,
    locked_options: plan?.training_options || settings,
    plan,
    checkin: {},
    rerolls_remaining: 12,
  })
  await page.route('**/api/daily', (route) => route.fulfill({ json: state() }))
  await page.route('**/api/daily/settings', (route) => {
    settings = route.request().postDataJSON()
    saved.push(settings)
    return route.fulfill({ json: state() })
  })
  await page.route('**/api/training/topics', (route) =>
    route.fulfill({
      json: {
        groups: [
          {
            id: 'structures',
            title: '数据结构',
            topics: [{ id: 'luogu-47', title: '并查集', path: ['数据结构', '并查集'] }],
          },
        ],
      },
    }),
  )
  await page.route('**/api/ai/models?provider=api', (route) =>
    route.fulfill({
      json: {
        items: [{ id: 'daily-topic-model', name: '合成模型' }],
        default_model: 'daily-topic-model',
      },
    }),
  )
  await page.route('**/api/daily/roll', (route) => {
    expect(route.request().postDataJSON()).toEqual({ provider: 'api', model: 'daily-topic-model' })
    plan = {
      status: 'ready',
      goal_count: settings.daily_goal,
      training_options: { ...settings },
      completed_ids: [],
      tasks: Array.from({ length: settings.daily_goal }, (_, i) => ({
        task_id: i + 1,
        id: `luogu:P${10000 + i}`,
        problem_id: `P${10000 + i}`,
        title: `合成专项 ${i + 1}`,
        platform: 'luogu',
        completed: false,
      })),
    }
    return route.fulfill({ json: state() })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '训练', exact: true }).click()
  const daily = page.locator('.daily-mission')
  await daily.getByLabel('每日训练方式').selectOption('specialist')
  await daily.getByLabel('训练专题', { exact: true }).selectOption('luogu-47')
  await daily.getByLabel('训练难度', { exact: true }).selectOption('variation')
  await daily.getByLabel('每日题量', { exact: true }).fill('12')
  await daily.getByRole('button', { name: '保存每日任务', exact: true }).click()
  expect(saved[0]).toEqual({
    daily_goal: 12,
    duration_days: 14,
    selection_type: 'specialist',
    topic_id: 'luogu-47',
    training_mode: 'variation',
  })
  await daily.getByRole('button', { name: '读取可用模型', exact: true }).click()
  await daily.getByLabel('选择模型', { exact: true }).selectOption('daily-topic-model')
  await daily.getByRole('button', { name: 'AI 生成今日 12 道题', exact: true }).click()
  await expect(daily.locator('.daily-problem')).toHaveCount(12)
  await expect(daily).toContainText('专项训练 · 并查集 · 变式')
  await daily.getByLabel('每日训练方式').selectOption('random')
  await daily.getByLabel('每日题量', { exact: true }).fill('3')
  await daily.getByRole('button', { name: '保存每日任务', exact: true }).click()
  await page.reload()
  await page.getByRole('button', { name: '训练', exact: true }).click()
  await expect(daily.getByLabel('每日训练方式')).toHaveValue('random')
  await expect(daily.locator('.daily-problem')).toHaveCount(12)
  await expect(daily).toContainText('专项训练 · 并查集 · 变式')
  await expect(daily.getByRole('checkbox')).toHaveCount(0)
})

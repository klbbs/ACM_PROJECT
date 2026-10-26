import { test, expect, type Locator, type Page } from '@playwright/test'

// All specialist plans, official-source evidence and AI answers below are synthetic
// browser fixtures. These checks do not establish real OJ submission or AI success.
const topics = {
  groups: [
    {
      id: 'basic',
      title: '基础算法',
      topics: [
        {
          id: 'binary-search',
          title: '二分查找',
          description: '从边界模板开始',
          source_url: 'https://oi-wiki.org/basic/binary/',
        },
      ],
    },
    {
      id: 'graph',
      title: '图论',
      topics: [
        {
          id: 'shortest-path',
          title: '最短路',
          description: '先掌握最短路模板，再组合建模',
          source_url: 'https://oi-wiki.org/graph/shortest-path/',
        },
      ],
    },
  ],
}
const savedPlan = {
  id: 'synthetic-shortest-path',
  topic_id: 'shortest-path',
  topic_title: '最短路',
  title: '隔离专项路线：最短路',
  created_at: 1792850000,
  items: [
    {
      position: 0,
      problem_id: 'codeforces:4-A',
      title: '隔离模板题',
      platform: 'codeforces',
      url: 'https://codeforces.com/problemset/problem/4/A',
      difficulty: 800,
      difficulty_label: 'CF 800',
      stage: 'template',
      reason: '先练习模板与输入输出边界',
      completed: true,
    },
    {
      position: 1,
      problem_id: 'codeforces:7-A',
      title: '隔离建模题',
      platform: 'codeforces',
      url: 'https://codeforces.com/problemset/problem/7/A',
      difficulty: 1000,
      difficulty_label: 'CF 1000',
      stage: 'application',
      reason: '尝试将题意转换为图模型',
      completed: true,
    },
    {
      position: 2,
      problem_id: 'codeforces:8-A',
      title: '隔离挑战题',
      platform: 'codeforces',
      url: 'https://codeforces.com/problemset/problem/8/A',
      difficulty: 1200,
      difficulty_label: 'CF 1200',
      stage: 'challenge',
      reason: '结合状态设计解决变式',
      completed: false,
    },
  ],
}
const sources = [
  {
    attempt_id: 'synthetic-attempt-1',
    submission_id: '920001',
    account: 'fixture_owner',
    platform: 'codeforces',
    language: 'GNU C++20',
    verdict: 'AC',
    source_available: true,
    can_fetch_source: false,
    disabled_reason: '',
  },
  {
    attempt_id: 'synthetic-attempt-2',
    submission_id: '920002',
    account: 'fixture_owner',
    platform: 'codeforces',
    language: 'Python 3',
    verdict: 'AC',
    source_available: true,
    can_fetch_source: false,
    disabled_reason: '',
  },
]
const assessment = {
  result: {
    summary: '**边界处理值得改进**，本次复杂度符合要求。',
    dimensions: [
      {
        category: 'boundaries',
        finding: '应单独核对最小输入',
        evidence: '所选提交中初始化分支依赖 n > 1',
        suggestion: '检查 n=1 的状态初始化',
        confidence: 'medium',
      },
    ],
    strengths: ['变量职责明确'],
    next_steps: ['练习相同算法的边界变式'],
    overall_confidence: 'medium',
  },
  provenance: { synthetic: true },
  fingerprint: 'synthetic-fingerprint',
  analyzed_at: 1792850000,
  submission: sources[1],
}
async function install(page: Page, initiallySaved = true) {
  const state = {
    plans: initiallySaved ? [savedPlan] : ([] as (typeof savedPlan)[]),
    generation: [] as any[],
    job: null as any,
    requestKey: '',
    autoComplete: true,
    assessments: [] as any[],
    chats: [] as any[],
    failGeneration: false,
    delayAssessment: null as null | Promise<void>,
    reviews: new Map<number, any>(),
  }
  await page.route('**/api/ai/settings', (route) =>
    route.fulfill({
      json: {
        channel: 'api',
        has_key: true,
        codex_enabled: false,
        model: 'synthetic-coach',
        ready: true,
      },
    }),
  )
  await page.route('**/api/training/topics', (route) => route.fulfill({ json: topics }))
  await page.route('**/api/training/generation*', (route) => {
    const key = new URL(route.request().url()).searchParams.get('request_key')
    return route.fulfill({ json: { job: key && key !== state.requestKey ? null : state.job } })
  })
  await page.route('**/api/training/plans**', async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname
    if (path === '/api/training/plans/generate') {
      if (request.method() === 'POST') {
        state.generation.push(request.postDataJSON())
        state.requestKey = request.postDataJSON().request_key
        if (state.failGeneration) {
          state.job = {
            id: 'failed-background',
            kind: 'specialist_generate',
            status: 'failed',
            error: '隔离模拟 AI 无法生成有效递进题单',
            result: { stage: 'reading' },
          }
          return route.fulfill({ json: state.job })
        }
        const body = request.postDataJSON()
        const generated = {
          ...savedPlan,
          mode: body.mode,
          rating_request:
            body.cf_minimum != null
              ? { minimum: body.cf_minimum, maximum: body.cf_maximum }
              : undefined,
          items: Array.from({ length: body.count }, (_, position) => ({
            ...savedPlan.items[Math.min(position, savedPlan.items.length - 1)],
            position,
          })),
        }
        state.job = {
          id: 'synthetic-background',
          kind: 'specialist_generate',
          status: state.autoComplete ? 'completed' : 'running',
          result: {
            stage: state.autoComplete ? 'completed' : 'reading',
            requested_count: body.count,
            actual_count: state.autoComplete ? body.count : 1,
            ...(state.autoComplete ? { plan: generated } : {}),
          },
        }
        if (state.autoComplete) state.plans = [generated]
        return route.fulfill({ json: state.job })
      }
    }
    if (path === '/api/training/plans') return route.fulfill({ json: { plans: state.plans } })
    const match = path.match(/\/items\/(\d+)\/(assessment|chat)$/)
    if (!match) return route.fulfill({ json: savedPlan })
    const position = Number(match[1])
    const current = () =>
      state.reviews.get(position) || {
        completed: position < 2,
        sources,
        assessment: null,
        messages: [],
      }
    if (match[2] === 'assessment' && request.method() === 'POST') {
      state.assessments.push({ position, ...request.postDataJSON() })
      if (state.delayAssessment) await state.delayAssessment
      const next = { ...current(), assessment }
      state.reviews.set(position, next)
      return route.fulfill({ json: next })
    }
    if (match[2] === 'chat') {
      const body = request.postDataJSON()
      state.chats.push({ position, ...body })
      const next = {
        ...current(),
        messages: [
          ...current().messages,
          { role: 'user', content: body.message },
          {
            role: 'assistant',
            content: '**另一种思路**：比较稠密图和稀疏图的不同实现，先判断数据规模。',
          },
        ],
      }
      state.reviews.set(position, next)
      return route.fulfill({ json: next })
    }
    return route.fulfill({ json: current() })
  })
  return state
}
async function openTraining(page: Page) {
  if ((page.viewportSize()?.width || 1600) < 1000)
    await page.getByRole('button', { name: '打开菜单', exact: true }).click()
  await page.getByRole('button', { name: '训练', exact: true }).click()
  await page.getByRole('tab', { name: '专项训练', exact: true }).click()
  await expect(page.getByRole('heading', { name: '专项训练', exact: true })).toBeVisible()
}
async function expectSavedAi(page: Page) {
  const panel = page.locator('.specialist-training')
  await expect(panel.getByRole('status', { name: '当前 AI 配置' })).toContainText('synthetic-coach')
  await expect(panel.getByLabel('AI 通道')).toHaveCount(0)
  await expect(panel.getByLabel('选择模型', { exact: true })).toHaveCount(0)
  await expect(panel.getByRole('button', { name: '读取可用模型', exact: true })).toHaveCount(0)
}
const generateAction = (panel: Locator) => panel.locator('.specialist-generate-action')

test.beforeEach(async ({ page }) => {
  expect((await page.request.post('/_fixture/reset-workbench')).ok()).toBe(true)
})

test('training hub generates an explicit progressive plan, opens its first problem and restores it after reload', async ({
  page,
}) => {
  const state = await install(page, false)
  await page.goto('/')
  await page.getByRole('button', { name: '训练', exact: true }).click()
  await expect(page.getByRole('tab', { name: '每日训练', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await page.getByRole('tab', { name: '专项训练', exact: true }).click()
  const panel = page.locator('.specialist-training')
  await expect(panel.getByText('你的第一条专项训练路线', { exact: true })).toBeVisible()
  await panel.getByLabel('专题分类', { exact: true }).selectOption('graph')
  await panel.getByLabel('训练专题', { exact: true }).selectOption('shortest-path')
  await panel.getByLabel('专项题量', { exact: true }).fill('12')
  await panel.getByLabel('专项 CF rating 下限').fill('1000')
  await panel.getByLabel('专项 CF rating 上限').fill('2000')
  await expect(panel.getByRole('checkbox')).toHaveCount(0)
  await expect(panel.locator('.specialist-scope')).toContainText('你已验证登录的所有平台')
  await expect(panel.locator('.specialist-scope')).toContainText('检测登录状态')
  await expectSavedAi(page)
  await expect(panel.getByRole('radio')).toHaveCount(4)
  await panel.getByRole('radio', { name: /巩固/ }).check()
  await panel.getByRole('radio', { name: /新学/ }).check()
  await expect(panel.locator('button.primary')).toHaveCount(1)
  await expect(generateAction(panel)).toHaveClass(/button primary/)
  await expect(generateAction(panel)).toBeEnabled()
  await expect(generateAction(panel)).toHaveText('AI 生成「最短路」12 道题单')
  await expect(panel.locator('.specialist-generate-state')).toContainText('按「新学」生成 12 道题')
  expect(
    ((await generateAction(panel).getAttribute('aria-describedby')) || '').split(' '),
  ).toHaveLength(2)
  await generateAction(panel).click()
  await expect(panel.getByRole('heading', { name: savedPlan.title, exact: true })).toBeVisible()
  expect(state.generation).toEqual([
    {
      topic_id: 'shortest-path',
      count: 12,
      mode: 'learn',
      provider: 'api',
      model: 'synthetic-coach',
      cf_minimum: 1000,
      cf_maximum: 2000,
      request_key: expect.stringMatching(/^[a-f0-9]{32}$/),
    },
  ])
  await expect(panel.locator('.specialist-step').first()).toContainText('模板入门')
  await expect(panel.locator('.specialist-step')).toHaveCount(12)
  await page.screenshot({ path: 'test-results/specialist-training-desktop.png', fullPage: true })
  const resolved = page.waitForResponse((response) => {
    const url = new URL(response.url())
    return (
      url.pathname === '/api/workbench/resolve' &&
      url.searchParams.get('problem') === 'codeforces:4-A'
    )
  })
  await panel
    .locator('.specialist-step')
    .first()
    .getByRole('button', { name: '前往工作台', exact: true })
    .click()
  await expect(page.locator('.wb-statement')).toBeVisible()
  expect((await (await resolved).json()).id).toBe('codeforces:4-A')
  await openTraining(page)
  await expect(panel.getByRole('heading', { name: savedPlan.title, exact: true })).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: '训练', exact: true }).click()
  await expect(page.getByRole('tab', { name: '专项训练', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await expect(panel.getByRole('heading', { name: savedPlan.title, exact: true })).toBeVisible()
  expect(state.generation).toHaveLength(1)
})

test('a selected official AC submission is evaluated by an explicit click and alternatives appear as a Markdown conversation', async ({
  page,
}) => {
  const state = await install(page)
  await page.goto('/')
  await openTraining(page)
  await expectSavedAi(page)
  const first = page.locator('.specialist-step').first()
  await first.getByRole('button', { name: '提交评估与讨论', exact: true }).click()
  const review = first.locator('.specialist-review')
  await review
    .getByLabel('选择本题提交', { exact: true })
    .selectOption({ label: 'Codeforces · fixture_owner · #920002 · AC · Python 3' })
  await expect(review.getByRole('checkbox')).toHaveCount(0)
  expect(state.assessments).toHaveLength(0)
  await review.getByRole('button', { name: '递交 AI 评估', exact: true }).click()
  await expect(review.locator('.specialist-assessment strong').first()).toHaveText(
    '边界处理值得改进',
  )
  await expect(review).toContainText('评估对应提交 #920002')
  expect(state.assessments).toEqual([
    {
      position: 0,
      attempt_id: 'synthetic-attempt-2',
      provider: 'api',
      model: 'synthetic-coach',
    },
  ])
  expect(state.assessments[0]).not.toHaveProperty('code')
  await review.getByRole('button', { name: '探讨其他思路', exact: true }).click()
  await expect(review.locator('.specialist-message.assistant strong').last()).toHaveText(
    '另一种思路',
  )
  expect(state.chats).toHaveLength(1)
  expect(state.chats[0].message).toContain('不同的解法或思路')
  await review.getByLabel('追问 AI 教练', { exact: true }).fill('请说明我该检查哪些最小边界。')
  await review.getByRole('button', { name: '发送追问', exact: true }).click()
  await expect(review.locator('.specialist-message.user').last()).toContainText(
    '请说明我该检查哪些最小边界。',
  )
  expect(state.chats[1]).toMatchObject({ position: 0, model: 'synthetic-coach' })
})

test('failed generation retains saved training and late item results cannot leak to another item on mobile', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 950 })
  const state = await install(page)
  await page.goto('/')
  await openTraining(page)
  await expectSavedAi(page)
  const panel = page.locator('.specialist-training')
  const action = generateAction(panel)
  await expect(action).toBeVisible()
  expect(
    await action.evaluate(
      (element) =>
        Math.round(element.getBoundingClientRect().width) ===
        Math.round((element.parentElement as HTMLElement).getBoundingClientRect().width),
    ),
  ).toBe(true)
  state.failGeneration = true
  await action.click()
  await expect(panel.getByRole('alert')).toContainText('隔离模拟 AI 无法生成有效递进题单')
  await expect(panel).toContainText('失败不会自动重试收费')
  await expect(panel.getByRole('heading', { name: savedPlan.title, exact: true })).toBeVisible()
  const first = panel.locator('.specialist-step').nth(0),
    second = panel.locator('.specialist-step').nth(1)
  await first.getByRole('button', { name: '提交评估与讨论', exact: true }).click()
  let release!: () => void
  state.delayAssessment = new Promise<void>((done) => {
    release = done
  })
  try {
    await first.getByRole('button', { name: '递交 AI 评估', exact: true }).click()
    await expect.poll(() => state.assessments.length).toBe(1)
    await second.getByRole('button', { name: '提交评估与讨论', exact: true }).click()
    await expect(second.locator('.specialist-review')).toBeVisible()
    release()
    await expect(second.getByRole('button', { name: '递交 AI 评估', exact: true })).toBeVisible()
    await expect(second.locator('.specialist-assessment')).toHaveCount(0)
    await expect(panel.getByText('边界处理值得改进', { exact: true })).toHaveCount(0)
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true)
    await page.screenshot({ path: 'test-results/specialist-training-mobile.png', fullPage: true })
  } finally {
    release()
  }
})

test('slow background reading survives navigation and reload and polls without generating again', async ({
  page,
}) => {
  const state = await install(page)
  state.autoComplete = false
  await page.goto('/')
  await openTraining(page)
  const panel = page.locator('.specialist-training')
  await generateAction(panel).click()
  await expect(panel.locator('.specialist-generation-progress')).toContainText(
    'AI 正在读取题面并整理题目报告',
  )
  await expect(panel.locator('.specialist-generation-progress')).toContainText(
    '已完成阅读 1 / 4 道',
  )
  await expect(panel.getByRole('heading', { name: savedPlan.title, exact: true })).toBeVisible()
  await expect(generateAction(panel)).toBeDisabled()
  await page.getByRole('tab', { name: '每日训练', exact: true }).click()
  await page.getByRole('tab', { name: '专项训练', exact: true }).click()
  await expect(panel.locator('.specialist-generation-progress')).toContainText(
    '已完成阅读 1 / 4 道',
  )
  await page.reload()
  await openTraining(page)
  await expect(panel.locator('.specialist-generation-progress')).toContainText(
    '已完成阅读 1 / 4 道',
  )
  const next = { ...savedPlan, id: 'slow-finished', title: '隔离慢模型后台题单' }
  state.plans = [next, savedPlan]
  state.job = {
    ...state.job,
    status: 'completed',
    result: { stage: 'completed', requested_count: 4, actual_count: 4, plan: next },
  }
  await expect(panel.getByRole('heading', { name: next.title, exact: true })).toBeVisible({
    timeout: 7000,
  })
  await expect(generateAction(panel)).toBeEnabled()
  expect(state.generation).toHaveLength(1)
  expect(state.generation[0].request_key).toMatch(/^[a-f0-9]{32}$/)
})

test('an interrupted POST recovers its own request key after reload and never accepts an older completed plan', async ({
  page,
}) => {
  const state = await install(page)
  state.requestKey = 'a'.repeat(32)
  state.job = {
    id: 'older',
    kind: 'specialist_generate',
    status: 'completed',
    result: { plan: savedPlan },
  }
  await page.route('**/api/training/plans/generate', async (route) => {
    state.generation.push(route.request().postDataJSON())
    await route.abort('failed')
  })
  await page.goto('/')
  await openTraining(page)
  const panel = page.locator('.specialist-training')
  await generateAction(panel).click()
  await expect(panel.getByRole('alert')).toContainText('本次生成请求尚未被后台确认')
  await expect(generateAction(panel)).toBeDisabled()
  expect(state.generation).toHaveLength(1)
  const key = state.generation[0].request_key
  expect(key).toMatch(/^[a-f0-9]{32}$/)
  expect(key).not.toBe(state.requestKey)
  await page.reload()
  await openTraining(page)
  await expect(panel.getByRole('alert')).toContainText('本次生成请求尚未被后台确认')
  await expect(generateAction(panel)).toBeDisabled()
  const next = { ...savedPlan, id: 'exact-request', title: '隔离本次请求对应题单' }
  state.requestKey = key
  state.job = {
    id: 'confirmed',
    kind: 'specialist_generate',
    status: 'completed',
    result: { plan: next },
  }
  state.plans = [next, savedPlan]
  await panel.getByRole('button', { name: '重新查询生成状态', exact: true }).click()
  await expect(panel.getByRole('heading', { name: next.title, exact: true })).toBeVisible()
  await expect(generateAction(panel)).toBeEnabled()
  expect(state.generation).toHaveLength(1)
})

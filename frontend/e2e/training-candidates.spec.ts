import { test, expect, type Page } from '@playwright/test'

// These browser fixtures exercise the real UI with synthetic candidate evidence.
// The guarded loopback server rejects external platform/AI network traffic.
const topics = {
  groups: [
    {
      id: 'graph',
      title: '图论',
      topics: [
        {
          id: 'bfs',
          title: '广度优先搜索',
          description: '图上搜索',
          source_url: 'https://oi-wiki.org/search/bfs/',
        },
        {
          id: 'dfs',
          title: '深度优先搜索',
          description: '递归搜索',
          source_url: 'https://oi-wiki.org/search/dfs/',
        },
      ],
    },
  ],
}
const evidence = (topic: string, count = 7) => ({
  topic_id: topic,
  eligible_count: count,
  template_count: 1,
  notice: '本地缓存覆盖范围有限，不代表平台全站无题。',
  platforms: [
    {
      platform: 'codeforces',
      verified: true,
      cached_count: 90,
      topic_count: 12,
      eligible_count: count,
      template_count: 1,
      unknown_topic_count: 3,
      conditions: [{ code: 'unknown_date', count: 2, message: '缺少可核查的官方发布日期' }],
      discovery: { status: 'cached', message: '复用当前专题缓存' },
    },
    {
      platform: 'luogu',
      verified: true,
      cached_count: 60,
      topic_count: 8,
      eligible_count: 0,
      template_count: 0,
      unknown_topic_count: 5,
      conditions: [],
      discovery: { status: 'failed', message: '公开专题列表暂时不可访问，旧缓存仍保留' },
    },
    {
      platform: 'atcoder',
      verified: false,
      cached_count: 12,
      topic_count: 3,
      eligible_count: 0,
      template_count: 0,
      unknown_topic_count: 0,
      conditions: [],
      discovery: { status: 'not_requested' },
    },
  ],
})
async function install(page: Page) {
  expect((await page.request.post('/_fixture/reset-workbench')).ok()).toBe(true)
  const state = {
    reads: [] as URL[],
    refreshes: [] as unknown[],
    methodChecks: [] as unknown[],
    paid: [] as string[],
    jobCreated: false,
    runningReads: 0,
    slowJob: false,
    overviewReads: 0,
    delay: undefined as Promise<void> | undefined,
  }
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname
    if (path === '/api/overview') state.overviewReads++
    if (
      request.method() === 'POST' &&
      (/\/api\/(recommend|analysis|daily\/roll)$/.test(path) ||
        /\/api\/training\/plans/.test(path) ||
        path === '/api/training/method-check' ||
        /\/(hint|assessment|chat)$/.test(path))
    )
      state.paid.push(path)
  })
  await page.route('**/api/training/topics', (route) => route.fulfill({ json: topics }))
  await page.route('**/api/training/plans', (route) => route.fulfill({ json: { plans: [] } }))
  await page.route('**/api/training/candidates?**', async (route) => {
    const url = new URL(route.request().url())
    state.reads.push(url)
    const delay = state.delay
    if (delay) await delay
    await route.fulfill({
      json: {
        ...evidence(url.searchParams.get('topic_id')!, delay ? 999 : 7),
        ...(state.methodChecks.length
          ? {
              method_check_summary: {
                checked_count: 6,
                accepted_count: 2,
                uncertain_count: 3,
                rejected_count: 1,
              },
            }
          : {}),
      },
    })
  })
  await page.route('**/api/training/method-check', async (route) => {
    state.methodChecks.push(route.request().postDataJSON())
    await route.fulfill({ json: { id: 'synthetic-method-job', status: 'queued' } })
  })
  await page.route('**/api/training/catalog/refresh', async (route) => {
    state.refreshes.push(route.request().postDataJSON())
    state.jobCreated = true
    await route.fulfill({ json: { id: 'synthetic-topic-job', status: 'queued' } })
  })
  await page.route('**/api/jobs', async (route) => {
    if (state.methodChecks.length)
      return route.fulfill({
        json: {
          items: [
            {
              id: 'synthetic-method-job',
              kind: 'topic_method_check',
              status: 'completed',
              result: {
                checked_count: 6,
                accepted_count: 2,
                uncertain_count: 3,
                rejected_count: 1,
              },
            },
          ],
        },
      })
    if (!state.jobCreated) return route.fulfill({ json: { items: [] } })
    const running = state.slowJob && ++state.runningReads <= 3
    await route.fulfill({
      json: {
        items: [
          {
            id: 'synthetic-topic-job',
            kind: 'specialist_catalog',
            status: running ? 'running' : 'partial',
            result: {
              completed_platforms: running ? 1 : 3,
              total_platforms: 3,
              platforms: [
                { platform: 'codeforces', status: 'cached', count: 12 },
                {
                  platform: 'luogu',
                  status: 'failed',
                  message: '公开专题列表暂时不可访问，旧缓存仍保留',
                },
                { platform: 'atcoder', status: 'skipped', message: '平台登录尚未验证' },
              ],
            },
          },
        ],
      },
    })
  })
  return state
}
async function specialist(page: Page) {
  await page.goto('/')
  if ((page.viewportSize()?.width || 1600) < 1000)
    await page.getByRole('button', { name: '打开菜单', exact: true }).click()
  await page.getByRole('button', { name: '训练', exact: true }).click()
  await page.getByRole('tab', { name: '专项训练', exact: true }).click()
  await expect(page.locator('.specialist-training')).toBeVisible()
  await expect(page.getByLabel('训练专题', { exact: true })).toHaveValue('bfs')
}

test('specialist cache evidence and explicit slow discovery survive the global job refresh without paid AI requests', async ({
  page,
}) => {
  const state = await install(page)
  state.slowJob = true
  await specialist(page)
  const panel = page.getByRole('region', { name: '专项候选诊断' })
  expect(state.reads).toHaveLength(0)
  expect(state.refreshes).toHaveLength(0)
  await page.getByLabel('专项 CF rating 下限').fill('1000')
  await page.getByLabel('专项 CF rating 上限').fill('1800')
  await panel.getByRole('button', { name: '查看本专题候选', exact: true }).click()
  await expect(panel).toContainText('缓存 90 题 · 专题匹配 12 题 · 可选 7 题')
  await expect(panel).toContainText('缺少可核查的官方发布日期（2 题）')
  await expect(panel).toContainText('未验证，不参与选题')
  await expect(panel).toContainText('尚未查询')
  expect(state.reads[0].searchParams.get('cf_minimum')).toBe('1000')
  expect(state.reads[0].searchParams.get('cf_maximum')).toBe('1800')
  const beforeRefresh = state.overviewReads
  await panel.getByRole('button', { name: '补抓本专题题库', exact: true }).click()
  await expect(panel.getByRole('button', { name: '正在补抓本专题…', exact: true })).toBeDisabled()
  await expect(panel).toContainText('1/3 个平台已处理')
  await expect(panel).toContainText('部分成功', { timeout: 10000 })
  await expect(panel).toContainText('公开专题列表暂时不可访问，旧缓存仍保留')
  await expect.poll(() => state.overviewReads, { timeout: 10000 }).toBeGreaterThan(beforeRefresh)
  // The app-wide /jobs observer refreshes revision after a slow job. Evidence
  // must be read again from current-user cache rather than disappear or replay AI.
  await expect(panel).toContainText('当前缓存可选 7 题')
  await expect(panel).toContainText('复用缓存')
  expect(state.reads.length).toBeGreaterThanOrEqual(3)
  expect(state.refreshes).toEqual([{ topic_id: 'bfs' }])
  expect(state.paid).toEqual([])
})

test('a late old-topic response never replaces the newly selected topic cache', async ({
  page,
}) => {
  const state = await install(page)
  await specialist(page)
  let release!: () => void
  state.delay = new Promise<void>((resolve) => {
    release = resolve
  })
  const panel = page.getByRole('region', { name: '专项候选诊断' })
  await panel.getByRole('button', { name: '查看本专题候选', exact: true }).click()
  await expect.poll(() => state.reads.length).toBe(1)
  await page.getByLabel('训练专题', { exact: true }).selectOption('dfs')
  state.delay = undefined
  await panel.getByRole('button', { name: '查看本专题候选', exact: true }).click()
  await expect(panel).toContainText('「深度优先搜索」题库与筛选说明')
  await expect(panel).toContainText('当前缓存可选 7 题')
  const oldResponse = page.waitForResponse((response) =>
    response.url().includes('/training/candidates?topic_id=bfs'),
  )
  release()
  await oldResponse
  await expect(panel).not.toContainText('999')
  expect(state.reads.map((url) => url.searchParams.get('topic_id'))).toEqual(['bfs', 'dfs'])
  expect(state.refreshes).toEqual([])
  expect(state.paid).toEqual([])
})

test('daily specialist exposes the same explicit cache and partial refresh controls on mobile', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const state = await install(page)
  await specialist(page)
  await page.getByRole('tab', { name: '每日训练', exact: true }).click()
  const daily = page.locator('.daily-mission')
  await daily.getByLabel('每日训练方式').selectOption('specialist')
  await daily.getByLabel('训练专题', { exact: true }).selectOption('bfs')
  const panel = daily.getByRole('region', { name: '专项候选诊断' })
  expect(state.reads).toHaveLength(0)
  await panel.getByRole('button', { name: '查看本专题候选', exact: true }).click()
  await expect(panel).toContainText('当前缓存可选 7 题')
  await panel.getByRole('button', { name: '补抓本专题题库', exact: true }).click()
  await expect(panel).toContainText('部分成功')
  await expect(panel).toContainText('平台登录尚未验证')
  expect(state.refreshes).toEqual([{ topic_id: 'bfs' }])
  expect(state.reads[0].searchParams.has('cf_minimum')).toBe(false)
  expect(state.paid).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
})

test('paid method verification is explicitly disclosed, sends the saved-model contract, and restores only a summary without spoilers', async ({
  page,
}) => {
  const state = await install(page)
  await specialist(page)
  const panel = page.getByRole('region', { name: '专项候选诊断' })
  await expect(panel).toContainText('读取最多 6 道候选官方题面')
  await expect(panel).toContainText('可能产生额外费用')
  await expect(panel).toContainText('不会改变 AC / 签到')
  await expect(page.locator('.specialist-ai-note')).toContainText('候选不足时')
  expect(state.methodChecks).toEqual([])
  await page.getByLabel('专项 CF rating 下限').fill('1000')
  await page.getByLabel('专项 CF rating 上限').fill('1800')
  await panel.getByRole('button', { name: 'AI 核查细类候选', exact: true }).click()
  await expect(panel).toContainText('已核查 6 题 · 方法可行 2 题 · 无法确定 3 题 · 未通过核查 1 题')
  await expect(panel).toContainText('不是官方标签、标准解法或判题结果')
  await expect(panel).toContainText('不会把 AI 核查通过当作模板')
  expect(state.methodChecks).toEqual([{ topic_id: 'bfs', cf_minimum: 1000, cf_maximum: 1800 }])
  expect(state.refreshes).toEqual([])
  expect(state.paid).toEqual(['/api/training/method-check'])
  await expect(panel.locator('pre')).toHaveCount(0)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.reload()
  await page.getByRole('button', { name: '训练', exact: true }).click()
  await page.getByRole('tab', { name: '专项训练', exact: true }).click()
  await expect(panel).toBeVisible()
  await panel.getByRole('button', { name: '查看本专题候选', exact: true }).click()
  await expect(panel).toContainText('方法可行 2 题')
  expect(state.methodChecks).toHaveLength(1)
  expect(state.paid).toEqual(['/api/training/method-check'])
})

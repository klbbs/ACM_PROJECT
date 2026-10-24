import { test, expect } from '@playwright/test'

const eligibility = {
  candidate_count: 0,
  platforms: [
    {
      platform: 'codeforces',
      login_verified: false,
      cached_count: 5,
      total_cached_count: 5,
      eligible_count: 0,
      reasons: ['登录尚未通过检测：到连接与设置点击“检测登录状态”'],
      notice: '需要检测登录状态',
    },
    {
      platform: 'atcoder',
      login_verified: true,
      cached_count: 4,
      total_cached_count: 4,
      eligible_count: 0,
      reasons: ['4 题早于最早发布日期'],
      date_eligible_count: 0,
    },
  ],
}

test('empty-candidate sync immediately explains progress, partial results and exact next steps', async ({
  page,
}) => {
  let job: any = null
  let catalogReads = 0
  await page.route('**/api/catalog?**', (route) => {
    catalogReads++
    return route.fulfill({ json: { items: [], eligibility, source: '隔离官方候选' } })
  })
  await page.route('**/api/jobs', (route) => route.fulfill({ json: { items: job ? [job] : [] } }))
  await page.route('**/api/catalog/refresh', async (route) => {
    job = {
      id: 'synthetic-catalog',
      kind: 'catalog',
      status: 'queued',
      created_at: Math.floor(Date.now() / 1000),
      result: null,
    }
    return route.fulfill({ json: { id: job.id, status: 'queued' } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '训练', exact: true }).click()
  const panel = page.getByRole('region', { name: '题库同步进度与说明' })
  await expect(panel).toContainText('登录尚未通过检测')
  await expect(panel).toContainText('4 题早于最早发布日期')
  const empty = page.locator('.candidate-list')
  await empty.getByRole('button', { name: '同步平台题库', exact: true }).click()
  await expect(panel).toBeInViewport()
  await expect(panel.getByRole('status').first()).toContainText('同步任务已排队')
  const syncButtons = page.getByRole('button', { name: '同步平台题库', exact: true })
  await expect(syncButtons).toHaveCount(2)
  await expect(syncButtons.nth(0)).toBeDisabled()
  await expect(syncButtons.nth(1)).toBeDisabled()
  job = {
    ...job,
    status: 'running',
    result: {
      completed_platforms: 1,
      total_platforms: 4,
      platforms: [
        { platform: 'codeforces', status: 'updated', count: 5 },
        { platform: 'atcoder', status: 'running' },
      ],
    },
  }
  await expect(panel).toContainText('1 / 4 个平台已处理', { timeout: 7000 })
  job = {
    ...job,
    status: 'partial',
    updated_at: Math.floor(Date.now() / 1000),
    result: {
      completed_platforms: 4,
      total_platforms: 4,
      platforms: [
        { platform: 'codeforces', status: 'updated', count: 5, dated_count: 5 },
        { platform: 'atcoder', status: 'updated', count: 4, dated_count: 4 },
        {
          platform: 'luogu',
          status: 'failed',
          message: '官方限制访问（HTTP 403），请稍后重试；已保留旧缓存',
        },
        { platform: 'nowcoder', status: 'failed', message: '官方请求超时，请稍后重试' },
      ],
    },
  }
  await expect(panel).toContainText('部分平台同步成功', { timeout: 7000 })
  await expect(panel).toContainText('HTTP 403')
  await expect(panel).toContainText('当前筛选共 0 道可选题')
  await expect.poll(() => catalogReads).toBeGreaterThan(1)
  await panel.scrollIntoViewIfNeeded()
  await page.screenshot({ path: test.info().outputPath('catalog-sync.png') })
  await panel.getByText('“同步平台题库”怎么用？', { exact: true }).click()
  await expect(panel).toContainText('本操作不获取个人提交记录、不检测 Cookie，也不调用 AI')
  await panel.getByRole('button', { name: '检查平台登录与训练偏好', exact: true }).click()
  await expect(page.getByRole('heading', { name: '平台信息、登录检测与交题', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '训练偏好', exact: true })).toBeVisible()
})

test('rejected sync and failed candidate reads have different local errors and retry only reads candidates', async ({
  page,
}) => {
  let failRead = true,
    syncCalls = 0
  await page.route('**/api/catalog?**', (route) =>
    failRead
      ? route.fulfill({ status: 503, json: { detail: '候选条件读取暂时不可用' } })
      : route.fulfill({ json: { items: [], eligibility } }),
  )
  await page.route('**/api/catalog/refresh', (route) => {
    syncCalls++
    return route.fulfill({ status: 409, json: { detail: '当前用户已有两项任务，请等待完成' } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '训练', exact: true }).click()
  const panel = page.getByRole('region', { name: '题库同步进度与说明' })
  await expect(panel).toContainText('暂时无法读取候选条件')
  await expect(panel).not.toContainText('当前筛选共 0 道可选题')
  failRead = false
  await page.getByRole('button', { name: '重新读取候选', exact: true }).click()
  await expect(panel).toContainText('登录尚未通过检测')
  expect(syncCalls).toBe(0)
  await page
    .locator('.candidate-list')
    .getByRole('button', { name: '同步平台题库', exact: true })
    .click()
  await expect(panel).toContainText('同步请求未启动')
  await expect(panel.getByRole('alert')).toContainText('当前用户已有两项任务')
  expect(syncCalls).toBe(1)
})

test('an accepted sync survives a progress read failure without submitting duplicate work', async ({
  page,
}) => {
  let failProgress = false,
    syncCalls = 0
  const job = {
    id: 'accepted-catalog',
    kind: 'catalog',
    status: 'queued',
    created_at: Math.floor(Date.now() / 1000),
  }
  await page.route('**/api/catalog?**', (route) =>
    route.fulfill({ json: { items: [], eligibility } }),
  )
  await page.route('**/api/jobs', (route) =>
    failProgress
      ? route.fulfill({ status: 503, json: { detail: '任务进度暂时不可用' } })
      : route.fulfill({ json: { items: [] } }),
  )
  await page.route('**/api/catalog/refresh', (route) => {
    syncCalls++
    failProgress = true
    return route.fulfill({ json: { id: job.id, status: 'queued' } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '训练', exact: true }).click()
  await page
    .locator('.candidate-list')
    .getByRole('button', { name: '同步平台题库', exact: true })
    .click()
  const panel = page.getByRole('region', { name: '题库同步进度与说明' })
  await expect(panel).toContainText('同步任务已排队')
  await expect(page.getByText(/暂时无法读取题库同步进度：/)).toContainText('请勿重复提交同步任务')
  await expect(panel).not.toContainText('同步请求未启动')
  expect(syncCalls).toBe(1)
})

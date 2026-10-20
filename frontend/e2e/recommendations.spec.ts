import { test, expect } from '@playwright/test'

test('recommendation mixes platforms, inherits the saved date and can renew expired login checks', async ({
  page,
}) => {
  let body: any = null
  await page.route('**/api/training', (route) =>
    route.fulfill({
      json: {
        minimum: 1000,
        maximum: 1600,
        published_after: '2025-01-01',
        preferred_tags: [],
        avoid_tags: [],
        goal_rating: 2000,
        prefer_recent: true,
        min_contest: 0,
      },
    }),
  )
  await page.route('**/api/catalog?**', (route) =>
    route.fulfill({
      json: {
        items: [],
        source: '所有已验证登录平台的官方候选题',
        eligibility: {
          platforms: [
            {
              platform: 'codeforces',
              login_verified: false,
              date_eligible_count: 0,
              notice: '等待重新检测',
            },
            {
              platform: 'atcoder',
              login_verified: false,
              date_eligible_count: 0,
              notice: '等待重新检测',
            },
          ],
        },
      },
    }),
  )
  await page.route('**/api/ai/models?provider=api', (route) =>
    route.fulfill({
      json: { items: [{ id: 'synthetic', name: '隔离测试模型' }], default_model: 'synthetic' },
    }),
  )
  await page.route('**/api/recommend', (route) => {
    body = route.request().postDataJSON()
    return route.fulfill({ status: 400, json: { detail: '隔离验收已捕获选题请求，不调用真实AI' } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '今日练习', exact: true }).click()
  const panel = page.locator('.practice-setup')
  await expect(page.getByLabel('推荐平台', { exact: true })).toHaveCount(0)
  await expect(panel.getByLabel('选题发布日期下限')).toHaveValue('2025-01-01')
  await expect(panel.getByText('Codeforces · 待检测登录')).toBeVisible()
  await expect(panel.getByText('AtCoder · 待检测登录')).toBeVisible()
  await panel.getByRole('button', { name: '读取可用模型', exact: true }).click()
  await panel.getByLabel('选择模型', { exact: true }).selectOption('synthetic')
  // Empty stale GET candidates must not disable the explicit background renewal.
  await expect(panel.getByRole('button', { name: 'AI 为我选题', exact: true })).toBeEnabled()
  await panel.getByRole('button', { name: 'AI 为我选题', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('隔离验收已捕获')
  expect(body.published_after).toBe('2025-01-01')
  expect(body.platform || '').toBe('')
})

test('legacy contest restriction can be explicitly cleared and saved in training preferences', async ({
  page,
}) => {
  let payload: any = null
  let training: any = {
    minimum: 1000,
    maximum: 1600,
    published_after: null,
    min_contest: 2200,
    preferred_tags: [],
    avoid_tags: [],
    goal_rating: 2000,
    prefer_recent: true,
  }
  await page.route('**/api/training', (route) => {
    if (route.request().method() === 'PUT') {
      payload = route.request().postDataJSON()
      training = payload
    }
    return route.fulfill({ json: training })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  await page.getByRole('button', { name: '不限制发布日期（清除旧编号限制）', exact: true }).click()
  await page.getByRole('button', { name: '保存训练偏好', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('训练偏好已保存')
  expect(payload.min_contest).toBe(0)
  expect(payload.published_after).toBeNull()
})

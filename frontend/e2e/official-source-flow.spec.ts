import { test, expect } from '@playwright/test'

test('sync without code, fetch official source and review it without replacing the draft', async ({ page }) => {
  test.setTimeout(45000)
  // Product routes, background jobs and database are real; only upstream OJ and
  // AI transports are synthetic in the guarded loopback test server.
  expect((await page.request.post('/_fixture/reset-workbench')).ok()).toBe(true)
  expect((await page.request.put('/api/accounts', {
    data: { items: [{ platform: 'codeforces', handle: 'fixture_user',
      cookie: 'JSESSIONID=synthetic-e2e-session', enrich: false }] },
  })).ok()).toBe(true)
  await page.route('**/api/ai/models?provider=api', (route) => route.fulfill({
    json: { items: [{ id: 'fixture-coach', name: '合成教练模型' }], default_model: 'fixture-coach' },
  }))
  let syncBody: unknown
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/sync') && request.url().includes('/problem-submissions/'))
      syncBody = request.postDataJSON()
  })
  await page.goto('/')
  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  await page.getByLabel('平台题号或官方题目链接').fill('4-A')
  await page.locator('.wb-open').getByRole('button', { name: '打开', exact: true }).click()
  await expect(page.getByLabel('源代码', { exact: true })).toHaveText('')
  await expect(page.getByLabel('本次平台提交号')).toHaveCount(0)
  await page.getByRole('button', { name: '同步本题提交', exact: true }).click()
  const record = page.locator('article.problem-submission').filter({ hasText: '#920001' })
  await expect(record).toContainText('WA', { timeout: 15000 })
  expect(syncBody).toEqual({ handle: 'fixture_user' })
  await record.getByRole('button', { name: '获取本次代码', exact: true }).click()
  await expect(record).toContainText('代码来自官方页面')
  await expect(page.getByLabel('源代码', { exact: true })).toHaveText('')
  const state = await (await page.request.get('/api/workbench/problem/4-A')).json()
  expect(state.attempts).toHaveLength(1)
  expect(state.attempts[0].code_verified).toBe(true)
  expect(state.attempts[0].code).toBe('print("fixture_wrong")')
  await page.getByLabel('代码语言').selectOption('python3')
  const currentDraft = 'print("keep my current draft")'
  await page.getByLabel('源代码', { exact: true }).fill(currentDraft)
  await page.getByLabel('源代码', { exact: true }).press('Control+s')
  await expect(page.getByText('已保存到个人数据库', { exact: true })).toBeVisible()
  await page.getByLabel('AI 通道').selectOption('api')
  await page.getByRole('button', { name: '读取可用模型', exact: true }).click()
  await page.getByLabel('选择模型', { exact: true }).selectOption('fixture-coach')
  const review = record.getByRole('button', { name: '分析本次错因（不提供答案）', exact: true })
  await expect(review).toBeEnabled()
  await review.click()
  await expect(page.getByText(/仅针对本地快照检查边界/)).toBeVisible({ timeout: 15000 })
  await expect(page.getByLabel('源代码', { exact: true })).toHaveText(currentDraft)
  const after = await (await page.request.get('/api/workbench/problem/4-A')).json()
  expect(after.attempts[0].code).toBe('print("fixture_wrong")')
  expect(after.draft.code).toBe(currentDraft)
  expect((await (await page.request.post('/_fixture/remote-count')).json()).sent).toBe(0)
})

import { test, expect } from '@playwright/test'

// Only the guarded loopback fixture replaces the OJ transport. Queue, draft,
// immutable code records, API routes and browser interactions are the real app.
test('submit wrong then correct code, retain results and never resend on refresh', async ({ page }) => {
  expect((await page.request.post('/_fixture/reset-workbench')).ok()).toBe(true)
  expect((await page.request.put('/api/accounts', {
    data: { items: [{ platform: 'codeforces', handle: 'fixture_user', cookie: 'JSESSIONID=synthetic-e2e-session', enrich: false }] },
  })).ok()).toBe(true)
  await page.goto('/')
  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  await page.getByLabel('平台题号或官方题目链接').fill('4-A')
  await page.locator('.wb-open').getByRole('button', { name: '打开', exact: true }).click()
  await page.getByLabel('代码语言').selectOption('python3')
  const panel = page.getByRole('region', { name: '真实平台提交', exact: true })
  await page.getByLabel('源代码', { exact: true }).fill('print("fixture_wrong")')
  await panel.getByRole('button', { name: '读取平台编译器', exact: true }).click()
  await expect(panel.getByLabel('实际平台编译器')).toHaveValue('31')
  await panel.getByRole('button', { name: '提交到平台', exact: true }).click()
  await expect(panel).toContainText('WA · 请查看判题结果', { timeout: 15000 })
  await expect(page.locator('article.wb-attempt').getByText('WA', { exact: true })).toBeVisible()
  let state = await (await page.request.get('/api/workbench/problem/4-A')).json()
  expect(state.attempts).toHaveLength(1)
  expect(state.attempts[0].submission_id).toBe(930001)
  expect((await (await page.request.post('/_fixture/remote-count')).json()).sent).toBe(1)
  await page.getByLabel('源代码', { exact: true }).fill('print("fixture_correct")')
  await panel.getByRole('button', { name: '提交到平台', exact: true }).click()
  await expect(panel).toContainText('AC · 通过！', { timeout: 15000 })
  state = await (await page.request.get('/api/workbench/problem/4-A')).json()
  expect(state.attempts).toHaveLength(2)
  expect(state.attempts.map((item: { verdict: string }) => item.verdict)).toContain('AC')
  await page.reload()
  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  await page.getByLabel('平台题号或官方题目链接').fill('4-A')
  await page.locator('.wb-open').getByRole('button', { name: '打开', exact: true }).click()
  await expect(panel).toContainText('AC · 通过！')
  expect((await (await page.request.post('/_fixture/remote-count')).json()).sent).toBe(2)
  const history = await (await page.request.get('/api/submissions?limit=20')).json()
  expect(JSON.stringify(history)).toContain('930002')
})

test('lost browser receipt queries the same intent instead of sending code twice', async ({ page }) => {
  expect((await page.request.post('/_fixture/reset-workbench')).ok()).toBe(true)
  await page.request.put('/api/accounts', {
    data: { items: [{ platform: 'codeforces', handle: 'fixture_user', cookie: 'JSESSIONID=synthetic-e2e-session', enrich: false }] },
  })
  await page.goto('/')
  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  await page.getByLabel('平台题号或官方题目链接').fill('4-A')
  await page.locator('.wb-open').getByRole('button', { name: '打开', exact: true }).click()
  await page.getByLabel('代码语言').selectOption('python3')
  await page.getByLabel('源代码', { exact: true }).fill('print("fixture_wrong")')
  const panel = page.getByRole('region', { name: '真实平台提交', exact: true })
  await panel.getByRole('button', { name: '读取平台编译器', exact: true }).click()
  let lost = false
  await page.route('**/api/remote-submissions', async (route) => {
    if (route.request().method() === 'POST' && !lost) {
      lost = true
      await route.fetch()
      await route.abort('failed')
    } else await route.continue()
  })
  await panel.getByRole('button', { name: '提交到平台', exact: true }).click()
  await expect(panel.getByRole('button', { name: '查询本次提交', exact: true })).toBeEnabled()
  await panel.getByRole('button', { name: '查询本次提交', exact: true }).click()
  await expect(panel).toContainText('WA · 请查看判题结果', { timeout: 15000 })
  expect((await (await page.request.post('/_fixture/remote-count')).json()).sent).toBe(1)
})

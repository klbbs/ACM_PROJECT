import { test, expect, type Page } from '@playwright/test'

// Judge records for these tests are synthetic route responses; the app's own
// session, CSRF and workbench state are the real implementation.
async function open(page: Page, problem = '4-A') {
  await page.goto('/')
  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  await page.getByLabel('平台题号或官方题目链接').fill(problem)
  await page.locator('.wb-open').getByRole('button', { name: '打开', exact: true }).click()
  await expect(page.getByLabel('同步账号')).toBeVisible()
}

const userRow = {
  submission_id: '920001',
  account: 'fixture_user',
  verdict: 'WA',
  submitted_at: 1792640000,
  language: 'Python 3',
  url: 'https://codeforces.com/contest/4/submission/920001',
  attempt_id: null,
  has_local_code: false,
  code_verified: false,
}
const acRow = {
  submission_id: '920002',
  account: 'fixture_user',
  verdict: 'AC',
  submitted_at: 1792640600,
  language: 'GNU G++20',
  url: 'https://codeforces.com/contest/4/submission/920002',
  attempt_id: null,
  has_local_code: false,
  code_verified: false,
}
const altRow = { ...userRow, submission_id: '77777', account: 'fixture_alt', verdict: 'AC' }

test('records show verdict colours and only official https links', async ({ page }) => {
  expect((await page.request.post('/_fixture/reset-workbench')).ok()).toBe(true)
  await page.request.put('/api/accounts', {
    data: { items: [{ platform: 'codeforces', handle: 'fixture_user', enrich: false }] },
  })
  await page.route('**/api/problem-submissions/**', async (route) => {
    if (route.request().method() !== 'GET') return route.continue()
    await route.fulfill({
      json: {
        problem_id: 'codeforces:4-A',
        handles: ['fixture_user'],
        sync_status: { status: 'partial', count: 3, complete: false, coverage: '本场最近 600 条' },
        items: [
          // A hostile link and an insecure one must stay plain text.
          { ...userRow, url: 'https://evil.example/920001' },
          acRow,
          { ...userRow, submission_id: '920003', url: 'http://codeforces.com/contest/4/submission/920003' },
        ],
      },
    })
  })
  await open(page)
  const wa = page.locator('article.problem-submission').filter({ hasText: '#920001' })
  const ac = page.locator('article.problem-submission').filter({ hasText: '#920002' })
  const insecure = page.locator('article.problem-submission').filter({ hasText: '#920003' })
  await expect(wa).toHaveClass(/is-error/)
  await expect(ac).toHaveClass(/is-ac/)
  await expect(ac.getByRole('link', { name: /官方记录/ })).toHaveAttribute(
    'href',
    'https://codeforces.com/contest/4/submission/920002',
  )
  await expect(wa.getByRole('link', { name: /官方记录/ })).toHaveCount(0)
  await expect(insecure.getByRole('link', { name: /官方记录/ })).toHaveCount(0)
  await expect(page.getByText(/部分成功/)).toBeVisible()
  // Judge records are readable with an empty editor and without any source code.
  await expect(page.getByLabel('源代码', { exact: true })).toHaveText('')
  await expect(page.locator('article.problem-submission pre')).toHaveCount(0)
})

test('switching accounts reloads only that account and a running sync is never duplicated', async ({
  page,
}) => {
  expect((await page.request.post('/_fixture/reset-workbench')).ok()).toBe(true)
  await page.request.put('/api/accounts', {
    data: {
      items: [
        { platform: 'codeforces', handle: 'fixture_user', enrich: false },
        { platform: 'codeforces', handle: 'fixture_alt', enrich: false },
      ],
    },
  })
  let jobStatus = ''
  let syncCalls = 0
  await page.route('**/api/jobs', (route) =>
    route.fulfill({
      json: {
        items: jobStatus
          ? [
              {
                id: 'sync-fixture',
                kind: 'wb_problem_submissions',
                status: jobStatus,
                result:
                  jobStatus === 'completed'
                    ? { count: 1, complete: true, coverage: '本场最近 600 条' }
                    : null,
              },
            ]
          : [],
      },
    }),
  )
  await page.route('**/api/problem-submissions/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/sync')) {
      syncCalls++
      jobStatus = 'running'
      await new Promise((resolve) => setTimeout(resolve, 1200))
      jobStatus = 'completed'
      return route.fulfill({ json: { id: 'sync-fixture', status: 'queued' } })
    }
    const alt = url.searchParams.get('handle') === 'fixture_alt'
    await route.fulfill({
      json: {
        problem_id: 'codeforces:4-A',
        handles: ['fixture_user', 'fixture_alt'],
        sync_status: null,
        items: alt ? [altRow] : [userRow, acRow],
      },
    })
  })
  await open(page)
  await expect(page.locator('article.problem-submission')).toHaveCount(2)
  await page.getByLabel('同步账号').selectOption('fixture_alt')
  await expect(page.locator('article.problem-submission')).toHaveCount(1)
  await expect(page.locator('article.problem-submission')).toContainText('#77777')
  await page.getByRole('button', { name: '同步本题提交', exact: true }).click()
  await expect(page.getByRole('button', { name: '正在同步…', exact: true })).toBeDisabled()
  // A disabled button cannot start a second synchronization, even from the DOM.
  await page
    .getByRole('button', { name: '正在同步…', exact: true })
    .evaluate((element) => (element as HTMLButtonElement).click())
  await expect(page.getByText('已同步 1 条本题提交')).toBeVisible({ timeout: 15000 })
  expect(syncCalls).toBe(1)
})

test('a sync that finds nothing explains the range without hiding old records', async ({
  page,
}) => {
  expect((await page.request.post('/_fixture/reset-workbench')).ok()).toBe(true)
  await page.request.put('/api/accounts', {
    data: { items: [{ platform: 'codeforces', handle: 'fixture_user', enrich: false }] },
  })
  let jobStatus = ''
  await page.route('**/api/jobs', (route) =>
    route.fulfill({
      json: {
        items: jobStatus
          ? [
              {
                id: 'sync-empty',
                kind: 'wb_problem_submissions',
                status: jobStatus,
                result:
                  jobStatus === 'completed'
                    ? {
                        count: 0,
                        complete: true,
                        coverage: 'AtCoder Problems 最近 7 天、最多 3 页（第三方同步可能延迟）',
                        message: '在本次查询范围内没有找到本题提交；已保留历史记录',
                      }
                    : null,
              },
            ]
          : [],
      },
    }),
  )
  await page.route('**/api/problem-submissions/**', async (route) => {
    if (new URL(route.request().url()).pathname.endsWith('/sync')) {
      jobStatus = 'completed'
      return route.fulfill({ json: { id: 'sync-empty', status: 'queued' } })
    }
    return route.fulfill({
      json: {
        problem_id: 'codeforces:4-A',
        handles: ['fixture_user'],
        sync_status: null,
        items: [userRow],
      },
    })
  })
  await open(page)
  await page.getByRole('button', { name: '同步本题提交', exact: true }).click()
  await expect(page.getByText('已同步 0 条本题提交')).toBeVisible({ timeout: 15000 })
  await expect(page.getByText(/没有找到本题提交/)).toBeVisible()
  await expect(page.getByText(/查询范围：AtCoder Problems 最近 7 天/)).toBeVisible()
  // The earlier record is still listed after a sync that returned nothing.
  await expect(page.locator('article.problem-submission').filter({ hasText: '#920001' })).toBeVisible()
})

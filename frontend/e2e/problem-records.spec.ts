import { test, expect } from '@playwright/test'

test('an older official submission links after saving even when recommendations are unavailable', async ({ page }) => {
  expect((await page.request.post('/_fixture/reset-workbench')).ok()).toBe(true)
  await page.request.put('/api/accounts', {
    data: { items: [{ platform: 'codeforces', handle: 'fixture_user', enrich: false }] },
  })
  await page.route('**/api/workbench', (route) => route.fulfill({ status: 503, json: { detail: '合成推荐服务不可用' } }))
  await page.goto('/')
  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  await page.getByRole('textbox', { name: '平台题号或官方题目链接' }).fill('4-A')
  await page.getByRole('button', { name: '打开', exact: true }).click()
  await expect(page.getByLabel('本次提交账号')).toHaveValue('fixture_user')
  let release!: () => void
  const gate = new Promise<void>((resolve) => { release = resolve })
  let savingStarted = false
  let submissions = 0
  await page.route('**/api/workbench/problem/*/draft', async (route) => {
    if (route.request().method() === 'PUT') {
      savingStarted = true
      await gate
    }
    await route.continue()
  })
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/submission')) submissions++
  })
  const code = 'print("association while saving")'
  await page.getByLabel('代码语言').selectOption('python3')
  await page.getByLabel('源代码', { exact: true }).fill(code)
  const form = page.locator('.wb-submission-form')
  await form.getByLabel('本次平台提交号').fill('https://codeforces.com/contest/4/submission/920003')
  await form.getByRole('checkbox').check()
  try {
    await expect.poll(() => savingStarted).toBe(true)
    await form.getByRole('button', { name: '关联并核验本次提交' }).click()
    await expect(form.getByRole('button', { name: '关联并核验本次提交' })).toBeDisabled()
    expect(submissions).toBe(0)
  } finally {
    release()
  }
  await expect(page.locator('article.wb-attempt').getByText('WA', { exact: true })).toBeVisible({ timeout: 15000 })
  expect(submissions).toBe(1)
  const state = await (await page.request.get('/api/workbench/problem/4-A')).json()
  expect(state.draft.code).toBe(code)
  expect(state.attempts).toHaveLength(1)
  expect(state.attempts[0].submission_id).toBe(920003)
})

test('a note typed while the next problem is resolving stays on the original problem', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  const input = page.getByRole('textbox', { name: '平台题号或官方题目链接' })
  await input.fill('7-A')
  await page.getByRole('button', { name: '打开', exact: true }).click()
  const note = page.getByLabel('我的题目备注')
  await expect(note).toBeEnabled()
  let release!: () => void
  const gate = new Promise<void>((resolve) => { release = resolve })
  let resolving = false
  await page.route('**/api/workbench/resolve?*', async (route) => {
    resolving = true
    await gate
    await route.continue()
  })
  await input.fill('8-A')
  await page.getByRole('button', { name: '打开', exact: true }).click()
  try {
    await expect.poll(() => resolving).toBe(true)
    await note.fill('换题等待期间新增的备注')
  } finally {
    release()
  }
  await expect(page.getByRole('region', { name: '题目收藏与备注' }).getByRole('alert')).toContainText('备注尚未保存')
  await expect(note).toHaveValue('换题等待期间新增的备注')
  await expect(page.getByRole('heading', { name: '编辑器测试示例 7（合成数据）', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '放弃本次修改', exact: true }).click()
})

test('favorites and notes persist, guard unsaved edits, and reopen the problem', async ({ page }) => {
  const pid = 'codeforces:6-A'
  const endpoint = `/api/problem-records/${encodeURIComponent(pid)}`
  const old = await (await page.request.get(endpoint)).json()
  expect((await page.request.put(endpoint, {
    data: { revision: old.revision, favorite: false, note: '' },
  })).ok()).toBe(true)
  await page.goto('/')
  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  await page.getByRole('textbox', { name: '平台题号或官方题目链接' }).fill(pid)
  await page.getByRole('button', { name: '打开', exact: true }).click()
  const editor = page.getByRole('region', { name: '题目收藏与备注' })
  await editor.getByRole('button', { name: '收藏本题', exact: true }).click()
  await expect(editor.getByRole('button', { name: '已收藏', exact: true })).toHaveAttribute('aria-pressed', 'true')
  const note = '值得复习：检查边界。\n<img src=x onerror="alert(1)">'
  await editor.getByLabel('我的题目备注').fill(note)
  await page.getByRole('button', { name: '收藏与备注', exact: true }).click()
  await expect(editor.getByRole('alert')).toContainText('备注尚未保存')
  await editor.getByRole('button', { name: '保存备注', exact: true }).click()
  await expect(editor.getByRole('status')).toContainText('备注已保存')
  await page.getByRole('button', { name: '收藏与备注', exact: true }).click()
  const records = page.getByRole('region', { name: '收藏与备注记录' })
  await records.getByLabel('搜索题目记录').fill('值得复习')
  await expect(records.locator('article')).toHaveCount(1)
  await expect(records.locator('article')).toContainText('<img src=x')
  await expect(records.locator('img')).toHaveCount(0)
  await page.reload()
  await page.getByRole('button', { name: '收藏与备注', exact: true }).click()
  await records.getByLabel('搜索题目记录').fill('值得复习')
  await records.getByRole('button', { name: '去工作台', exact: true }).click()
  await expect(editor.getByLabel('我的题目备注')).toHaveValue(note)
  await editor.getByRole('button', { name: '已收藏', exact: true }).click()
  await expect(editor.getByRole('status')).toContainText('备注仍保留')
  const saved = await (await page.request.get(endpoint)).json()
  expect(saved.favorite).toBe(false)
  expect(saved.note).toBe(note)
})

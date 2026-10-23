import { test, expect } from '@playwright/test'

test('one-click synchronization needs no code and no submission id, even when recommendations are unavailable', async ({ page }) => {
  expect((await page.request.post('/_fixture/reset-workbench')).ok()).toBe(true)
  await page.request.put('/api/accounts', {
    data: { items: [{ platform: 'codeforces', handle: 'fixture_owner', enrich: false }] },
  })
  await page.route('**/api/workbench', (route) => route.fulfill({ status: 503, json: { detail: '合成推荐服务不可用' } }))
  await page.goto('/')
  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  await page.getByRole('textbox', { name: '平台题号或官方题目链接' }).fill('4-A')
  await page.getByRole('button', { name: '打开', exact: true }).click()
  await expect(page.getByLabel('同步账号')).toHaveValue('fixture_owner')
  // The former manual flow is gone: no submission-id input and no code-match tick.
  await expect(page.locator('.wb-submission-form')).toHaveCount(0)
  await expect(page.getByLabel('本次平台提交号')).toHaveCount(0)
  await expect(page.getByRole('checkbox', { name: /代码对应这条官方提交/ })).toHaveCount(0)
  const syncBodies: unknown[] = []
  let submissionPosts = 0
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname
    if (request.method() === 'POST' && path.endsWith('/sync')) syncBodies.push(request.postDataJSON())
    if (request.method() === 'POST' && path.endsWith('/submission')) submissionPosts++
  })
  let release!: () => void
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  let savingStarted = false
  // Autosave is deliberately blocked: records must be readable while the draft is
  // still pending, because synchronization never sends or waits for editor code.
  await page.route('**/api/workbench/problem/*/draft', async (route) => {
    if (route.request().method() === 'PUT') {
      savingStarted = true
      await gate
    }
    await route.continue()
  })
  const code = 'print("synchronize while saving")'
  await page.getByLabel('代码语言').selectOption('python3')
  await page.getByLabel('源代码', { exact: true }).fill(code)
  try {
    await expect.poll(() => savingStarted).toBe(true)
    await page.getByRole('button', { name: '同步本题提交', exact: true }).click()
    await expect(page.getByText(/已同步 \d+ 条本题提交/)).toBeVisible({ timeout: 25000 })
    await expect(
      page.locator('article.problem-submission').filter({ hasText: '#920001' }),
    ).toContainText('WA')
    expect(syncBodies).toEqual([{ handle: 'fixture_owner' }])
    expect(submissionPosts).toBe(0)
  } finally {
    release()
  }
  // The pending autosave completes independently of the record synchronization.
  await expect
    .poll(
      async () => (await (await page.request.get('/api/workbench/problem/4-A')).json()).draft.code,
    )
    .toBe(code)
  const state = await (await page.request.get('/api/workbench/problem/4-A')).json()
  expect(state.attempts).toHaveLength(0)
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

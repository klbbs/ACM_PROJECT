import { test, expect, type Page } from '@playwright/test'

async function open(page: Page, problem = '4-A') {
  await page.goto('/')
  if ((page.viewportSize()?.width || 1600) < 1000)
    await page.getByRole('button', { name: '打开菜单', exact: true }).click()
  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  await page.getByRole('textbox', { name: 'Codeforces 题号或链接' }).fill(problem)
  await page.getByRole('button', { name: '打开', exact: true }).click()
  await expect(page.locator('.cm-content')).toBeVisible()
  await expect(page.getByLabel('编辑器键位')).toBeEnabled()
}
async function reset(page: Page) {
  await page.request.put('/api/workbench/editor-settings', {
    data: { keymap: 'standard', font_size: 14, line_wrapping: false },
  })
  for (const pid of ['4-A', '5-A']) {
    const state = await (await page.request.get(`/api/workbench/problem/${pid}`)).json()
    await page.request.put(`/api/workbench/problem/${pid}/draft`, {
      data: { code: '', language: 'cpp17', revision: state.draft.revision },
    })
  }
}
test.beforeEach(async ({ page }) => reset(page))

test('local completions, snapshot save, undo and preference updates preserve the editor', async ({
  page,
}) => {
  const outbound: string[] = []
  page.on('request', (r) => {
    if (!r.url().startsWith('http://127.0.0.1:18766')) outbound.push(r.url())
  })
  await open(page)
  const editor = page.locator('.cm-content')
  await editor.fill('vec')
  await editor.press('Control+Space')
  const candidate = page.getByRole('option').filter({ hasText: /^vector/ })
  await expect(candidate.first()).toBeVisible()
  await candidate.first().click()
  await expect(editor).toHaveText('vector')
  const marker = await editor.elementHandle()
  await page.getByLabel('编辑器字号').selectOption('18')
  await expect(page.getByLabel('编辑器字号')).toBeEnabled()
  expect(await marker!.evaluate((node) => node.isConnected)).toBe(true)
  await expect(editor).toHaveText('vector')
  await editor.press('Control+z')
  await expect(editor).toHaveText('vec')
  await editor.press('Control+Shift+Z')
  await expect(editor).toHaveText('vector')
  await editor.press('Control+s')
  await expect(page.getByRole('status').filter({ hasText: '已保存到个人数据库' })).toBeVisible()
  await page.getByRole('button', { name: '保存提交快照', exact: true }).click()
  await expect(page.locator('.wb-attempt')).toContainText('UNLINKED')
  await page.getByRole('button', { name: '编辑器全屏', exact: true }).click()
  await expect(page.locator('.wb-editor-fullscreen')).toBeVisible()
  await page.getByRole('button', { name: '退出全屏', exact: true }).click()
  expect(outbound).toEqual([])
  await page.screenshot({ path: 'test-results/editor-desktop.png', fullPage: true })
})

test('Vim insert/normal, visual delete, undo, macro, search and :w work without navigation', async ({
  page,
}) => {
  await open(page)
  await page.getByLabel('编辑器键位').selectOption('vim')
  await expect(page.locator('.cm-position')).toContainText('NORMAL')
  const editor = page.locator('.cm-content')
  await editor.click()
  await editor.press('i')
  await expect(page.locator('.cm-position')).toContainText('INSERT')
  await page.keyboard.type('alpha beta')
  await editor.press('Escape')
  await expect(page.locator('.cm-position')).toContainText('NORMAL')
  await page.keyboard.type('0vllx')
  await expect(editor).toHaveText('ha beta')
  await page.keyboard.type('u')
  await expect(editor).toHaveText('alpha beta')
  // Record a one-character deletion macro in register a and replay it.
  await page.keyboard.type('0qaxq@a')
  await expect(editor).toHaveText('pha beta')
  await page.keyboard.type('/beta')
  await page.keyboard.press('Enter')
  await page.keyboard.type(':w')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('status').filter({ hasText: '已保存到个人数据库' })).toBeVisible()
  await page.keyboard.type(':wq')
  await page.keyboard.press('Enter')
  await expect(page.getByText('请使用 :w 保存；:q、:wq、:x 不会退出网站')).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  await expect(page.getByLabel('编辑器键位')).toHaveCount(0) // no problem selected after reload
  await page.getByRole('textbox', { name: 'Codeforces 题号或链接' }).fill('4-A')
  await page.getByRole('button', { name: '打开', exact: true }).click()
  await expect(page.getByLabel('编辑器键位')).toHaveValue('vim')
  await expect(page.locator('.cm-position')).toContainText('NORMAL')
})

test('narrow layout, language changes, fallback and restoration preserve the draft', async ({
  page,
}) => {
  await page.setViewportSize({ width: 720, height: 1000 })
  await open(page)
  const editor = page.locator('.cm-content')
  await editor.fill('// my draft')
  await editor.press('Control+s')
  await expect(page.getByRole('status').filter({ hasText: '已保存到个人数据库' })).toBeVisible()
  for (const language of ['cpp20', 'python3', 'java17']) {
    await page.getByLabel('代码语言').selectOption(language)
    await expect(editor).toHaveText('// my draft')
  }
  await page.getByRole('button', { name: '切换基础编辑器' }).click()
  await expect(page.getByRole('textbox', { name: '源代码', exact: true })).toHaveValue(
    '// my draft',
  )
  await page.getByRole('textbox', { name: '源代码', exact: true }).fill('fallback code')
  await page.getByRole('textbox', { name: '源代码', exact: true }).press('Control+s')
  await expect(page.getByRole('status').filter({ hasText: '已保存到个人数据库' })).toBeVisible()
  await page.getByRole('button', { name: '返回增强编辑器' }).click()
  await expect(page.locator('.cm-content')).toHaveText('fallback code')
  await page.screenshot({ path: 'test-results/editor-mobile.png', fullPage: true })
})

test('chunk load failure exposes a fallback with the existing code', async ({ page }) => {
  const state = await (await page.request.get('/api/workbench/problem/4-A')).json()
  await page.request.put('/api/workbench/problem/4-A/draft', {
    data: { code: 'preserved code', language: 'cpp17', revision: state.draft.revision },
  })
  await page.route('**/assets/code-editor-*.js', (route) => route.abort())
  await page.goto('/')
  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  await page.getByRole('textbox', { name: 'Codeforces 题号或链接' }).fill('4-A')
  await page.getByRole('button', { name: '打开', exact: true }).click()
  await page.getByRole('button', { name: '启用基础文本框' }).click()
  await expect(page.getByRole('textbox', { name: '源代码', exact: true })).toHaveValue(
    'preserved code',
  )
})

test('Enter accepts a keyword, Tab indents or jumps snippet fields, and replacement is local', async ({
  page,
}) => {
  await open(page)
  const editor = page.locator('.cm-content')
  await editor.fill('ret')
  await editor.press('Control+Space')
  await expect(page.getByRole('option').filter({ hasText: /^return/ })).toBeVisible()
  await editor.press('Enter')
  await expect(editor).toHaveText('return')
  await editor.fill('vec')
  await editor.press('Control+Space')
  await expect(
    page
      .getByRole('option')
      .filter({ hasText: /^vector/ })
      .first(),
  ).toBeVisible()
  await editor.press('Tab')
  await expect.poll(() => editor.textContent()).toBe('    vec')
  await editor.press('Escape')
  await editor.fill('for')
  await editor.press('Control+Space')
  await page
    .getByRole('option')
    .filter({ hasText: /^for-loop/ })
    .click()
  await expect(editor).toContainText('for (int i = 0; i < n; ++i)')
  await editor.press('Tab')
  await expect.poll(() => page.evaluate(() => window.getSelection()?.toString())).toBe('n')
  await page.keyboard.type('count')
  await editor.press('Escape')
  await editor.press('Control+h')
  const replace = page.locator('.cm-search input[name="replace"]')
  await expect(replace).toBeFocused()
  await page.locator('.cm-search input[name="search"]').fill('count')
  await replace.fill('limit')
  await page.locator('.cm-search button[name="replaceAll"]').click()
  await expect(editor).toContainText('i < limit')
})

test('conflicting autosave keeps local edits until explicit backup and server restore', async ({
  page,
}) => {
  await open(page)
  const editor = page.locator('.cm-content')
  const state = await (await page.request.get('/api/workbench/problem/4-A')).json()
  await page.request.put('/api/workbench/problem/4-A/draft', {
    data: { code: 'other tab draft', language: 'cpp17', revision: state.draft.revision },
  })
  await editor.fill('my unsaved code')
  await editor.press('Control+s')
  await expect(page.getByRole('status').filter({ hasText: '保存失败' })).toBeVisible()
  await expect(editor).toHaveText('my unsaved code')
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载备份并恢复服务器草稿' }).click()
  await download
  await expect(editor).toHaveText('other tab draft')
  await expect(page.getByRole('status').filter({ hasText: '已从服务器恢复' })).toBeVisible()
})

test('collapsible sidebars, platform tutorial and imported statement are usable', async ({
  page,
}) => {
  await open(page)
  await expect(page.locator('.sidebar-collapsed')).toHaveCount(0)
  await page.getByRole('button', { name: '收起侧栏', exact: true }).click()
  await expect(page.locator('.sidebar-collapsed')).toBeVisible()
  await page.getByRole('button', { name: '训练总览', exact: true }).click()
  await expect(page.locator('.sidebar-collapsed')).toBeVisible()
  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  await expect(page.locator('.sidebar-collapsed')).toBeVisible()
  await page.getByRole('textbox', { name: 'Codeforces 题号或链接' }).fill('4-A')
  await page.getByRole('button', { name: '打开', exact: true }).click()
  await page.getByRole('button', { name: '展开侧栏', exact: true }).click()
  await page.getByRole('button', { name: '收起选题栏', exact: true }).click()
  await expect(page.locator('.wb-list')).toBeHidden()
  await page.getByRole('button', { name: '展开选题栏', exact: true }).click()
  await expect(page.locator('.wb-list')).toBeVisible()
  await page.getByText('获取失败？导入浏览器题面 / 查看教程').click()
  await page
    .getByLabel('题面 HTML')
    .fill(
      '<div class="problem-statement"><div class="header"><div class="title">Imported demo</div></div><p>Manual statement.</p></div>',
    )
  await page.getByRole('button', { name: '导入本题文本与样例' }).click()
  await expect(page.locator('.wb-statement')).toContainText('手动导入题面')
  await page.getByRole('button', { name: '平台绑定教程', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'F12 到底复制哪一部分？' })).toBeVisible()
  await expect(page.locator('.platform-guide')).toContainText('尚未实现自动代提交')
})

test('statement renders readable math and images with usable links on failure', async ({
  page,
}) => {
  await open(page)
  await page.route('https://codeforces.com/test-diagram.png', (route) => route.abort())
  await page.getByText('获取失败？导入浏览器题面 / 查看教程').click()
  await page
    .getByLabel('题面 HTML')
    .fill(
      '<div class="problem-statement"><div class="header"><div class="title">Rendering check</div></div><p>Let $$$x_i^2$$$ be positive.</p><ul><li>Readable list</li></ul><img src="https://codeforces.com/test-diagram.png"></div>',
    )
  await page.getByRole('button', { name: '导入本题文本与样例' }).click()
  await expect(page.locator('.statement-body .katex')).toBeVisible()
  await expect(page.locator('.statement-body li')).toHaveText('Readable list')
  await expect(page.getByRole('link', { name: '打开图片原链接 ↗' })).toHaveAttribute(
    'href',
    'https://codeforces.com/test-diagram.png',
  )
  await page.getByRole('link', { name: '打开图片原链接 ↗' }).scrollIntoViewIfNeeded()
  await expect(page.getByText('图片加载失败，可能被平台限制。')).toBeVisible()
  await expect(page.locator('.statement-check summary')).toContainText('1 张图片')
})

test('AI presets expose dedicated fields and preserve secrets per endpoint', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  await page.getByLabel('模型提供方').selectOption('deepseek')
  await expect(page.getByLabel('API 地址', { exact: true })).toHaveValue('https://api.deepseek.com')
  await expect(page.getByLabel('思考模式')).toBeVisible()
  await page.getByLabel('默认模型 ID', { exact: true }).fill('test-model')
  await page.getByLabel('API Key', { exact: true }).fill('synthetic-deepseek-key')
  await page.getByRole('button', { name: '保存 API 配置' }).click()
  await expect(page.getByLabel('API Key', { exact: true })).toHaveValue('')
  await expect(page.getByText('AI API 配置已保存，密钥仅保存在服务端')).toBeVisible()
  await page.getByLabel('模型提供方').selectOption('anthropic')
  await expect(page.getByLabel('接入协议')).toHaveValue('anthropic')
  await page.getByRole('button', { name: '保存 API 配置' }).click()
  await expect(page.getByLabel('API Key', { exact: true })).toHaveAttribute(
    'placeholder',
    '输入提供方的 API Key',
  )
  await expect(page.getByRole('button', { name: '测试已保存配置（会计费）' })).toBeVisible()
  // Deliberately do not trigger a billable request: no actual provider key is used.
})

test('practice loop keeps hints, immutable snapshots, WA reviews and AC submission records', async ({
  page,
}) => {
  // All upstream judge/AI responses are synthetic in the isolated test server.
  await page.request.put('/api/accounts', {
    data: { items: [{ platform: 'codeforces', handle: 'fixture_user', enrich: false }] },
  })
  await open(page)
  await page.getByLabel('代码语言').selectOption('python3')
  const editor = page.getByLabel('源代码', { exact: true })
  await editor.fill('print("deliberately wrong")')
  await page.getByRole('button', { name: '保存草稿', exact: true }).click()
  await expect(page.getByText('已保存到个人数据库', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /给我一个小提示/ })).toBeDisabled()
  await page.getByLabel('AI 通道').selectOption('api')
  await page.getByLabel('手动模型 ID').fill('fixture-coach')
  await page.getByRole('checkbox', { name: /我同意将本题题面/ }).check()
  await page.getByRole('button', { name: /给我一个小提示/ }).click()
  await expect(page.getByText('先检查输入较小的一端。', { exact: true })).toBeVisible({
    timeout: 15000,
  })
  const beforeSnapshots = await page.locator('.wb-attempt').count()
  await page.getByRole('button', { name: '保存提交快照', exact: true }).click()
  await expect(page.locator('.wb-attempt')).toHaveCount(beforeSnapshots + 1)
  const wa = page.locator('.wb-attempt').first()
  await wa.getByLabel('平台提交号').fill('920001')
  await wa.getByRole('checkbox', { name: /本次同步得到未通过结果/ }).check()
  await wa.getByRole('button', { name: '关联并核验判题', exact: true }).click()
  await expect(wa.getByText('WA', { exact: true })).toBeVisible({ timeout: 15000 })
  await expect(wa.getByText(/仅针对本地快照检查边界/)).toBeVisible()
  await editor.fill('print("corrected practice version")')
  await page.getByRole('button', { name: '保存草稿', exact: true }).click()
  await expect(page.getByText('已保存到个人数据库', { exact: true })).toBeVisible()
  await wa.getByText('查看不可变代码快照', { exact: true }).click()
  await expect(wa.locator('.wb-snapshot')).toHaveText('print("deliberately wrong")')
  await page.getByRole('button', { name: '保存提交快照', exact: true }).click()
  await expect(page.locator('.wb-attempt')).toHaveCount(beforeSnapshots + 2)
  const ac = page.locator('.wb-attempt').first()
  await ac.getByLabel('平台提交号').fill('920002')
  await ac.getByRole('button', { name: '关联并核验判题', exact: true }).click()
  await expect(ac.getByText('AC', { exact: true })).toBeVisible({ timeout: 15000 })
  await expect(ac.getByRole('button', { name: '分析本次错因（不提供答案）' })).toHaveCount(0)
  await page.getByRole('button', { name: '提交记录', exact: true }).click()
  await expect(page.getByText('4-A · #920001', { exact: true })).toBeVisible()
  await expect(page.getByText('4-A · #920002', { exact: true })).toBeVisible()
  await page.getByLabel('筛选判题结果').selectOption('WA')
  await expect(page.getByText('4-A · #920001', { exact: true })).toBeVisible()
  await expect(page.getByText('4-A · #920002', { exact: true })).toHaveCount(0)
  await page.reload()
  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  await page.getByRole('button', { name: /^codeforces:4-A / }).click()
  await expect(page.getByText('先检查输入较小的一端。', { exact: true })).toBeVisible()
  await expect(page.getByLabel('源代码', { exact: true })).toHaveText(
    'print("corrected practice version")',
  )
  await expect(page.locator('.wb-attempt').getByText('AC', { exact: true })).toBeVisible()
  await expect(page.locator('.wb-attempt').getByText('WA', { exact: true })).toBeVisible()
  await page.request.put('/api/accounts', {
    data: { items: [{ platform: 'codeforces', handle: 'FIXTURE_USER', enrich: false }] },
  })
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  await expect(page.getByLabel('优先知识点（英文标签，逗号分隔）')).toBeVisible()
  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  await page.getByRole('button', { name: /^codeforces:4-A / }).click()
  await expect(page.getByLabel('提交账号').first()).toHaveValue('FIXTURE_USER')
})

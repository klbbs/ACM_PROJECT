import { test, expect, type Page } from '@playwright/test'

async function open(page: Page, problem = '4-A') {
  await page.goto('/')
  if ((page.viewportSize()?.width || 1600) < 1000)
    await page.getByRole('button', { name: '打开菜单', exact: true }).click()
  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  await page.getByRole('textbox', { name: '平台题号或官方题目链接' }).fill(problem)
  await page.getByRole('button', { name: '打开', exact: true }).click()
  await expect(page.locator('.cm-content')).toBeVisible()
  await expect(page.getByLabel('编辑器键位')).toBeEnabled()
}
async function reset(page: Page) {
  const restored = await page.request.post('/_fixture/reset-workbench')
  expect(restored.ok()).toBe(true)
  await page.request.put('/api/workbench/coach-settings', { data: { enabled: false } })
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

test('local completions, automatic save, undo and preference updates preserve the editor', async ({
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
  await expect(page.getByRole('button', { name: '保存提交快照', exact: true })).toHaveCount(0)
  await expect(page.locator('.wb-attempt')).toHaveCount(0)
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
  await page.getByRole('textbox', { name: '平台题号或官方题目链接' }).fill('4-A')
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
  await page.getByRole('textbox', { name: '平台题号或官方题目链接' }).fill('4-A')
  await page.getByRole('button', { name: '打开', exact: true }).click()
  await page.getByRole('button', { name: '启用基础文本框' }).click()
  await expect(page.getByRole('textbox', { name: '源代码', exact: true })).toHaveValue(
    'preserved code',
  )
})

test('Tab accepts a keyword or jumps snippet fields, and replacement is local', async ({
  page,
}) => {
  await open(page)
  const editor = page.locator('.cm-content')
  await editor.fill('ret')
  await editor.press('Control+Space')
  await expect(page.getByRole('option').filter({ hasText: /^return/ })).toBeVisible()
  await editor.press('Tab')
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
  await expect.poll(() => editor.textContent()).toBe('vector')
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

test('conflicting autosave warns and preserves local code without removed restore controls', async ({
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
  await expect(page.getByRole('button', { name: '下载备份并恢复服务器草稿' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '保存草稿', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '下载代码', exact: true })).toHaveCount(0)
  await expect(editor).toHaveText('my unsaved code')
  const server = await (await page.request.get('/api/workbench/problem/4-A')).json()
  expect(server.draft.code).toBe('other tab draft')
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
  await page.getByRole('textbox', { name: '平台题号或官方题目链接' }).fill('4-A')
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
  await page.getByRole('button', { name: '查看详细教程', exact: true }).click()
  await expect(page.getByRole('heading', { name: '平台绑定与提交教程', exact: true })).toBeVisible()
  await expect(page.locator('.platform-guide')).toContainText('本站目前不会自动代交')
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

test('Enter closes completion and inserts a newline without accepting its keyword', async ({
  page,
}) => {
  await open(page)
  const editor = page.locator('.cm-content')
  await editor.fill('ret')
  await editor.press('Control+Space')
  await expect(page.getByRole('option').filter({ hasText: /^return/ })).toBeVisible()
  await editor.press('Enter')
  await expect(editor.locator('.cm-line')).toHaveCount(2)
  await editor.press('Control+s')
  await expect
    .poll(
      async () => (await (await page.request.get('/api/workbench/problem/4-A')).json()).draft.code,
    )
    .toBe('ret\n')
  await expect(page.locator('.cm-tooltip-autocomplete')).toBeHidden()
  await editor.fill('')
  await editor.press('Tab')
  await expect.poll(() => editor.textContent()).toBe('    ')
})

test('AI presets expose dedicated fields and preserve secrets per endpoint', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  await page.getByLabel('模型提供方').selectOption('deepseek')
  await expect(page.getByLabel('API 地址', { exact: true })).toHaveValue('https://api.deepseek.com')
  await expect(page.getByLabel('思考模式')).toBeVisible()
  await page.getByText('目录未包含可用模型？手动填写模型 ID', { exact: true }).click()
  await page.getByLabel('手动模型 ID', { exact: true }).fill('test-model')
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

test('practice loop keeps hints, submitted code, WA reviews and navigable AC submission records', async ({
  page,
}) => {
  // All upstream judge/AI responses are synthetic in the isolated test server.
  await page.route('**/api/ai/models?provider=api', (route) =>
    route.fulfill({
      json: {
        items: [{ id: 'fixture-coach', name: '合成教练模型' }],
        default_model: 'fixture-coach',
        source: 'fixture',
        note: '仅用于离线教练交互验收',
      },
    }),
  )
  await page.request.put('/api/accounts', {
    data: { items: [{ platform: 'codeforces', handle: 'fixture_user', enrich: false }] },
  })
  await open(page)
  await page.getByLabel('代码语言').selectOption('python3')
  const editor = page.getByLabel('源代码', { exact: true })
  await editor.fill('print("deliberately wrong")')
  await editor.press('Control+s')
  await expect(page.getByText('已保存到个人数据库', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /给我一个小提示/ })).toBeDisabled()
  await page.getByLabel('AI 通道').selectOption('api')
  await page.getByRole('button', { name: '读取可用模型', exact: true }).click()
  await page.getByLabel('选择模型', { exact: true }).selectOption('fixture-coach')
  await page.getByRole('checkbox', { name: /我同意将本题题面/ }).check()
  await page.getByRole('button', { name: /给我一个小提示/ }).click()
  await expect(page.getByText('先检查输入较小的一端。', { exact: true })).toBeVisible({
    timeout: 15000,
  })
  const form = page.locator('.wb-submission-form')
  // A mistyped official ID should fail visibly and remain correctable.
  await form.getByLabel('本次平台提交号').fill('999999')
  await form.getByRole('checkbox').check()
  await form.getByRole('button', { name: '关联并核验本次提交' }).click()
  await expect(page.getByText(/未在该账号最近 600 条本场比赛记录中找到提交号/).first()).toBeVisible(
    { timeout: 15000 },
  )
  await expect
    .poll(
      async () =>
        (await (await page.request.get('/api/workbench/problem/4-A')).json()).attempts.length,
    )
    .toBe(0)
  await expect(editor).toHaveText('print("deliberately wrong")')
  await form.getByLabel('本次平台提交号').fill('920001')
  await form.getByRole('checkbox').check()
  await form.getByRole('button', { name: '关联并核验本次提交' }).click()
  const wa = page
    .locator('article.wb-attempt')
    .filter({ has: page.getByText('WA', { exact: true }) })
    .last()
  await expect(wa.getByText('WA', { exact: true })).toBeVisible({ timeout: 15000 })
  await wa.getByRole('button', { name: '分析本次错因（不提供答案）' }).click()
  await expect(wa.getByText(/仅针对本地快照检查边界/)).toBeVisible()
  await editor.fill('print("corrected practice version")')
  await editor.press('Control+s')
  await expect(page.getByText('已保存到个人数据库', { exact: true })).toBeVisible()
  await wa.getByText('查看当次代码（只读）', { exact: true }).click()
  await expect(wa.locator('.wb-snapshot')).toHaveText('print("deliberately wrong")')
  await form.getByLabel('本次平台提交号').fill('920002')
  await form.getByRole('checkbox').check()
  await form.getByRole('button', { name: '关联并核验本次提交' }).click()
  const ac = page
    .locator('article.wb-attempt')
    .filter({ has: page.getByText('AC', { exact: true }) })
    .last()
  await expect(ac.getByText('AC', { exact: true })).toBeVisible({ timeout: 15000 })
  await expect(ac.getByRole('button', { name: '分析本次错因（不提供答案）' })).toHaveCount(0)
  await page.getByRole('button', { name: '提交记录', exact: true }).click()
  await expect(page.getByText('4-A · #920001', { exact: true })).toBeVisible()
  await expect(page.getByText('4-A · #920002', { exact: true })).toBeVisible()
  await page.getByLabel('筛选判题结果').selectOption('WA')
  await expect(page.getByText('4-A · #920001', { exact: true })).toBeVisible()
  await expect(page.getByText('4-A · #920002', { exact: true })).toHaveCount(0)
  await page
    .getByRole('row')
    .filter({ hasText: '#920001' })
    .getByRole('button', { name: '在工作台查看' })
    .click()
  await expect(page.getByRole('heading', { name: '本题历史提交' })).toBeVisible()
  await expect(page.getByText('先检查输入较小的一端。', { exact: true })).toBeVisible()
  await expect(page.getByLabel('源代码', { exact: true })).toHaveText(
    'print("corrected practice version")',
  )
  await expect(page.locator('article.wb-attempt').getByText('AC', { exact: true })).toBeVisible()
  await expect(page.locator('article.wb-attempt').getByText('WA', { exact: true })).toBeVisible()
  await page.request.put('/api/accounts', {
    data: { items: [{ platform: 'codeforces', handle: 'FIXTURE_USER', enrich: false }] },
  })
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  await expect(page.getByLabel('优先知识点（英文标签，逗号分隔）')).toBeVisible()
  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  await page.getByRole('button', { name: /^codeforces:4-A / }).click()
  await expect(page.getByLabel('本次提交账号')).toHaveValue('FIXTURE_USER')
})

test('self-test clearly reports disabled service and never sends code', async ({ page }) => {
  let runs = 0
  await page.route('**/api/workbench/self-test/capabilities', (route) =>
    route.fulfill({
      json: {
        enabled: false,
        languages: {},
        message: '管理员尚未配置隔离自测服务。网站不会直接执行代码。',
      },
    }),
  )
  await page.route('**/api/workbench/self-test/run', (route) => {
    runs++
    return route.abort()
  })
  await open(page)
  await page.getByText('代码自测 · 自定义输入', { exact: true }).click()
  await expect(page.getByText('管理员尚未配置隔离自测服务。网站不会直接执行代码。')).toBeVisible()
  await expect(page.getByRole('button', { name: '运行自测', exact: true })).toHaveCount(0)
  expect(runs).toBe(0)
})

test('self-test uses custom input, compares output and keeps results out of submissions', async ({
  page,
}) => {
  await page.route('**/api/workbench/self-test/capabilities', (route) =>
    route.fulfill({
      json: {
        enabled: true,
        languages: { cpp17: { id: 54, name: 'C++ (GCC 9.2.0)' } },
        message: '合成隔离服务，仅用于界面验收',
      },
    }),
  )
  const sent: any[] = []
  await page.route('**/api/workbench/self-test/run', async (route) => {
    sent.push(route.request().postDataJSON())
    await route.fulfill({
      json: {
        status_id: 3,
        status: 'Accepted',
        stdout: '7\n',
        stderr: '',
        compile_output: '',
        time: '0.01',
        memory: 128,
        matched: false,
        truncated: false,
      },
    })
  })
  await open(page)
  await page.locator('.cm-content').fill('int main() { return 0; }')
  await page.getByText('代码自测 · 自定义输入', { exact: true }).click()
  await page.getByLabel('填入自测样例').selectOption('0')
  await expect(page.getByLabel('自测输入', { exact: true })).toHaveValue('1\n2')
  await page.getByLabel('自测输入', { exact: true }).fill('3 4\n')
  await page.getByLabel('自测预期输出', { exact: true }).fill('8\n')
  const run = page.getByRole('button', { name: '运行自测', exact: true })
  await expect(run).toBeDisabled()
  await page.getByLabel('我同意将本次代码和输入发送到管理员配置的隔离自测服务').check()
  await run.click()
  await expect(page.getByText('FAIL · 与预期输出不一致', { exact: true })).toBeVisible()
  expect(sent).toEqual([
    {
      code: 'int main() { return 0; }',
      language: 'cpp17',
      stdin: '3 4\n',
      expected: '8\n',
      consent: true,
    },
  ])
  await expect(page.locator('.wb-attempt')).toHaveCount(0)
  await page.locator('.cm-content').fill('int main() { return 1; }')
  await expect(page.locator('.wb-selftest-result')).toHaveCount(0)
})

test('automatic error coaching needs opt-in and gives a small hint after a verified WA', async ({
  page,
}) => {
  await page.route('**/api/ai/models?provider=api', (route) =>
    route.fulfill({
      json: {
        items: [{ id: 'fixture-coach', name: '合成教练模型' }],
        default_model: 'fixture-coach',
        source: 'fixture',
        note: '离线测试',
      },
    }),
  )
  await page.request.put('/api/accounts', {
    data: { items: [{ platform: 'codeforces', handle: 'fixture_user', enrich: false }] },
  })
  await open(page)
  const toggle = page.getByRole('checkbox', { name: '开启自动错误提示', exact: true })
  await expect(toggle).not.toBeChecked()
  await expect(toggle).toBeDisabled()
  await page.getByLabel('AI 通道').selectOption('api')
  await page.getByRole('button', { name: '读取可用模型', exact: true }).click()
  await page.getByLabel('选择模型', { exact: true }).selectOption('fixture-coach')
  await toggle.check()
  await expect(page.getByText(/已开启 · 使用 api \/ fixture-coach/)).toBeVisible()
  await page.getByLabel('代码语言').selectOption('python3')
  await page.getByLabel('源代码', { exact: true }).fill('print("automatic coach opt-in test")')
  const form = page.locator('.wb-submission-form')
  await form.getByLabel('本次平台提交号').fill('920001')
  await form.getByRole('checkbox').check()
  await form.getByRole('button', { name: '关联并核验本次提交' }).click()
  const attempt = page.locator('article.wb-attempt').last()
  await expect(attempt.getByText('WA', { exact: true })).toBeVisible({ timeout: 15000 })
  await expect(attempt.getByText(/仅针对本地快照检查边界/)).toBeVisible({ timeout: 15000 })
  // Automatic consent is separate from optional manual-hint consent; no manual call was made.
  await expect(page.getByRole('checkbox', { name: /我同意将本题题面/ })).not.toBeChecked()
  await toggle.uncheck()
  await expect(
    page.getByText('自动提示已关闭，不会自动发送代码。仍可手动获取小提示或分析错因。'),
  ).toBeVisible()
  await expect
    .poll(
      async () => (await (await page.request.get('/api/workbench/coach-settings')).json()).enabled,
    )
    .toBe(false)
})

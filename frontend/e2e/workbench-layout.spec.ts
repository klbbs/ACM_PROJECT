import { test, expect, type Page } from '@playwright/test'

async function open(page: Page) {
  await page.goto('/')
  if ((page.viewportSize()?.width || 1600) < 1000)
    await page.getByRole('button', { name: '打开菜单', exact: true }).click()
  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  await page.getByRole('textbox', { name: '平台题号或官方题目链接' }).fill('4-A')
  await page.getByRole('button', { name: '打开', exact: true }).click()
  await expect(page.locator('.cm-content')).toBeVisible()
  await expect(page.getByLabel('工作台布局', { exact: true })).toBeEnabled()
}

test.beforeEach(async ({ page }) => {
  await page.request.post('/_fixture/reset-workbench')
  await page.request.put('/api/workbench/layout-settings', {
    data: { mode: 'auto', statement_percent: 50 },
  })
  await page.request.put('/api/workbench/editor-settings', {
    data: { keymap: 'standard', font_size: 14, line_wrapping: false },
  })
  const state = await (await page.request.get('/api/workbench/problem/4-A')).json()
  await page.request.put('/api/workbench/problem/4-A/draft', {
    data: { code: '', language: 'cpp20', revision: state.draft.revision },
  })
})

test('drag, keyboard and persisted layout keep editor identity, draft and undo', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  await open(page)
  const divider = page.getByRole('separator', { name: '调整题面与编辑器宽度' })
  await expect(divider).toBeVisible()
  const editor = page.locator('.cm-content')
  await editor.fill('// layout draft')
  const node = await editor.elementHandle()
  const box = (await divider.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + 50)
  await page.mouse.down()
  await page.mouse.move(box.x + 100, box.y + 50, { steps: 8 })
  await page.mouse.up()
  await expect
    .poll(async () => Number(await divider.getAttribute('aria-valuenow')))
    .toBeGreaterThan(50)
  await divider.focus()
  const before = Number(await divider.getAttribute('aria-valuenow'))
  await divider.press('ArrowLeft')
  await expect(divider).toHaveAttribute('aria-valuenow', String(before - 2))
  await expect(page.getByText('布局按用户保存', { exact: true })).toBeVisible()
  await page.getByLabel('工作台布局', { exact: true }).selectOption('stacked')
  await expect(divider).toBeHidden()
  const statement = (await page.locator('.wb-statement').boundingBox())!
  const code = (await page.locator('.wb-editor').boundingBox())!
  expect(code.y).toBeGreaterThan(statement.y + statement.height)
  expect(await node!.evaluate((element) => element.isConnected)).toBe(true)
  await expect(editor).toHaveText('// layout draft')
  await editor.press('Control+z')
  await expect(editor).toHaveText('')
  await editor.press('Control+Shift+z')
  await expect(editor).toHaveText('// layout draft')
  await editor.press('Control+s')
  await expect(page.getByRole('status').filter({ hasText: '已保存到个人数据库' })).toBeVisible()
  await expect(page.getByText('布局按用户保存', { exact: true })).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  await page.getByRole('textbox', { name: '平台题号或官方题目链接' }).fill('4-A')
  await page.getByRole('button', { name: '打开', exact: true }).click()
  await expect(page.getByLabel('工作台布局', { exact: true })).toHaveValue('stacked')
  await expect(editor).toHaveText('// layout draft')
  const saved = await (await page.request.get('/api/workbench/layout-settings')).json()
  expect(saved).toEqual({ mode: 'stacked', statement_percent: before - 2 })
})

test('available width controls fallback and restores chosen proportions without horizontal overflow', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  await page.request.put('/api/workbench/layout-settings', {
    data: { mode: 'split', statement_percent: 60 },
  })
  await open(page)
  const divider = page.getByRole('separator', { name: '调整题面与编辑器宽度' })
  await expect(divider).toHaveAttribute('aria-valuenow', '60')
  for (const width of [1280, 1024, 720, 390]) {
    await page.setViewportSize({ width, height: 1000 })
    await expect(divider).toBeHidden()
    await expect(
      page.getByText('当前空间较窄，已自动上下排列；空间足够时恢复左右分栏。'),
    ).toBeVisible()
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true)
  }
  await page.setViewportSize({ width: 1920, height: 1080 })
  await expect(divider).toBeVisible()
  await expect(divider).toHaveAttribute('aria-valuenow', '60')
  await page.getByRole('button', { name: '重置布局', exact: true }).click()
  await expect(page.getByLabel('工作台布局', { exact: true })).toHaveValue('auto')
  await expect(divider).toHaveAttribute('aria-valuenow', '50')
})

test('layout save failure stays usable and provides a retry', async ({ page }) => {
  await open(page)
  await page.route('**/api/workbench/layout-settings', async (route) => {
    if (route.request().method() === 'PUT')
      await route.fulfill({
        status: 503,
        body: '{"detail":"unavailable"}',
        contentType: 'application/json',
      })
    else await route.continue()
  })
  await page.getByLabel('工作台布局', { exact: true }).selectOption('stacked')
  await expect(page.getByRole('alert').filter({ hasText: '布局已调整，但保存失败' })).toBeVisible()
  await expect(page.locator('.cm-content')).toBeVisible()
  await page.unroute('**/api/workbench/layout-settings')
  await page.getByRole('button', { name: '重试保存', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: '布局已调整，但保存失败' })).toHaveCount(0)
  await expect(page.getByText('布局按用户保存', { exact: true })).toBeVisible()
  expect((await (await page.request.get('/api/workbench/layout-settings')).json()).mode).toBe(
    'stacked',
  )
})

test('capture split and stacked workspace acceptance views', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  await open(page)
  await page.getByRole('button', { name: '收起选题栏', exact: true }).click()
  await page
    .locator('.cm-content')
    .fill(
      '// C++20 · layout acceptance\n#include <iostream>\nint main() {\n    int a, b;\n    std::cin >> a >> b;\n    std::cout << a + b << "\\n";\n}\n',
    )
  await page.locator('.cm-content').press('Control+s')
  await page
    .locator('.wb-reading-workspace')
    .screenshot({ path: 'test-results/workbench-split.png' })
  await page.getByLabel('工作台布局', { exact: true }).selectOption('stacked')
  await page
    .locator('.wb-reading-workspace')
    .screenshot({ path: 'test-results/workbench-stacked.png' })
})

test('slow preference saves cannot overwrite a newer layout choice', async ({ page }) => {
  await open(page)
  let release: () => void = () => {}
  const waiting = new Promise<void>((resolve) => {
    release = resolve
  })
  let writes = 0
  await page.route('**/api/workbench/layout-settings', async (route) => {
    if (route.request().method() === 'PUT' && ++writes === 1) await waiting
    await route.continue()
  })
  await page.getByLabel('工作台布局', { exact: true }).selectOption('split')
  await expect.poll(() => writes).toBe(1)
  await page.getByLabel('工作台布局', { exact: true }).selectOption('stacked')
  await expect(page.getByLabel('工作台布局', { exact: true })).toHaveValue('stacked')
  expect(writes).toBe(1)
  release()
  await expect(page.getByText('布局按用户保存', { exact: true })).toBeVisible()
  await expect.poll(() => writes).toBe(2)
  expect((await (await page.request.get('/api/workbench/layout-settings')).json()).mode).toBe(
    'stacked',
  )
})

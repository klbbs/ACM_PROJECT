import { test, expect } from '@playwright/test'

test('Cookie save, masked editing, replacement checks and explicit clearing form one flow', async ({
  page,
  request,
}) => {
  const headers = { 'X-ACM-Request': '1' }
  await request.put('/api/accounts', {
    headers,
    data: { items: [{ platform: 'codeforces', handle: 'fixture' }] },
  })
  const savedChecks: unknown[] = []
  await page.route('**/api/accounts/check', async (route) => {
    savedChecks.push(route.request().postDataJSON())
    await route.fulfill({
      json: {
        platform: 'codeforces',
        handle: 'fixture',
        status: 'authenticated',
        login_verified: true,
        checked_at: Math.floor(Date.now() / 1000),
        message: '合成平台当前身份已验证',
      },
    })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  await page.getByRole('button', { name: '配置平台账号', exact: true }).click()
  const editor = page.locator('.account-editor')
  const accounts = page.locator('#platform-accounts')
  await accounts.getByRole('button', { name: '编辑', exact: true }).click()
  await editor
    .getByLabel('Cookie（可选）', { exact: true })
    .fill('Cookie: session=synthetic...value; pref=ok')
  await editor.getByRole('button', { name: '保存平台账号', exact: true }).click()
  await expect.poll(() => savedChecks.length).toBe(1)
  await expect(accounts.getByRole('button', { name: '编辑', exact: true })).toBeEnabled()
  await accounts.getByRole('button', { name: '编辑', exact: true }).click()
  await expect(editor).toContainText('服务器已保存 Cookie（2 项）')
  await expect(editor.getByLabel('Cookie（可选）', { exact: true })).toHaveValue('')
  const box = await editor.getByLabel('Cookie（可选）', { exact: true }).boundingBox()
  const parent = await editor.boundingBox()
  expect(box!.width).toBeGreaterThan(parent!.width * 0.9)
  await editor.getByRole('button', { name: '保存平台账号', exact: true }).click()
  await expect.poll(() => savedChecks.length).toBe(2)
  await expect(accounts.getByRole('button', { name: '编辑', exact: true })).toBeEnabled()
  const data = await (await request.get('/api/accounts')).json()
  expect(data.items[0].cookie_info).toMatchObject({ status: 'valid', pair_count: 2 })
  expect(JSON.stringify(data)).not.toContain('synthetic...value')
  await accounts.getByRole('button', { name: '编辑', exact: true }).click()
  await editor.getByLabel('清除已保存 Cookie，使用公开接口').check()
  await editor.getByRole('button', { name: '保存平台账号', exact: true }).click()
  await expect(accounts.getByText('公开接口', { exact: true })).toBeVisible()
  expect((await (await request.get('/api/accounts')).json()).items[0].has_cookie).toBe(false)
  expect(savedChecks.length).toBe(2)
})

// Synthetic screenshots and responses exercise the UI; no real platform login is claimed.
const browserImage =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=='
const browserSession = {
  login_id: 'synthetic-browser',
  status: 'human_required',
  platform: 'atcoder',
  width: 1000,
  height: 720,
  expires_in: 90,
}

test('browser login reports unavailable honestly and keeps manual Cookie binding available', async ({
  page,
}) => {
  await page.route('**/api/platform-login/browser/start', async (route) => {
    expect(route.request().postDataJSON()).toEqual({
      platform: 'atcoder',
      expected_handle: 'fixture',
    })
    await route.fulfill({
      json: {
        status: 'unavailable',
        message: '合成环境没有浏览器运行时',
        official_url: 'https://unsafe.invalid/',
      },
    })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  const panel = page
    .locator('.platform-action-grid article')
    .filter({ has: page.getByRole('heading', { name: 'AtCoder', exact: true }) })
  await panel.getByLabel('绑定目标 handle / 用户名（可选）').fill('fixture')
  await panel.getByRole('button', { name: '打开安全登录窗口', exact: true }).click()
  await expect(panel.getByRole('status')).toContainText('尚未绑定账号')
  await expect(panel.getByRole('alert')).toContainText('合成环境没有浏览器运行时')
  await expect(panel.locator('img')).toHaveCount(0)
  await expect(panel.locator('a[href*="unsafe"]')).toHaveCount(0)
  await panel.getByRole('button', { name: '返回账号卡片', exact: true }).click()
  await expect(panel.getByRole('dialog')).not.toBeVisible()
  await page.getByRole('button', { name: '配置平台账号', exact: true }).click()
  await expect(page.getByLabel('Cookie（可选）', { exact: true })).toBeVisible()
})

test('mobile official browser dialog clears sent text and cancels without overflow or false success', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.route('**/api/platform-login/browser/start', (route) =>
    route.fulfill({ json: browserSession }),
  )
  await page.route('**/api/platform-login/browser/image', (route) =>
    route.fulfill({ json: { image: browserImage } }),
  )
  await page.route('**/api/platform-login/browser/input', (route) => {
    expect(route.request().postDataJSON()).toEqual({
      login_id: 'synthetic-browser',
      kind: 'text',
      text: 'synthetic-password',
    })
    return route.fulfill({ json: browserSession })
  })
  await page.route('**/api/platform-login/browser/confirm', (route) =>
    route.fulfill({ json: { ...browserSession, message: '合成页面尚未验证官方身份' } }),
  )
  await page.route('**/api/platform-login/browser/cancel', (route) =>
    route.fulfill({ json: { status: 'cancelled' } }),
  )
  await page.goto('/')
  await page.getByRole('button', { name: '打开菜单', exact: true }).click()
  await page
    .getByRole('navigation')
    .getByRole('button', { name: '连接与设置', exact: true })
    .click()
  const panel = page
    .locator('.platform-action-grid article')
    .filter({ has: page.getByRole('heading', { name: 'AtCoder', exact: true }) })
  await panel.getByRole('button', { name: '打开安全登录窗口', exact: true }).click()
  const dialog = panel.getByRole('dialog', { name: 'AtCoder 安全登录窗口', exact: true })
  await expect(dialog).toBeVisible()
  expect(await dialog.evaluate((element) => element.matches(':modal'))).toBeTruthy()
  await expect(dialog.getByRole('img')).toBeVisible()
  const input = panel.getByLabel('向选中位置输入', { exact: true })
  await expect(input).toHaveAttribute('type', 'password')
  await input.fill('synthetic-password')
  await panel.getByRole('button', { name: '发送文字', exact: true }).click()
  await expect(input).toHaveValue('')
  await panel.getByRole('button', { name: '检测登录并绑定', exact: true }).click()
  await expect(panel.getByRole('status')).toContainText('尚未验证官方身份')
  await expect(panel.getByRole('status')).not.toContainText('已绑定')
  await panel.getByRole('button', { name: '放大登录页面', exact: true }).click()
  expect(
    await panel
      .locator('.browser-login-viewport')
      .evaluate((element) => element.scrollWidth > element.clientWidth),
  ).toBeTruthy()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await panel.getByRole('button', { name: '取消安全登录', exact: true }).click()
  await expect(panel.getByRole('img')).toHaveCount(0)
  await expect(panel.getByRole('status')).toContainText('已取消')
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain('data:image/png')
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain(
    'synthetic-password',
  )
})

test('opening an official login shows a modal immediately without navigation or moving platform cards', async ({
  page,
}) => {
  let releaseStart!: () => void
  const waiting = new Promise<void>((resolve) => {
    releaseStart = resolve
  })
  let cancelCalls = 0
  await page.route('**/api/platform-login/browser/start', async (route) => {
    await waiting
    await route.fulfill({ json: browserSession })
  })
  await page.route('**/api/platform-login/browser/image', (route) =>
    route.fulfill({ json: { image: browserImage } }),
  )
  await page.route('**/api/platform-login/browser/cancel', async (route) => {
    cancelCalls++
    await route.fulfill({ json: { status: 'cancelled' } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  const panel = page.locator('.platform-action-grid article').filter({
    has: page.getByRole('heading', { name: 'AtCoder', exact: true }),
  })
  const launch = panel.getByRole('button', { name: '打开安全登录窗口', exact: true })
  await launch.scrollIntoViewIfNeeded()
  const before = await panel.boundingBox()
  const url = page.url()
  let navigations = 0
  let popups = 0
  page.on('framenavigated', (frame) => {
    if (frame === page.mainFrame()) navigations++
  })
  page.on('popup', () => {
    popups++
  })
  await launch.click()
  const dialog = panel.getByRole('dialog', { name: 'AtCoder 安全登录窗口', exact: true })
  await expect(dialog).toBeVisible()
  expect(await dialog.evaluate((element) => element.matches(':modal'))).toBeTruthy()
  await expect(dialog.getByRole('status')).toContainText('正在')
  expect(page.url()).toBe(url)
  expect(navigations).toBe(0)
  expect(popups).toBe(0)
  const opening = await panel.boundingBox()
  expect(opening!.width).toBeCloseTo(before!.width, 0)
  expect(opening!.height).toBeCloseTo(before!.height, 0)
  releaseStart()
  await expect(dialog.getByRole('img')).toBeVisible()
  const ready = await panel.boundingBox()
  expect(ready!.width).toBeCloseTo(before!.width, 0)
  expect(ready!.height).toBeCloseTo(before!.height, 0)
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeVisible()
  await expect.poll(() => cancelCalls).toBe(1)
  await expect(launch).toBeFocused()
  expect(navigations).toBe(0)
  expect(page.url()).toBe(url)
})

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

test('password attempt is explicit, clears the password and offers only official fallback links', async ({
  page,
}) => {
  await page.route('**/api/platform-login/password', async (route) => {
    const payload = route.request().postDataJSON()
    expect(payload).toEqual({
      platform: 'atcoder',
      username: 'fixture',
      password: 'synthetic-password',
      expected_handle: 'fixture',
    })
    await route.fulfill({
      json: {
        status: 'blocked',
        reason: 'browser_challenge',
        message: '合成平台要求浏览器验证',
        official_url: 'https://unsafe.invalid/',
      },
    })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  const panel = page
    .locator('.platform-action-grid article')
    .filter({ has: page.getByRole('heading', { name: 'AtCoder', exact: true }) })
  await panel.locator('summary').click()
  await panel.getByLabel('账号（用户名）', { exact: true }).fill('fixture')
  await panel.getByLabel('密码', { exact: true }).fill('synthetic-password')
  await panel.getByLabel('绑定目标用户名（可选）', { exact: true }).fill('fixture')
  await panel.getByRole('button', { name: '尝试登录并绑定' }).click()
  await expect(panel.getByRole('status')).toContainText('当前服务器尚未验证登录')
  await expect(panel.getByLabel('密码', { exact: true })).toHaveValue('')
  await expect(panel.getByRole('link', { name: '打开 AtCoder 官方登录 ↗' })).toHaveAttribute(
    'href',
    'https://atcoder.jp/login',
  )
  expect(await panel.locator('a[href*="unsafe"]').count()).toBe(0)
})

test('expanded password login stays inside a mobile viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  const notice = page.getByRole('button', { name: '已读以上更新', exact: true })
  if (await notice.count()) await notice.click()
  await page.getByRole('button', { name: '打开菜单', exact: true }).click()
  await page
    .getByRole('navigation')
    .getByRole('button', { name: '连接与设置', exact: true })
    .click()
  const panel = page
    .locator('.platform-action-grid article')
    .filter({ has: page.getByRole('heading', { name: 'AtCoder', exact: true }) })
  await panel.locator('summary').click()
  await expect(panel.getByLabel('账号（用户名）', { exact: true })).toBeVisible()
  const widths = await page.evaluate(() => ({
    viewport: innerWidth,
    document: document.documentElement.scrollWidth,
  }))
  expect(widths.document).toBeLessThanOrEqual(widths.viewport)
  const box = await panel.getByLabel('密码', { exact: true }).boundingBox()
  expect(box!.width).toBeGreaterThan(180)
  expect(box!.x + box!.width).toBeLessThanOrEqual(390)
})

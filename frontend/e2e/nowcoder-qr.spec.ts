import { test, expect } from '@playwright/test'

// Synthetic API acceptance only; this does not assert that a real WeChat user scanned.
const qr =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=='
test('Nowcoder QR explicit action, pending verification and account refresh work without an existing account', async ({
  page,
}) => {
  let verified = false
  let polls = 0
  let accountReads = 0
  await page.route('**/api/platforms', (route) =>
    route.fulfill({
      json: {
        items: [
          { platform: 'nowcoder', accounts: verified ? [{ handle: '123', has_cookie: true }] : [] },
        ],
      },
    }),
  )
  await page.route('**/api/accounts', (route) => {
    accountReads++
    return route.fulfill({
      json: { items: verified ? [{ platform: 'nowcoder', handle: '123', has_cookie: true }] : [] },
    })
  })
  await page.route('**/api/platform-login/nowcoder/start', (route) => {
    expect(route.request().postDataJSON()).toEqual({ consent: true })
    return route.fulfill({
      json: {
        login_id: 'synthetic',
        status: 'waiting',
        qr_image: qr,
        expires_at: Date.now() / 1000 + 120,
        poll_after_seconds: 3,
        method: 'wechat',
      },
    })
  })
  await page.route('**/api/platform-login/nowcoder/poll', (route) => {
    polls++
    verified = polls > 1
    return route.fulfill({
      json: {
        login_id: 'synthetic',
        status: verified ? 'verified' : 'authorizing',
        ...(verified ? { verified_uid: '123' } : {}),
      },
    })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  const panel = page.getByRole('region', { name: '牛客微信扫码登录' })
  await expect(panel).toHaveCount(1)
  await expect(panel.getByRole('button', { name: '获取微信登录二维码' })).toBeEnabled()
  await expect(panel.getByRole('checkbox')).toHaveCount(0)
  await expect(panel).toContainText('点击获取即申请本人扫码登录，验证后保存会话至当前账户')
  await panel.getByRole('button', { name: '获取微信登录二维码' }).click()
  await expect(panel.getByRole('img')).toBeVisible()
  await expect(panel.getByRole('status')).toContainText('正在验证牛客身份', { timeout: 8000 })
  await expect(panel.getByRole('status')).not.toContainText('验证通过')
  const before = accountReads
  await expect(panel.getByRole('status')).toContainText('已绑定 UID：123', { timeout: 8000 })
  await expect(panel.getByRole('img')).toHaveCount(0)
  await expect.poll(() => accountReads).toBeGreaterThan(before)
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain('data:image/png')
})

test('mobile QR panel cancels with no overflow and never reports cancellation as success', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.route('**/api/platforms', (route) =>
    route.fulfill({ json: { items: [{ platform: 'nowcoder', accounts: [] }] } }),
  )
  await page.route('**/api/platform-login/nowcoder/start', (route) =>
    route.fulfill({
      json: {
        login_id: 'synthetic',
        status: 'waiting',
        qr_image: qr,
        expires_at: Date.now() / 1000 + 120,
      },
    }),
  )
  await page.route('**/api/platform-login/nowcoder/cancel', (route) =>
    route.fulfill({ json: { login_id: 'synthetic', status: 'cancelled' } }),
  )
  await page.goto('/')
  // The compact navigation toggle is explicit on small screens.
  await page.getByRole('button', { name: '打开菜单', exact: true }).click()
  await page
    .getByRole('navigation')
    .getByRole('button', { name: '连接与设置', exact: true })
    .click()
  const panel = page.getByRole('region', { name: '牛客微信扫码登录' })
  await panel.getByRole('button', { name: '获取微信登录二维码' }).click()
  await expect(panel.getByRole('img')).toBeVisible()
  expect(await panel.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBeTruthy()
  await panel.getByRole('button', { name: '取消扫码登录' }).click()
  await expect(panel.getByRole('img')).toHaveCount(0)
  await expect(panel.getByRole('status')).toContainText('已取消')
  await expect(panel.getByRole('status')).not.toContainText('验证通过')
})

import { test, expect } from '@playwright/test'

test('merged settings expose userscript source, downloads and the existing guide shortcut', async ({
  page,
}) => {
  await page.goto('/')
  await expect(
    page.getByRole('navigation').getByRole('button', { name: '平台绑定教程', exact: true }),
  ).toHaveCount(0)
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  await page.getByRole('button', { name: '安装油猴脚本与绑定教程' }).click()
  await expect(page.getByRole('heading', { name: '油猴脚本：获取平台 Cookie' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '平台绑定与提交教程' })).toBeVisible()
  await expect(page.getByText(/公开数据可以先不填 Cookie/)).toContainText('油猴脚本')
  await expect(page.locator('.platform-guide')).toContainText('官方当前会话并核对数字 UID')
  await expect(page.getByText('Chrome、Edge、Firefox', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '② 复制 ACM Lab 脚本源码' }).click()
  await expect(page.locator('.extension-guide [role="status"]')).toContainText(
    /脚本源码已复制|浏览器未允许自动复制/,
  )
  await page.getByText('查看并手动复制脚本源码', { exact: true }).click()
  await expect(page.getByLabel('ACM Lab 油猴脚本源码')).toHaveValue(/==UserScript==/)
  await page.locator('.settings-guide').scrollIntoViewIfNeeded()
  await page.screenshot({ path: test.info().outputPath('settings.png') })
  const downloadEvent = page.waitForEvent('download')
  await page.getByRole('link', { name: '下载 .user.js 文件（备用）↓' }).click()
  const download = await downloadEvent
  expect(download.suggestedFilename()).toBe('acm-lab-cookie-helper.user.js')
  expect(await download.failure()).toBeNull()
  await page.getByRole('button', { name: '配置平台账号', exact: true }).click()
  await expect(page.getByRole('heading', { name: '算法平台连接', exact: true })).toBeInViewport()
})

test('release banner persists read state, retains history and rejects failed acknowledgments', async ({
  page,
}) => {
  // Keep this test independent of the shared fixture database's existing read state.
  let unread = ['0.14.0']
  let reject = true
  await page.route('**/api/announcements', (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id: '0.14.0',
            date: '2026-10-20',
            title: '本次测试公告',
            items: ['新的插件下载与设置教程'],
          },
        ],
        unread_ids: unread,
      },
    }),
  )
  await page.route('**/api/announcements/read', (route) => {
    if (reject) return route.fulfill({ status: 503, json: { detail: '暂时无法保存' } })
    expect(route.request().postDataJSON()).toEqual({ ids: ['0.14.0'] })
    unread = []
    return route.fulfill({ json: { ok: true, unread_ids: [] } })
  })
  await page.goto('/')
  const banner = page.getByRole('region', { name: '未读更新公告' })
  await expect(banner).toContainText('本次测试公告')
  await banner.getByRole('button', { name: '已读以上更新' }).click()
  await expect(banner.getByRole('alert')).toContainText('未读状态未清除')
  reject = false
  await banner.getByRole('button', { name: '已读以上更新' }).click()
  await expect(banner).toHaveCount(0)
  await page.reload()
  await expect(banner).toHaveCount(0)
  await page.getByRole('button', { name: '更新公告', exact: true }).click()
  await expect(page.getByRole('region', { name: '历史更新公告' })).toContainText('本次测试公告')
  await expect(page.getByRole('region', { name: '历史更新公告' })).toContainText('已读')
})

test('AtCoder verification explains browser handling and refreshes after a user completes it', async ({
  page,
}) => {
  const blocked = {
    platform: 'atcoder',
    handle: '42',
    status: 'blocked',
    reason: 'captcha',
    browser_action_required: true,
    browser_action_url: 'https://unsafe.example/challenge',
    checked_at: Math.floor(Date.now() / 1000),
    message: '平台要求完成验证',
  }
  await page.route('**/api/platforms', (route) =>
    route.fulfill({
      json: { items: [{ platform: 'atcoder', accounts: [{ handle: '42', has_cookie: true }] }] },
    }),
  )
  await page.route('**/api/accounts/checks', (route) =>
    route.fulfill({ json: { items: [blocked] } }),
  )
  await page.route('**/api/accounts/check', (route) => {
    expect(route.request().postDataJSON()).toEqual({
      platform: 'atcoder',
      handle: '42',
      force_refresh: true,
    })
    return route.fulfill({
      json: {
        ...blocked,
        status: 'authenticated',
        reason: '',
        browser_action_required: false,
        matched_account: '42',
        message: '检测账号匹配',
      },
    })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  const account = page.locator('.platform-account-check')
  await expect(account).toContainText('需要在官方完成验证')
  await expect(account.getByRole('link', { name: '打开官方平台处理验证 ↗' })).toHaveAttribute(
    'href',
    'https://atcoder.jp/login',
  )
  await expect(account).toContainText('不等于 Cookie 过期')
  await expect(account.locator('iframe')).toHaveCount(0)
  await account.scrollIntoViewIfNeeded()
  await page.screenshot({ path: test.info().outputPath('platform-verification.png') })
  await account.getByRole('button', { name: '已处理，重新检测' }).click()
  await expect(account).toContainText('已验证登录')
  await expect(account.getByRole('button', { name: '已处理，重新检测' })).toHaveCount(0)
})

test('Luogu offers public sync and official submission without unusable verification controls', async ({ page }) => {
  let loginChecks = 0
  await page.route('**/api/platforms', route => route.fulfill({ json: {
    items: [{ platform: 'luogu', accounts: [{ handle: '42', has_cookie: true }] }],
  } }))
  await page.route('**/api/accounts/checks', route => route.fulfill({ json: { items: [{
    platform: 'luogu', handle: '42', status: 'blocked', reason: 'access_restricted',
    upstream_status: 418, checked_at: Math.floor(Date.now() / 1000),
    message: '旧缓存中的访问限制提示', browser_action_required: true,
  }] } }))
  await page.route('**/api/accounts/check', route => {
    loginChecks++
    return route.fulfill({ status: 500, json: { detail: 'Luogu should not request login verification' } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  const account = page.locator('.platform-account-check')
  await expect(account).toContainText('公开资料同步')
  await expect(account).not.toContainText(/访问限制|418|已验证登录|验证码/)
  await expect(account.getByRole('button')).toHaveCount(0)
  await expect(page.getByRole('link', { name: '打开洛谷官方网页 ↗' })).toHaveAttribute('href', 'https://www.luogu.com.cn/')
  await expect(page.getByRole('button', { name: '同步此平台信息' })).toBeEnabled()
  expect(loginChecks).toBe(0)
  await account.scrollIntoViewIfNeeded()
  await page.screenshot({ path: test.info().outputPath('luogu-public-sync.png') })
})

test('mobile userscript guide keeps install controls and source within the viewport', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  const notice = page.getByRole('button', { name: '已读以上更新', exact: true })
  if (await notice.count()) await notice.click()
  await page.getByRole('button', { name: '打开菜单', exact: true }).click()
  await page
    .getByRole('navigation')
    .getByRole('button', { name: '连接与设置', exact: true })
    .click()
  await page.getByRole('button', { name: '安装油猴脚本与绑定教程', exact: true }).click()
  const guide = page.locator('.extension-guide')
  await expect(guide).toContainText('Firefox 容器标签页、隐私窗口和首方隔离不支持脚本复制')
  await guide.getByRole('button', { name: '② 复制 ACM Lab 脚本源码' }).click()
  await guide.getByText('查看并手动复制脚本源码', { exact: true }).click()
  await expect(guide.getByLabel('ACM Lab 油猴脚本源码')).toBeVisible()
  const widths = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
  }))
  expect(widths.document).toBeLessThanOrEqual(widths.viewport)
  await guide.scrollIntoViewIfNeeded()
  await page.screenshot({ path: test.info().outputPath('userscript-mobile.png') })
})

test('CF verification checks the returned principal rather than just the session cookie marker', async ({
  page,
}) => {
  const result = {
    platform: 'codeforces',
    handle: 'fixture',
    checked_at: Math.floor(Date.now() / 1000),
    status: 'unknown',
    reason: 'page_changed',
    message: '合成验收：尚未获得当前登录身份',
    cookie_diagnostics: { cf_session_cookie_present: true },
    browser_action_required: false,
  }
  await page.route('**/api/platforms', (route) =>
    route.fulfill({
      json: {
        items: [{ platform: 'codeforces', accounts: [{ handle: 'fixture', has_cookie: true }] }],
      },
    }),
  )
  await page.route('**/api/accounts/checks', (route) =>
    route.fulfill({ json: { items: [result] } }),
  )
  await page.route('**/api/accounts/check', (route) =>
    route.fulfill({
      json: {
        ...result,
        status: 'authenticated',
        reason: 'identity_verified',
        matched_account: 'fixture',
        message: '合成验收：官方当前身份与配置匹配',
      },
    }),
  )
  await page.goto('/')
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  const account = page.locator('.platform-account-check')
  await expect(account).toContainText('JSESSIONID：已检测到')
  await expect(account).toContainText('无法确认登录')
  await expect(account).not.toContainText('已验证登录')
  await account.getByRole('button', { name: '检测登录状态', exact: true }).click()
  await expect(account).toContainText('已验证登录')
  await expect(account).toContainText('平台返回账号：fixture')
  await account.scrollIntoViewIfNeeded()
  await page.screenshot({ path: test.info().outputPath('cf-verification.png') })
  await page.getByRole('button', { name: '安装油猴脚本与绑定教程', exact: true }).click()
  await expect(page.getByRole('link', { name: 'Firefox Beta 官方下载页 ↗' })).toHaveAttribute(
    'href',
    'https://www.tampermonkey.net/?browser=firefox',
  )
  await expect(page.getByRole('link', { name: '打开 CF 设置页核对当前登录 ↗' })).toHaveAttribute(
    'href',
    'https://codeforces.com/settings/general',
  )
})

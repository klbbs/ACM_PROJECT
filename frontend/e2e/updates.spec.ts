import { test, expect } from '@playwright/test'

test('merged settings expose extension ZIP downloads and the existing guide shortcut', async ({
  page,
}) => {
  await page.goto('/')
  await expect(
    page.getByRole('navigation').getByRole('button', { name: '平台绑定教程', exact: true }),
  ).toHaveCount(0)
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  await page.getByRole('button', { name: '下载 Cookie 插件与绑定教程' }).click()
  await expect(page.getByRole('heading', { name: '一键获取平台 Cookie' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '平台绑定与提交教程' })).toBeVisible()
  await expect(page.getByText('Firefox 128+ · 临时测试安装')).toBeVisible()
  await page.locator('.settings-guide').scrollIntoViewIfNeeded()
  await page.screenshot({ path: test.info().outputPath('settings.png') })
  const downloadEvent = page.waitForEvent('download')
  await page.getByRole('link', { name: '下载 Chrome / Edge 插件 ZIP ↓' }).click()
  const download = await downloadEvent
  expect(download.suggestedFilename()).toBe('acm-lab-cookie-helper-chromium-1.0.0.zip')
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

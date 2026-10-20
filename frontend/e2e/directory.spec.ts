import { test, expect } from '@playwright/test'

test('home directory groups tools, searches feature descriptions and opens the merged tutorial', async ({
  page,
}) => {
  await page.goto('/')
  const notice = page.getByRole('button', { name: '已读以上更新', exact: true })
  if (await notice.count()) {
    await notice.click()
    await expect(notice).toHaveCount(0)
  }
  await expect(page.getByRole('heading', { name: /今天从哪里开始/ })).toBeVisible()
  await page.screenshot({ path: test.info().outputPath('main-directory.png') })
  const directory = page.locator('.main-directory')
  for (const name of ['开始训练', '记录与社交', '连接与管理'])
    await expect(directory.getByRole('region', { name })).toBeVisible()
  await expect(directory.getByRole('button', { name: '打开管理后台', exact: true })).toHaveCount(0)
  await expect(
    directory.getByRole('button', { name: '打开排行榜与好友', exact: true }),
  ).toHaveCount(0)
  await page.getByLabel('搜索功能', { exact: true }).fill('Cookie')
  await expect(directory.getByRole('status')).toHaveText('找到 1 个功能')
  await expect(directory.getByRole('button', { name: '打开连接与设置', exact: true })).toBeVisible()
  await directory.getByRole('button', { name: '打开连接与设置', exact: true }).click()
  await expect(
    page.getByRole('button', { name: '下载 Cookie 插件与绑定教程', exact: true }),
  ).toBeVisible()
  await page.getByRole('button', { name: '主要目录', exact: true }).click()
  await page.getByLabel('搜索功能', { exact: true }).fill('不存在的功能')
  await expect(page.getByRole('heading', { name: '没有找到这个功能', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '查看全部功能', exact: true }).click()
  await directory.getByRole('button', { name: '查看绑定与插件教程' }).click()
  await expect(
    page.getByRole('heading', { name: '一键获取平台 Cookie', exact: true }),
  ).toBeVisible()
})

test('member directory shows collaboration without administrator controls, and mobile navigation stays usable', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.route('**/api/auth/status', (route) =>
    route.fulfill({
      json: {
        authenticated: true,
        multi_user: true,
        user: { id: 'synthetic', username: 'fixture-user', role: 'member' },
        csrf_token: 'fixture',
      },
    }),
  )
  await page.route('**/api/presence', (route) => route.fulfill({ json: { items: [] } }))
  await page.goto('/')
  const directory = page.locator('.main-directory')
  await expect(
    directory.getByRole('button', { name: '打开排行榜与好友', exact: true }),
  ).toBeVisible()
  await expect(directory.getByRole('button', { name: '打开管理后台', exact: true })).toHaveCount(0)
  const overflow = await page.evaluate(() => ({
    viewport: window.innerWidth,
    width: document.documentElement.scrollWidth,
    elements: [...document.querySelectorAll('body *')]
      .filter((el) => el.getBoundingClientRect().right > window.innerWidth + 1)
      .map((el) => ({
        tag: el.tagName,
        classes: el.className,
        right: el.getBoundingClientRect().right,
        width: el.getBoundingClientRect().width,
      }))
      .slice(0, 15),
  }))
  expect(overflow.width, JSON.stringify(overflow)).toBeLessThanOrEqual(overflow.viewport)
  await page.getByRole('button', { name: '打开菜单', exact: true }).click()
  await page
    .getByRole('navigation', { name: '主要功能导航' })
    .getByRole('button', { name: '更新公告', exact: true })
    .click()
  await expect(page.getByRole('region', { name: '历史更新公告' })).toBeVisible()
  await expect(page.locator('.scrim')).toHaveCount(0)
})

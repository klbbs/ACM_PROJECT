import { test, expect } from '@playwright/test'
test('overview replaces standalone analysis and authenticated users can inspect presence', async ({
  page,
}) => {
  await page.route('**/api/auth/status', (route) =>
    route.fulfill({
      json: {
        authenticated: true,
        multi_user: true,
        user: { id: 'fixture', username: 'fixture-user', role: 'member' },
        csrf_token: 'fixture',
      },
    }),
  )
  await page.route('**/api/presence', (route) =>
    route.fulfill({
      json:
        route.request().method() === 'POST'
          ? { ok: true }
          : { items: [{ id: 'fixture', username: 'fixture-user', self: true }] },
    }),
  )
  await page.goto('/')
  await page.getByRole('button', { name: '训练总览', exact: true }).click()
  await expect(page.getByRole('button', { name: 'AI 能力分析', exact: true })).toHaveCount(0)
  await expect(page.locator('#overview-training-advice')).toBeVisible()
  await page.locator('.online-users summary').click()
  await expect(page.getByRole('region', { name: '在线用户列表' })).toContainText(
    'fixture-user（我）',
  )
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  await page.getByRole('button', { name: '配置平台账号', exact: true }).click()
  await expect(page.getByRole('heading', { name: '平台信息、登录检测与交题' })).toBeVisible()
  await expect(page.getByRole('button', { name: '同步此平台信息', exact: true })).toHaveCount(4)
})
test('AtCoder statement and submission links use platform-specific judge controls', async ({
  page,
}) => {
  await page.goto('/')
  const response = await page.request.post('/api/workbench/problem/atcoder%3Aabc001_1/import', {
    data: {
      html: '<h2 class="h2">A - Synthetic problem</h2><div id="task-statement"><span class="lang-en"><section><h3>Problem Statement</h3><p>Read two numbers.</p></section><section><h3>Sample Input 1</h3><pre>1 2</pre></section><section><h3>Sample Output 1</h3><pre>3</pre></section></span></div>',
    },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  await page.getByLabel('平台题号或官方题目链接').fill('atcoder:abc001_1')
  await page.locator('.wb-open').getByRole('button', { name: '打开', exact: true }).click()
  await expect(page.getByText('Read two numbers.', { exact: true })).toBeVisible()
  await page.getByLabel('源代码', { exact: true }).fill('int main(){return 0;}')
  await page.locator('.cm-content').press('Control+s')
  await expect(page.getByRole('button', { name: '保存提交快照', exact: true })).toHaveCount(0)
  await expect(page.getByRole('link', { name: '前往官方提交页 ↗', exact: true })).toHaveAttribute(
    'href',
    'https://atcoder.jp/contests/abc001/submit?taskScreenName=abc001_1',
  )
  await expect(page.getByLabel('本次平台提交号')).toHaveAttribute(
    'placeholder',
    '数字提交号或官方提交记录链接',
  )
  await expect(page.getByRole('button', { name: '同步此平台提交记录', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '前往官方提交页 ↗', exact: true })).toHaveAttribute(
    'href',
    'https://atcoder.jp/contests/abc001/submit?taskScreenName=abc001_1',
  )
  await expect(page.getByRole('button', { name: '保存草稿', exact: true })).toHaveCount(0)
})

test('newly saved platform account immediately exposes login detection without reloading', async ({
  page,
}) => {
  await page.goto('/')
  await page.request.put('/api/accounts', { data: { items: [] } })
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  await page.getByRole('button', { name: '配置平台账号', exact: true }).click()
  await page.getByLabel('用户名 / UID / 主页链接', { exact: true }).fill('cookie-test-user')
  await page.getByRole('button', { name: '保存平台账号', exact: true }).click()
  await page.getByRole('button', { name: '检测登录状态', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: '未保存 Cookie' })).toBeVisible()
  await expect(page.getByRole('status').filter({ hasText: '未保存 Cookie' })).not.toContainText(
    '已验证登录',
  )
  await page.request.put('/api/accounts', { data: { items: [] } })
})

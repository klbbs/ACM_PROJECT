import { test, expect, type Locator } from '@playwright/test'

async function defaultOnly(scope: Locator, model: string) {
  await expect(scope.getByLabel('AI 通道', { exact: true })).toHaveCount(0)
  await expect(scope.getByLabel('选择模型', { exact: true })).toHaveCount(0)
  await expect(scope.getByRole('button', { name: '读取可用模型', exact: true })).toHaveCount(0)
  await expect(scope.getByRole('status', { name: '当前 AI 配置' })).toContainText(model)
}

test('saved default AI applies across training and workbench without repeated model selectors or automatic generation', async ({ page }) => {
  test.setTimeout(45000)
  expect((await page.request.post('/_fixture/reset-workbench')).ok()).toBe(true)
  expect((await page.request.put('/api/ai/settings', {
    data: { model: 'saved-first-model', api_key: 'synthetic-default-config-key' },
  })).ok()).toBe(true)
  const calls: { path: string; body: any }[] = []
  const catalogs: string[] = []
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname
    if (path === '/api/ai/models') catalogs.push(path)
    if (request.method() === 'POST' && (/\/api\/(?:recommend|analysis|daily\/roll)$/.test(path) || /\/hint$/.test(path)))
      calls.push({ path, body: request.postDataJSON() })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '训练', exact: true }).click()
  await defaultOnly(page.locator('.daily-mission'), 'saved-first-model')
  await defaultOnly(page.locator('.practice-setup'), 'saved-first-model')
  await page.getByRole('tab', { name: '专项训练', exact: true }).click()
  await defaultOnly(page.locator('.specialist-training'), 'saved-first-model')
  expect(calls).toEqual([])
  expect(catalogs).toEqual([])

  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  await expect(page.getByRole('radio', { name: '使用 API Key 通道', exact: true })).toBeChecked()
  await page.getByText('目录未包含可用模型？手动填写模型 ID', { exact: true }).click()
  await page.getByLabel('手动模型 ID', { exact: true }).fill('saved-second-model')
  await page.getByRole('button', { name: /保存默认 AI 配置/ }).click()
  await expect.poll(async () => (await (await page.request.get('/api/ai/settings')).json()).model).toBe('saved-second-model')
  await page.getByRole('button', { name: '训练', exact: true }).click()
  await page.getByRole('tab', { name: '每日训练', exact: true }).click()
  await defaultOnly(page.locator('.daily-mission'), 'saved-second-model')
  await defaultOnly(page.locator('.practice-setup'), 'saved-second-model')

  await page.getByRole('button', { name: '刷题工作台', exact: true }).click()
  await page.getByLabel('平台题号或官方题目链接').fill('4-A')
  await page.locator('.wb-open').getByRole('button', { name: '打开', exact: true }).click()
  await defaultOnly(page.locator('.workbench'), 'saved-second-model')
  expect(calls).toEqual([])
  expect(catalogs).toEqual([])
  await page.getByRole('button', { name: /给我一个小提示/ }).click()
  await expect(page.getByText('先检查输入较小的一端。', { exact: true })).toBeVisible({ timeout: 15000 })
  expect(calls).toHaveLength(1)
  expect(calls[0].body).toMatchObject({ provider: 'api', model: 'saved-second-model' })
  expect(catalogs).toEqual([])
})

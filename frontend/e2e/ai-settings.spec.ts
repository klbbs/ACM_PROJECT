import { test, expect } from '@playwright/test'

test('API Key is saved before reading a remote catalog, dropdown selection is saved, failed refresh clears stale choices', async ({ page }) => {
  expect((await page.request.put('/api/ai/settings', { data: { model: '' } })).ok()).toBe(true)
  await page.goto('/')
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  await page.getByLabel('模型提供方').selectOption('deepseek')
  await page.getByLabel('API Key', { exact: true }).fill('synthetic-browser-fixture-key')

  let failCatalog = false
  let catalogCalls = 0
  await page.route('**/api/ai/models?provider=api', async (route) => {
    catalogCalls++
    // Verify that the real local settings endpoint has received the new key
    // before the UI attempts to load its directory. No upstream request runs.
    const saved = await (await page.request.get('/api/ai/settings')).json()
    expect(saved.has_key).toBe(true)
    expect(saved.base_url).toBe('https://api.deepseek.com')
    expect(JSON.stringify(saved)).not.toContain('synthetic-browser-fixture-key')
    await route.fulfill({
      // The backend maps an upstream authorization failure to AIError/400.
      // HTTP 401 from our own endpoint instead means the ACM session expired.
      status: failCatalog ? 400 : 200,
      contentType: 'application/json',
      body: JSON.stringify(failCatalog ? { detail: '模型列表请求失败，HTTP 401；可手动配置模型 ID' } : {
        items: [{ id: 'fixture-chat', name: 'Fixture Chat' }, { id: 'fixture-reasoner', name: 'Fixture Reasoner' }],
        source: 'remote', default_model: '', note: '隔离浏览器测试模型目录',
      }),
    })
  })

  await page.getByRole('button', { name: '保存并读取模型列表', exact: true }).click()
  const models = page.getByLabel('默认模型 ID', { exact: true })
  await expect(models.locator('option')).toHaveCount(3)
  await models.selectOption('fixture-reasoner')
  await page.getByRole('button', { name: /保存 API 配置/ }).click()
  await expect.poll(async () => (await (await page.request.get('/api/ai/settings')).json()).model).toBe('fixture-reasoner')

  failCatalog = true
  await page.getByRole('button', { name: '保存并读取模型列表', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: '读取失败' })).toBeVisible()
  await expect(models.locator('option[value="fixture-chat"]')).toHaveCount(0)
  expect(catalogCalls).toBe(2)
  await page.getByText('目录未包含可用模型？手动填写模型 ID', { exact: true }).click()
  await page.getByLabel('手动模型 ID', { exact: true }).fill('provider-private-model')
  await page.getByRole('button', { name: /保存 API 配置/ }).click()
  await expect.poll(async () => (await (await page.request.get('/api/ai/settings')).json()).model).toBe('provider-private-model')
})

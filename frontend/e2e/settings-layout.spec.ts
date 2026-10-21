import { expect, test } from '@playwright/test'

test('settings prioritize platforms, reveal optional sections and retain unsaved drafts across navigation', async ({
  page,
}) => {
  await page.goto('/')
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  const groups = page.locator('.settings-section')
  await expect(groups).toHaveCount(3)
  await expect(groups.nth(0).locator(':scope > summary')).toContainText('平台连接')
  await expect(groups.nth(1).locator(':scope > summary')).toContainText('AI 接入')
  await expect(groups.nth(2)).not.toHaveAttribute('open', '')
  await expect(page.locator('#platform-guide')).not.toHaveAttribute('open', '')
  await expect(page.locator('#platform-accounts')).not.toHaveAttribute('open', '')
  await expect(page.locator('.settings-advanced')).not.toHaveAttribute('open', '')

  // A shortcut reveals both the target and its collapsed parent.
  await groups.nth(0).locator(':scope > summary').click()
  await page.getByRole('button', { name: '配置平台账号', exact: true }).click()
  const handle = page.getByLabel('用户名 / UID / 主页链接', { exact: true })
  await expect(handle).toBeVisible()
  await handle.fill('unsaved-platform-draft')
  await page.getByLabel('API Key', { exact: true }).fill('synthetic-unsaved-key')
  await page.getByRole('button', { name: '训练偏好', exact: true }).click()
  await expect(page.getByLabel('目标 CF Rating')).toBeVisible()
  await page.getByLabel('目标 CF Rating').fill('2350')
  await page.getByRole('button', { name: '主要目录', exact: true }).click()
  await expect(page.getByLabel('API Key', { exact: true })).not.toBeVisible()
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  await expect(handle).toHaveValue('unsaved-platform-draft')
  await expect(page.getByLabel('API Key', { exact: true })).toHaveValue('synthetic-unsaved-key')
  await expect(page.getByLabel('目标 CF Rating')).toHaveValue('2350')
  await page.getByRole('button', { name: '安装油猴脚本与绑定教程', exact: true }).click()
  await expect(
    page.getByRole('heading', { name: '油猴脚本：获取平台 Cookie', exact: true }),
  ).toBeVisible()
})

test('reduced motion leaves feature changes immediate and suppresses page animations', async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/')
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  await page.getByRole('button', { name: '训练偏好', exact: true }).click()
  await expect(page.getByLabel('目标 CF Rating')).toBeVisible()
  expect(await page.locator('.content').evaluate((element) => element.getAnimations().length)).toBe(
    0,
  )
})

test('background job refresh preserves unsaved API/key pairing and training, then refreshes saved values', async ({
  page,
}) => {
  let phase = 0
  let configReads = 0
  let jobsReads = 0
  let savedPayload: any = null
  let savedTraining: any = null
  let config: any = {
    base_url: 'https://api.openai.com/v1',
    protocol: 'responses',
    model: 'saved-model',
    vendor: 'openai',
    has_key: false,
    codex_enabled: false,
  }
  let training: any = {
    goal_rating: 2000,
    minimum: 1000,
    maximum: 1600,
    preferred_tags: [],
    avoid_tags: [],
    prefer_recent: true,
  }
  await page.route('**/api/ai/settings', (route) => {
    if (route.request().method() === 'PUT') {
      savedPayload = route.request().postDataJSON()
      const { api_key, ...safe } = savedPayload
      config = { ...config, ...safe, has_key: !!api_key }
    } else configReads++
    return route.fulfill({ json: config })
  })
  await page.route('**/api/training', (route) => {
    if (route.request().method() === 'PUT') {
      savedTraining = route.request().postDataJSON()
      training = savedTraining
    }
    return route.fulfill({ json: training })
  })
  await page.route('**/api/jobs', (route) => {
    jobsReads++
    return route.fulfill({
      json: {
        items: [1, 2].map((index) => ({
          id: `draft-refresh-${index}`,
          kind: 'crawl',
          status: phase >= index ? 'completed' : 'running',
          created_at: 1,
        })),
      },
    })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '连接与设置', exact: true }).click()
  await expect(page.getByLabel('API 地址', { exact: true })).toHaveValue(
    'https://api.openai.com/v1',
  )
  await expect(page.getByLabel('默认模型 ID', { exact: true })).toHaveValue('saved-model')
  await expect.poll(() => jobsReads).toBeGreaterThan(0)
  await page.getByLabel('API 地址', { exact: true }).fill('https://api.deepseek.com')
  await page.getByLabel('API Key', { exact: true }).fill('synthetic-new-provider-key')
  await page.getByText('目录未包含可用模型？手动填写模型 ID', { exact: true }).click()
  await page.getByLabel('手动模型 ID', { exact: true }).fill('unsaved-model')
  await page.getByRole('button', { name: '训练偏好', exact: true }).click()
  await page.getByLabel('目标 CF Rating').fill('2350')
  const readsBefore = configReads
  phase = 1
  await expect.poll(() => configReads, { timeout: 8000 }).toBeGreaterThan(readsBefore)
  await expect(page.getByLabel('API 地址', { exact: true })).toHaveValue('https://api.deepseek.com')
  await expect(page.getByLabel('API Key', { exact: true })).toHaveValue(
    'synthetic-new-provider-key',
  )
  await expect(page.getByLabel('手动模型 ID', { exact: true })).toHaveValue('unsaved-model')
  await expect(page.getByLabel('目标 CF Rating')).toHaveValue('2350')
  await page.getByRole('button', { name: /保存 API 配置/ }).click()
  await expect.poll(() => savedPayload?.base_url).toBe('https://api.deepseek.com')
  expect(savedPayload.api_key).toBe('synthetic-new-provider-key')
  await expect(page.getByLabel('API Key', { exact: true })).toHaveValue('')
  await page.getByRole('button', { name: '保存训练偏好', exact: true }).click()
  await expect.poll(() => savedTraining?.goal_rating).toBe(2350)
  config = { ...config, model: 'server-after-save' }
  training = { ...training, goal_rating: 2700 }
  const readsAfterSave = configReads
  phase = 2
  await expect.poll(() => configReads, { timeout: 8000 }).toBeGreaterThan(readsAfterSave)
  await expect(page.getByLabel('默认模型 ID', { exact: true })).toHaveValue('server-after-save')
  await expect(page.getByLabel('目标 CF Rating')).toHaveValue('2700')
})

import { test, expect } from '@playwright/test'

test('daily plan generates every target, replaces one problem twice, and cannot claim a fake check-in', async ({
  page,
}) => {
  await page.route('**/api/ai/models?provider=api', (route) =>
    route.fulfill({
      json: {
        items: [{ id: 'fixture-daily', name: '隔离验收模型' }],
        default_model: 'fixture-daily',
        source: 'fixture',
        note: '仅测试模型',
      },
    }),
  )
  await page.goto('/')
  await page.getByRole('button', { name: '今日练习', exact: true }).click()
  const daily = page.locator('.daily-mission')
  await expect(daily.getByLabel('每日题量')).toBeEnabled()
  await daily.getByLabel('每日题量').selectOption('2')
  await daily.getByLabel('每天训练时间（分钟）').fill('45')
  await daily.getByLabel('持续天数').fill('14')
  await daily.getByRole('button', { name: '保存每日任务', exact: true }).click()
  await expect(daily).toContainText('今日目标 2 题')
  await daily.getByLabel('AI 通道').selectOption('api')
  await daily.getByRole('button', { name: '读取可用模型', exact: true }).click()
  await daily.getByLabel('选择模型').selectOption('fixture-daily')
  await expect(daily.getByRole('button', { name: 'AI 生成今日 2 道题' })).toBeDisabled()
  await daily.getByRole('checkbox', { name: /同意将训练偏好/ }).check()
  await daily.getByRole('button', { name: 'AI 生成今日 2 道题' }).click()
  await expect(daily.locator('.daily-problem')).toHaveCount(2)
  await expect(daily).toContainText('今日还可换题 2 次')
  const original = await daily.locator('.daily-problem').first().locator('strong').innerText()
  await daily.getByRole('button', { name: '换这道题', exact: true }).first().click()
  await expect(daily).toContainText('今日还可换题 1 次')
  await expect(daily.locator('.daily-problem').first().locator('strong')).not.toHaveText(original)
  await daily.getByRole('button', { name: '换这道题', exact: true }).first().click()
  await expect(daily).toContainText('今日还可换题 0 次')
  await expect(daily.getByRole('button', { name: '换这道题', exact: true }).first()).toBeDisabled()
  await expect(daily.getByRole('button', { name: '记录完成', exact: true })).toHaveCount(0)
  const forged = await page.request.post('/api/daily/complete', {
    data: { problem_id: 'codeforces:999-A' },
  })
  expect(forged.status()).toBe(409)
  await expect(daily).toContainText('尚未签到')
  await daily.getByLabel('每日题量').selectOption('1')
  await daily.getByRole('button', { name: '保存每日任务', exact: true }).click()
  await expect(daily).toContainText('今日目标 2 题')
  await expect(daily.locator('.daily-problem')).toHaveCount(2)
  await page.screenshot({ path: 'test-results/daily-plan.png', fullPage: true })
})

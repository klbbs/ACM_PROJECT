import { test, expect } from '@playwright/test'

async function community(page: any, options: { duel?: boolean; friend?: boolean } = {}) {
  const self = {
    id: 'self',
    username: 'fixture',
    display_name: '我的昵称',
    bio: '',
    leaderboard: false,
    public_profile: false,
  }
  let accepted = false
  let friendship = options.duel || options.friend ? 'friend' : 'none'
  let settings: any = { ...self }
  const sent: any[] = []
  await page.route('**/api/auth/status', (route: any) =>
    route.fulfill({
      json: {
        authenticated: true,
        multi_user: true,
        user: { id: 'self', username: 'fixture', role: 'member' },
        csrf_token: 'fixture',
      },
    }),
  )
  await page.route('**/api/presence', (route: any) =>
    route.fulfill({ json: route.request().method() === 'POST' ? { ok: true } : { items: [] } }),
  )
  await page.route('**/api/social/**', async (route: any) => {
    const request = route.request(),
      path = new URL(request.url()).pathname.replace('/api', ''),
      method = request.method()
    let response: any
    if (path === '/social/me') {
      if (method === 'PUT') {
        settings = { ...settings, ...request.postDataJSON() }
        sent.push({ path, body: request.postDataJSON() })
        response = { ok: true }
      } else
        response = {
          profile: settings,
          accounts: [],
          bindings: [{ platform: 'codeforces', handle: 'fixtureCF' }],
        }
    } else if (path === '/social/leaderboard')
      response = {
        items: [
          { id: 'partner', username: 'partner', display_name: '练习伙伴', count: 2, rank: 1 },
        ],
        rule: '每日官方 AC 按平台及题号去重',
      }
    else if (path === '/social/friends') {
      if (method === 'POST') {
        friendship = 'outgoing'
        sent.push({ path, body: request.postDataJSON() })
        response = { ok: true }
      } else
        response = {
          items:
            friendship === 'none'
              ? []
              : [
                  {
                    id: 'partner',
                    username: 'partner',
                    display_name: '练习伙伴',
                    relationship: friendship,
                  },
                ],
        }
    } else if (path === '/social/users')
      response = {
        items: [
          {
            id: 'partner',
            username: 'partner',
            display_name: '练习伙伴',
            relationship: friendship,
          },
        ],
      }
    else if (path === '/social/users/partner')
      response = {
        profile: {
          id: 'partner',
          username: 'partner',
          display_name: '练习伙伴',
          bio: '每天一道题',
        },
        relationship: friendship,
        day: '2026-10-20',
        items: [
          {
            id: 'codeforces:4-A',
            platform: 'codeforces',
            problem_id: '4-A',
            title: 'Watermelon',
            url: 'https://codeforces.com/problemset/problem/4/A',
            difficulty: 800,
            score: 100,
            tags: ['brute force', 'math'],
          },
        ],
      }
    else if (path === '/social/duels' && method === 'POST') {
      sent.push({ path, body: request.postDataJSON() })
      response = { id: 'new-synthetic-duel', status: 'pending' }
    } else if (path === '/social/duels')
      response = {
        items: options.duel
          ? [
              {
                id: 'd1',
                sender: '练习伙伴',
                target: '我',
                sender_id: 'partner',
                target_id: 'self',
                self_is_sender: false,
                status: accepted ? 'active' : 'pending',
                difficulty: 1200,
                duration_minutes: 60,
                published_after: '2025-01-01',
                ...(accepted
                  ? {
                      problem: {
                        id: 'codeforces:4-A',
                        title: 'Watermelon',
                        difficulty: 1200,
                        tags: ['math'],
                        url: 'https://codeforces.com/problemset/problem/4/A',
                      },
                    }
                  : {}),
              },
            ]
          : [],
      }
    else if (path === '/social/duels/d1/action') {
      sent.push({ path, body: request.postDataJSON() })
      accepted = true
      response = { status: 'active' }
    } else if (path === '/social/duels/d1/check') {
      sent.push({ path, body: request.postDataJSON() })
      response = { status: 'active' }
    } else if (path === '/social/accounts') {
      response = { detail: '请先在“连接与设置”检测此账号，确认登录身份后再用于榜单或对战' }
      await route.fulfill({ status: 409, json: response })
      return
    } else {
      await route.fulfill({ status: 404, json: { detail: 'fixture endpoint not found' } })
      return
    }
    await route.fulfill({ json: response })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '排行榜与好友', exact: true }).click()
  await expect(page.getByRole('heading', { name: '一起练，一起进步' })).toBeVisible()
  return sent
}

test('community leaderboard opens problem details and workbench; public settings require explicit opt-in', async ({
  page,
}) => {
  const sent = await community(page)
  await expect(page.getByText('你的榜单目前仅自己掌握')).toBeVisible()
  await page.getByRole('button', { name: '查看当日题目', exact: true }).click()
  await expect(page.getByRole('heading', { name: '练习伙伴 的主页' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Watermelon', exact: true })).toHaveAttribute(
    'href',
    'https://codeforces.com/problemset/problem/4/A',
  )
  await expect(page.getByText('brute force / math', { exact: true })).toBeVisible()
  await expect(page.getByText('Codeforces · 4-A · 难度 800 · 得分 100')).toBeVisible()
  await expect(page.getByRole('region', { name: '用户主页' })).toBeFocused()
  await page.getByRole('button', { name: '申请好友', exact: true }).click()
  await expect(page.getByRole('button', { name: '撤回申请', exact: true })).toBeVisible()
  expect(
    sent.some((r) => r.path === '/social/friends' && r.body.user_id === 'partner'),
  ).toBeTruthy()
  await page.getByRole('button', { name: '去工作台查看', exact: true }).click()
  await expect(page.getByRole('link', { name: '查看原题', exact: true })).toHaveAttribute(
    'href',
    'https://codeforces.com/problemset/problem/4/A',
  )
  await page.getByRole('button', { name: '排行榜与好友', exact: true }).click()
  await page.getByRole('button', { name: '我的主页', exact: true }).click()
  await page.getByLabel('加入每日公开榜单，并公开榜单题目详情').check()
  await page.getByRole('button', { name: '保存主页设置', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: '主页设置已保存' })).toBeVisible()
  expect(sent.some((r) => r.path === '/social/me' && r.body.leaderboard === true)).toBeTruthy()
  await page.getByRole('button', { name: '检测成功后认领', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: '检测此账号' }).first()).toBeVisible()
})

test('account submission sync refreshes the open leaderboard when its job completes', async ({
  page,
}) => {
  await community(page)
  let started = false
  let completed = false
  let reads = 0
  let sent = 0
  await page.route('**/api/social/leaderboard?**', (route) =>
    route.fulfill({
      json: {
        items: [{ id: 'partner', display_name: '练习伙伴', count: completed ? 3 : 2, rank: 1 }],
        rule: '包含所有练习题的当天官方 AC',
      },
    }),
  )
  await page.route('**/api/crawl', (route) => {
    expect(route.request().method()).toBe('POST')
    started = true
    sent += 1
    return route.fulfill({ json: { id: 'social-sync-job', status: 'queued' } })
  })
  await page.route('**/api/jobs', (route) => {
    if (!started) return route.fulfill({ json: { items: [] } })
    completed = ++reads > 1
    return route.fulfill({
      json: {
        items: [
          {
            id: 'social-sync-job',
            kind: 'crawl',
            status: completed ? 'completed' : 'queued',
            created_at: 1792830000,
            updated_at: 1792830000,
          },
        ],
      },
    })
  })
  await page.getByRole('button', { name: '同步我的提交记录', exact: true }).click()
  await expect(page.getByText('已开始同步平台提交记录，完成后自动更新排行榜。')).toBeVisible()
  await expect(page.getByRole('button', { name: '正在同步提交记录…' })).toBeDisabled({
    timeout: 10000,
  })
  await expect(page.locator('.social-ranking')).toContainText('3 题', { timeout: 12000 })
  expect(sent).toBe(1)
})

test('friend battle requires recipient acceptance and distinct official verification action', async ({
  page,
}) => {
  const sent = await community(page, { duel: true })
  await page.getByRole('button', { name: '好友对战', exact: true }).click()
  await expect(page.getByText('CF 1200 · 60 分钟 · 2025-01-01 起（北京时间，含当天）', { exact: true })).toBeVisible()
  await expect(page.getByText(/对战 AC 计入每日榜单与普通签到，不抵扣每日任务/)).toBeVisible()
  await page.getByRole('button', { name: '接受并随机抽题', exact: true }).click()
  await expect(page.getByRole('button', { name: '在工作台打开对战题', exact: true })).toBeVisible()
  expect(
    sent.some((r) => r.path === '/social/duels/d1/action' && r.body.action === 'accept'),
  ).toBeTruthy()
  await expect(page.getByRole('link', { name: '前往 CF 官方题目 / 交题' })).toHaveAttribute(
    'href',
    'https://codeforces.com/problemset/problem/4/A',
  )
  await page.getByRole('button', { name: '核验双方官方提交', exact: true }).click()
  expect(sent.filter((r) => r.path === '/social/duels/d1/check')).toHaveLength(1)
  await expect(page.getByText(/后台不会自动代交/)).toBeVisible()
})

test('a battle invitation requires a valid earliest publication date and sends the agreed date', async ({ page }) => {
  const sent = await community(page, { friend: true })
  await page.getByRole('button', { name: '好友对战', exact: true }).click()
  await page.getByLabel('对战好友').selectOption('partner')
  const invite = page.getByRole('button', { name: '发出邀请', exact: true })
  await expect(invite).toBeDisabled()
  await page.getByLabel('对战题目最早发布日期').fill('2099-01-01')
  await expect(invite).toBeDisabled()
  await page.getByLabel('对战题目最早发布日期').fill('2025-01-01')
  await expect(invite).toBeEnabled()
  await invite.click()
  expect(sent.filter((entry) => entry.path === '/social/duels')).toEqual([{
    path: '/social/duels',
    body: { user_id: 'partner', difficulty: 1200, duration_minutes: 60, published_after: '2025-01-01' },
  }])
})

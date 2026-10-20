import { test, expect } from '@playwright/test'

async function community(page: any, options: { duel?: boolean } = {}) {
  const self = {
    id: 'self',
    username: 'fixture',
    display_name: '我的昵称',
    bio: '',
    leaderboard: false,
    public_profile: false,
  }
  let accepted = false
  let friendship = options.duel ? 'friend' : 'none'
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
            tags: ['brute force', 'math'],
          },
        ],
      }
    else if (path === '/social/duels')
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
  await page.getByRole('button', { name: '练习伙伴', exact: true }).click()
  await expect(page.getByRole('heading', { name: '练习伙伴 的主页' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Watermelon', exact: true })).toHaveAttribute(
    'href',
    'https://codeforces.com/problemset/problem/4/A',
  )
  await expect(page.getByText('brute force / math', { exact: true })).toBeVisible()
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

test('friend battle requires recipient acceptance and distinct official verification action', async ({
  page,
}) => {
  const sent = await community(page, { duel: true })
  await page.getByRole('button', { name: '好友对战', exact: true }).click()
  await expect(page.getByText('CF 1200 · 60 分钟', { exact: true })).toBeVisible()
  await expect(page.getByText(/不抵扣每日任务/)).toBeVisible()
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

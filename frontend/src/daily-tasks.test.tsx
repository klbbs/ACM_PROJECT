import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { DailyTasksPage } from './daily-tasks'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('./api', () => ({ api: apiMock }))

let host: HTMLDivElement, root: Root
const value = (goal = 1) => ({
  settings: { daily_goal: goal, duration_days: 30 },
  active: false,
  locked_goal: goal,
  checkin: {},
  plan: null,
})
function deferred() {
  let resolve!: (v: any) => void
  const promise = new Promise((done) => {
    resolve = done
  })
  return { promise, resolve }
}
const render = async (revision = 0, overrides: Record<string, unknown> = {}) =>
  act(async () => {
    root.render(
      <DailyTasksPage
        revision={revision}
        provider="api"
        model="fixture"
        aiPicker={null}
        onError={vi.fn()}
        onNotice={vi.fn()}
        onOpenProblem={vi.fn()}
        {...overrides}
      />,
    )
  })
const goal = () => host.querySelector('select') as HTMLSelectElement
const save = () =>
  Array.from(host.querySelectorAll('button')).find(
    (button) => button.textContent === '保存每日任务',
  )!
const editGoal = async (next: string) =>
  act(async () => {
    goal().value = next
    goal().dispatchEvent(new Event('change', { bubbles: true }))
  })
beforeEach(() => {
  apiMock.mockReset()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

it('disables preferences and saving until the initial server settings arrive', async () => {
  const pending = deferred()
  apiMock.mockReturnValue(pending.promise)
  await render()
  expect(goal().disabled).toBe(true)
  expect(save().disabled).toBe(true)
  await act(async () => pending.resolve(value(3)))
  expect(host.textContent).not.toContain('每天训练时间')
  expect(goal().disabled).toBe(false)
  expect(goal().value).toBe('3')
  expect(save().disabled).toBe(false)
})

it('does not replace unsaved preferences when a revision refreshes verified progress', async () => {
  apiMock.mockResolvedValue(value())
  await render()
  await editGoal('2')
  await render(1)
  expect(goal().value).toBe('2')
  apiMock.mockResolvedValue(value(2))
  await act(async () => save().click())
  expect(apiMock).toHaveBeenCalledWith('/daily/settings', 'PUT', {
    daily_goal: 2,
    duration_days: 30,
  })
})

it('ignores a stale progress response that completes after preferences were saved', async () => {
  apiMock.mockResolvedValue(value())
  await render()
  await editGoal('2')
  const pendingRead = deferred()
  apiMock.mockImplementation((_path: string, method?: string) =>
    method === 'PUT' ? Promise.resolve(value(2)) : pendingRead.promise,
  )
  await render(1)
  await act(async () => save().click())
  await act(async () => pendingRead.resolve(value()))
  expect(goal().value).toBe('2')
  expect(host.textContent).toContain('今日目标 2 题')
})

const dailyTask = {
  task_id: 7,
  id: 'codeforces:4-A',
  problem_id: '4-A',
  title: 'Fixture task',
  url: 'https://codeforces.com/problemset/problem/4/A',
  difficulty: 1000,
  completed: false,
}
function findButton(text: string) {
  return [...host.querySelectorAll('button')].find((button) => button.textContent?.includes(text))!
}

it('generates and rerolls directly from explicit buttons, never from opening, refreshing or saving preferences', async () => {
  let state: any = { ...value(), active: true, rerolls_remaining: 2 }
  const requests: unknown[] = []
  apiMock.mockImplementation(async (path: string, method?: string, payload?: unknown) => {
    if (path === '/daily/roll' && method === 'POST') {
      requests.push(structuredClone(payload))
      state = {
        ...state,
        rerolls_remaining: requests.length === 1 ? 2 : 1,
        plan: { status: 'ready', tasks: [dailyTask], completed_ids: [] },
      }
    }
    return state
  })
  await render()
  expect(host.querySelector('input[type="checkbox"]')).toBeNull()
  expect(host.textContent).toContain(
    '训练偏好、每日目标、练习用时资料和公开候选题信息发送给所选 AI，可能产生 API 费用',
  )
  expect(findButton('AI 生成今日').disabled).toBe(false)
  await render(1)
  await act(async () => findButton('刷新已验证进度').click())
  await editGoal('2')
  await act(async () => save().click())
  expect(requests).toHaveLength(0)
  await act(async () => findButton('AI 生成今日').click())
  expect(requests).toEqual([{ provider: 'api', model: 'fixture' }])
  expect(findButton('换这道题').disabled).toBe(false)
  await act(async () => findButton('换这道题').click())
  expect(requests).toEqual([
    { provider: 'api', model: 'fixture' },
    { provider: 'api', model: 'fixture', task_id: 7 },
  ])
})

it.each([
  ['missing model', { active: true, plan: null }, { model: '' }],
  ['inactive training cycle', { active: false, plan: null }, {}],
  ['already generating', { active: true, plan: { status: 'generating' } }, {}],
])('cannot start generation with %s', async (_name, state, overrides) => {
  apiMock.mockResolvedValue({ ...value(), ...state })
  await render(0, overrides)
  const generate = [...host.querySelectorAll('button')].find(
    (button) =>
      button.textContent?.includes('AI 生成今日') || button.textContent?.includes('正在生成题单'),
  )!
  expect(generate.disabled).toBe(true)
  await act(async () => generate.click())
  expect(apiMock.mock.calls.some(([path]) => path === '/daily/roll')).toBe(false)
})

it('retains completed-task, reroll-limit and missing-model guards without consent checkboxes', async () => {
  let state: any = {
    ...value(),
    active: true,
    rerolls_remaining: 2,
    plan: {
      status: 'ready',
      tasks: [{ ...dailyTask, completed: true }],
      completed_ids: [dailyTask.id],
    },
  }
  apiMock.mockImplementation(async () => state)
  await render()
  expect(findButton('换这道题').disabled).toBe(true)
  state = { ...state, rerolls_remaining: 0, plan: { ...state.plan, tasks: [dailyTask] } }
  await render(1)
  expect(findButton('换这道题').disabled).toBe(true)
  state = { ...state, rerolls_remaining: 2 }
  await render(2, { model: '' })
  expect(findButton('换这道题').disabled).toBe(true)
  expect(apiMock.mock.calls.some(([path]) => path === '/daily/roll')).toBe(false)
})

it('does not submit a second selection while the first explicit request is pending', async () => {
  const state = { ...value(), active: true, rerolls_remaining: 2 }
  const pending = deferred()
  apiMock.mockImplementation((path: string) =>
    path === '/daily/roll' ? pending.promise : Promise.resolve(state),
  )
  await render()
  const generate = findButton('AI 生成今日')
  await act(async () => generate.click())
  expect(generate.disabled).toBe(true)
  await act(async () => generate.click())
  expect(apiMock.mock.calls.filter(([path]) => path === '/daily/roll')).toHaveLength(1)
  await act(async () => pending.resolve(state))
})

const secondTask = {
  ...dailyTask,
  task_id: 8,
  id: 'atcoder:abc001_a',
  problem_id: 'abc001_a',
  title: 'Second fixture task',
  url: 'https://atcoder.jp/contests/abc001/tasks/abc001_1',
}
const readyDay = (overrides: Record<string, unknown> = {}) => ({
  ...value(2),
  active: true,
  rerolls_limit: 2,
  rerolls_remaining: 2,
  plan: { status: 'ready', tasks: [dailyTask, secondTask], completed_ids: [] },
  ...overrides,
})
function taskCards() {
  return [...host.querySelectorAll<HTMLElement>('.daily-problem')]
}
function rollButton(index: number) {
  return taskCards()[index].querySelector<HTMLButtonElement>('button[aria-label^="Roll"]')!
}

it('shows a clear Roll button beside each task and sends only the clicked task id', async () => {
  let state = readyDay()
  apiMock.mockImplementation(async (path: string, method?: string, payload?: any) => {
    if (path === '/daily/roll' && method === 'POST') {
      expect(payload).toEqual({ provider: 'api', model: 'fixture', task_id: secondTask.task_id })
      state = readyDay({
        rerolls_remaining: 1,
        plan: {
          status: 'ready',
          tasks: [dailyTask, { ...secondTask, title: 'AI replacement', id: 'atcoder:abc002_a' }],
          completed_ids: [],
        },
      })
    }
    return state
  })
  await render()
  expect(taskCards()).toHaveLength(2)
  expect(rollButton(0).textContent).toContain('Roll · 换这道题')
  expect(rollButton(1).textContent).toContain('Roll · 换这道题')
  expect(host.textContent).toContain('今日 Roll 总额度 2 次，剩余 2 次')
  await act(async () => rollButton(1).click())
  expect(taskCards()[0].textContent).toContain('Fixture task')
  expect(taskCards()[1].textContent).toContain('AI replacement')
  expect(host.textContent).toContain('今日 Roll 总额度 2 次，剩余 1 次')
  expect(apiMock.mock.calls.filter(([path]) => path === '/daily/roll')).toHaveLength(1)
})

it('shows loading only for the selected task and blocks duplicate or simultaneous rerolls', async () => {
  const pending = deferred()
  const state = readyDay()
  apiMock.mockImplementation((path: string) =>
    path === '/daily/roll' ? pending.promise : Promise.resolve(state),
  )
  await render()
  await act(async () => rollButton(1).click())
  expect(rollButton(1).textContent).toContain('AI 正在 Roll 这道题')
  expect(rollButton(1).getAttribute('aria-busy')).toBe('true')
  expect(taskCards()[1].textContent).toContain('其余题目保持不变，请稍候')
  expect(rollButton(0).textContent).toContain('Roll · 换这道题')
  expect(taskCards()[0].textContent).toContain('请等待当前操作完成')
  expect(rollButton(0).disabled).toBe(true)
  expect(rollButton(1).disabled).toBe(true)
  await act(async () => {
    rollButton(0).click()
    rollButton(1).click()
  })
  expect(apiMock.mock.calls.filter(([path]) => path === '/daily/roll')).toHaveLength(1)
  await act(async () => pending.resolve(state))
  expect(rollButton(1).getAttribute('aria-busy')).toBe('false')
  expect(rollButton(0).disabled).toBe(false)
})

it('keeps the original task after a failed Roll and offers a retry for that exact task', async () => {
  let attempts = 0
  const state = readyDay()
  const onError = vi.fn()
  apiMock.mockImplementation(async (path: string, method?: string, payload?: any) => {
    if (path === '/daily/roll' && method === 'POST') {
      expect(payload.task_id).toBe(secondTask.task_id)
      attempts += 1
      if (attempts === 1) throw new Error('AI 服务暂时不可用')
    }
    return state
  })
  await render(0, { onError })
  await act(async () => rollButton(1).click())
  expect(taskCards()[0].querySelector('[role="alert"]')).toBeNull()
  expect(taskCards()[1].querySelector('[role="alert"]')?.textContent).toContain('AI 服务暂时不可用')
  expect(taskCards()[1].textContent).toContain('原题保留，请重试；失败不消耗机会')
  expect(taskCards()[1].textContent).toContain('Second fixture task')
  expect(rollButton(1).textContent).toContain('重试 Roll')
  expect(host.textContent).toContain('今日 Roll 总额度 2 次，剩余 2 次')
  await act(async () => rollButton(1).click())
  expect(taskCards()[1].querySelector('[role="alert"]')).toBeNull()
  expect(attempts).toBe(2)
  expect(onError).toHaveBeenCalledWith('AI 服务暂时不可用')
})

it.each([
  ['已完成题目不能 Roll', { plan: { status: 'ready', tasks: [{ ...dailyTask, completed: true }], completed_ids: [dailyTask.id] } }, {}],
  ['请先配置并选择 AI 模型', {}, { model: '' }],
  ['今日 Roll 机会已用完', { rerolls_remaining: 0 }, {}],
  ['训练周期未开启，请先保存每日任务', { active: false }, {}],
])('disables per-task Roll with a visible reason: %s', async (reason, overrides, props) => {
  apiMock.mockResolvedValue(readyDay(overrides))
  await render(0, props)
  expect(rollButton(0).disabled).toBe(true)
  expect(rollButton(0).title).toBe(reason)
  expect(taskCards()[0].textContent).toContain(reason)
  await act(async () => rollButton(0).click())
  expect(apiMock.mock.calls.some(([path]) => path === '/daily/roll')).toBe(false)
})

it('displays the server locked quota instead of changing today’s quota with tomorrow’s preference', async () => {
  let state = readyDay({
    locked_goal: 3,
    rerolls_limit: 3,
    rerolls_remaining: 1,
    settings: { daily_goal: 3, duration_days: 30 },
  })
  apiMock.mockImplementation(async (path: string) => {
    if (path === '/daily/settings') {
      state = { ...state, settings: { daily_goal: 8, duration_days: 30 } }
    }
    return state
  })
  await render()
  expect(host.textContent).toContain('今日 Roll 总额度 3 次，剩余 1 次')
  expect(host.textContent).not.toContain('每天可以换题两次')
  await editGoal('8')
  await act(async () => save().click())
  expect(goal().value).toBe('8')
  expect(host.textContent).toContain('今日 Roll 总额度 3 次，剩余 1 次')
  expect(apiMock.mock.calls.some(([path]) => path === '/daily/roll')).toBe(false)
})

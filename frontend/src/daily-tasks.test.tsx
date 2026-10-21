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

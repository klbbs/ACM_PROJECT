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
const render = async (revision = 0) =>
  act(async () => {
    root.render(
      <DailyTasksPage
        revision={revision}
        provider="api"
        model="fixture"
        aiPicker={null}
        onError={vi.fn()}
        onNotice={vi.fn()}
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

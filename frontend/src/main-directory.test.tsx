import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { MainDirectory } from './main-directory'
import { availableNavigation, nav } from './navigation'

it('uses one permission-aware registry for the sidebar and main directory', () => {
  expect(new Set(nav.map((item) => item.id)).size).toBe(nav.length)
  expect(
    availableNavigation(false).some((item) => ['social', 'community', 'admin'].includes(item.id)),
  ).toBe(false)
  expect(availableNavigation(true, 'member').some((item) => item.id === 'social')).toBe(true)
  expect(availableNavigation(true, 'member').some((item) => item.id === 'admin')).toBe(false)
  expect(availableNavigation(true, 'admin').some((item) => item.id === 'admin')).toBe(true)
})

it('offers categorized entry points, clear first-use steps and navigation without exposing markup', async () => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  const navigate = vi.fn(),
    guide = vi.fn()
  try {
    await act(async () =>
      root.render(
        <MainDirectory
          multiUser={true}
          role="member"
          username="<img src=x onerror=bad()>"
          unreadCount={2}
          onNavigate={navigate}
          onGuide={guide}
        />,
      ),
    )
    expect(host.querySelector('img')).toBeNull()
    expect(host.textContent).toContain('开始训练')
    expect(host.textContent).toContain('记录与社交')
    expect(host.textContent).toContain('连接与管理')
    expect(host.textContent).toContain('2 条未读')
    expect(host.querySelector('[aria-label="打开管理后台"]')).toBeNull()
    await act(async () =>
      (host.querySelector('[aria-label="打开刷题工作台"]') as HTMLButtonElement).click(),
    )
    expect(navigate).toHaveBeenCalledWith('workbench')
    await act(async () =>
      (host.querySelector('.directory-onboarding button') as HTMLButtonElement).click(),
    )
    expect(guide).toHaveBeenCalledOnce()
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
})

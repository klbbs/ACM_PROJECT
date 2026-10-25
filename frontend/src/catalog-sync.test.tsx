import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { CatalogSyncPanel, type CatalogSyncPanelProps } from './catalog-sync'

async function render(
  props: CatalogSyncPanelProps,
  check: (host: HTMLDivElement) => void | Promise<void>,
) {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  try {
    await act(async () => root.render(<CatalogSyncPanel {...props} />))
    await check(host)
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
}

it('shows queued, running progress and platform results next to the candidate list', async () => {
  await render(
    {
      candidateCount: 0,
      job: { status: 'queued' },
      eligibility: { refresh: [{ platform: 'codeforces', status: 'updated', count: 888 }] },
    },
    (host) => {
      expect(host.textContent).toContain('同步任务已排队')
      expect(host.textContent).toContain('0 / 4 个平台已处理')
      expect(host.textContent).not.toContain('更新 888 题')
      expect(host.querySelector('progress')?.max).toBe(4)
    },
  )
  await render(
    {
      candidateCount: 0,
      job: {
        status: 'running',
        result: {
          completed_platforms: 1,
          total_platforms: 4,
          platforms: [
            { platform: 'codeforces', status: 'updated', count: 123 },
            { platform: 'atcoder', status: 'running' },
          ],
        },
      },
    },
    (host) => {
      expect(host.textContent).toContain('1 / 4 个平台已处理')
      expect(host.textContent).toContain('更新 123 题')
      expect(host.textContent).toContain('正在获取')
      expect(host.querySelector('progress')?.value).toBe(1)
    },
  )
})

it('explains partial failures, time, current filters and separate login detection', async () => {
  const settings = vi.fn()
  await render(
    {
      candidateCount: 0,
      onSettings: settings,
      job: {
        status: 'partial',
        updated_at: 1760000000,
        result: {
          platforms: [
            {
              platform: 'luogu',
              status: 'failed',
              message: '官方平台拒绝访问（HTTP 403），已保留旧缓存',
            },
          ],
        },
      },
      eligibility: {
        candidate_count: 0,
        platforms: [
          {
            platform: 'codeforces',
            total_cached_count: 100,
            eligible_count: 0,
            reasons: ['登录尚未通过检测', '100 题早于最早发布日期'],
          },
        ],
      },
    },
    async (host) => {
      expect(host.textContent).toContain('部分平台同步成功')
      expect(host.textContent).toContain('HTTP 403')
      expect(host.textContent).toContain('结果时间')
      expect(host.textContent).toContain('100 题早于最早发布日期')
      expect(host.textContent).toContain('不检测 Cookie')
      expect(host.textContent).toContain('最近 6 场')
      await act(async () => (host.querySelector('button') as HTMLButtonElement).click())
      expect(settings).toHaveBeenCalledOnce()
    },
  )
})

it('keeps request failures and interrupted jobs visible and tolerates legacy status fields', async () => {
  await render({ candidateCount: 0, requestError: '同类任务正在执行，请等待完成' }, (host) => {
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('同类任务正在执行')
    expect(host.textContent).toContain('同步请求未启动')
  })
  await render(
    {
      candidateCount: 0,
      job: { status: 'interrupted' },
      eligibility: {
        platforms: [{ platform: 'nowcoder', cached_count: 50, notice: '需要重新检测登录状态' }],
      },
    },
    (host) => {
      expect(host.textContent).toContain('上次同步已中断')
      expect(host.textContent).toContain('缓存 50 题')
      expect(host.textContent).toContain('需要重新检测登录状态')
    },
  )
})

it('renders upstream strings as text and distinguishes the total pool from the visible slice', async () => {
  await render(
    {
      candidateCount: 60,
      eligibility: {
        candidate_count: 200,
        platforms: [{ platform: 'codeforces', reasons: ['<img src=x onerror=bad()>'] }],
      },
    },
    (host) => {
      expect(host.querySelector('img')).toBeNull()
      expect(host.textContent).toContain('200 道可选题，本次展示 60 道')
    },
  )
})

it('does not present stale completed results while starting or invent zero candidates on read failure', async () => {
  await render(
    {
      candidateCount: 0,
      busy: true,
      candidateError: '读取超时',
      job: {
        status: 'completed',
        updated_at: 1760000000,
        result: {
          count: 999,
          completed_platforms: 4,
          platforms: [{ platform: 'codeforces', status: 'updated', count: 999 }],
        },
      },
    },
    (host) => {
      expect(host.textContent).toContain('正在请求同步任务')
      expect(host.textContent).toContain('0 / 4 个平台已处理')
      expect(host.textContent).toContain('暂时无法读取候选条件：读取超时')
      expect(host.textContent).not.toContain('更新 999 题')
      expect(host.textContent).not.toContain('当前筛选共 0 道')
    },
  )
})

it('waits for current candidate conditions before displaying old or zero eligibility', async () => {
  await render(
    {
      candidateCount: 0,
      loadingCandidates: true,
      candidateError: '旧错误',
      eligibility: {
        candidate_count: 0,
        platforms: [{ platform: 'codeforces', reasons: ['旧条件'] }],
      },
    },
    (host) => {
      expect(host.textContent).toContain('正在读取候选条件…')
      expect(host.textContent).not.toContain('当前筛选共 0 道')
      expect(host.textContent).not.toContain('旧条件')
      expect(host.textContent).not.toContain('旧错误')
    },
  )
})

it('keeps successful catalog rows visible when personal submission sync fails', async () => {
  await render({ candidateCount: 3, job: { status: 'partial', result: {
    platforms: [{ platform: 'codeforces', status: 'updated', count: 123 }],
    submission_sync: { status: 'failed', message: '今日提交同步失败；题库结果已保留，请单独重试同步我的提交记录' },
  } } }, (host) => {
    expect(host.textContent).toContain('更新 123 题')
    expect(host.textContent).toContain('今日提交同步：未完成')
    expect(host.textContent).toContain('题库结果已保留')
  })
})

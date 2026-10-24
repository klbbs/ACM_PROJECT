import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it } from 'vitest'
import { TrainingHub } from './training-hub'

it('groups daily and specialist training and restores the selected mode for the same account', async () => {
  localStorage.clear()
  const host = document.createElement('div')
  const root = createRoot(host)
  const render = async (account: string, key: string) =>
    act(async () =>
      root.render(
        <TrainingHub
          key={key}
          account={account}
          daily={<p>每日题单</p>}
          specialist={<p>专项路线</p>}
        />,
      ),
    )
  try {
    await render('alice', 'initial')
    expect(host.querySelector('[role="tabpanel"]')?.textContent).toBe('每日题单')
    await act(async () =>
      (host.querySelector('#specialist-training-tab') as HTMLButtonElement).click(),
    )
    expect(host.querySelector('[role="tabpanel"]')?.textContent).toBe('专项路线')
    await render('alice', 'revisit')
    expect(host.querySelector('#specialist-training-tab')?.getAttribute('aria-selected')).toBe(
      'true',
    )
    await render('bob', 'new-account')
    expect(host.querySelector('[role="tabpanel"]')?.textContent).toBe('每日题单')
  } finally {
    await act(async () => root.unmount())
    localStorage.clear()
  }
})

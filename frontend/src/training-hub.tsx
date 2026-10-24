import { useState, type ReactNode } from 'react'

type Props = { account: string; daily: ReactNode; specialist: ReactNode }

/** Keep both training modes discoverable while preserving the existing daily workflow. */
export function TrainingHub({ account, daily, specialist }: Props) {
  const key = `acm:training-mode:${account}`
  const [mode, setMode] = useState(() => {
    try {
      return localStorage.getItem(key) === 'specialist' ? 'specialist' : 'daily'
    } catch {
      return 'daily'
    }
  })
  const select = (value: string) => {
    setMode(value)
    try {
      localStorage.setItem(key, value)
    } catch {
      /* Training works with browser storage disabled. */
    }
  }
  return (
    <div className="training-hub">
      <section className="card">
        <div className="card-heading">
          <div>
            <p className="eyebrow">KEEP LEARNING</p>
            <h2>选择训练方式</h2>
            <p>每天保持节奏，或从一道模板题开始，逐步掌握一个专题。</p>
          </div>
        </div>
        <div className="actions" role="tablist" aria-label="训练方式">
          <button
            type="button"
            role="tab"
            id="daily-training-tab"
            aria-controls="daily-training-panel"
            aria-selected={mode === 'daily'}
            className={mode === 'daily' ? 'button primary' : 'button secondary'}
            onClick={() => select('daily')}
          >
            每日训练
          </button>
          <button
            type="button"
            role="tab"
            id="specialist-training-tab"
            aria-controls="specialist-training-panel"
            aria-selected={mode === 'specialist'}
            className={mode === 'specialist' ? 'button primary' : 'button secondary'}
            onClick={() => select('specialist')}
          >
            专项训练
          </button>
        </div>
      </section>
      <div
        role="tabpanel"
        id={mode === 'daily' ? 'daily-training-panel' : 'specialist-training-panel'}
        aria-labelledby={mode === 'daily' ? 'daily-training-tab' : 'specialist-training-tab'}
      >
        {mode === 'daily' ? daily : specialist}
      </div>
    </div>
  )
}

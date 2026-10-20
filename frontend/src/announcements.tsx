import React, { useEffect, useState } from 'react'
import { api } from './api'
import './updates.css'

export type Announcement = { id: string; date: string; title: string; items: string[] }
export function useAnnouncements(enabled: boolean) {
  const [items, setItems] = useState<Announcement[]>([])
  const [unread, setUnread] = useState<string[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    api('/announcements')
      .then((data) => {
        if (cancelled) return
        setItems(data.items)
        setUnread(data.unread_ids)
        setError('')
      })
      .catch((e) => {
        if (!cancelled) setError(e.message)
      })
    return () => {
      cancelled = true
    }
  }, [enabled, retry])
  const markRead = async () => {
    if (!unread.length || busy) return
    setBusy(true)
    try {
      const result = await api('/announcements/read', 'POST', { ids: unread })
      setUnread(result.unread_ids)
      setError('')
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }
  return { items, unread, error, busy, markRead, retry: () => setRetry((n) => n + 1) }
}
type State = ReturnType<typeof useAnnouncements>

export function AnnouncementBanner({ state, onOpen }: { state: State; onOpen: () => void }) {
  if (!state.unread.length) return null
  const items = state.items.filter((item) => state.unread.includes(item.id))
  return (
    <section className="announcement-banner" aria-label="未读更新公告">
      <div className="announcement-heading">
        <strong>🌱 ACM Lab 更新了 · {state.unread.length} 条未读公告</strong>
        <button className="text-button" onClick={onOpen}>
          查看全部公告
        </button>
      </div>
      {items.map((item, i) => (
        <details key={item.id} open={i === 0}>
          <summary>
            v{item.id} · {item.title}
          </summary>
          <ul>
            {item.items.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
        </details>
      ))}
      {state.error && <p role="alert">{state.error}；未读状态未清除，请重试。</p>}
      <button className="secondary" disabled={state.busy} onClick={state.markRead}>
        {state.busy ? '正在记录…' : '已读以上更新'}
      </button>
    </section>
  )
}

export function AnnouncementPage({ state }: { state: State }) {
  return (
    <section className="announcement-history" aria-label="历史更新公告">
      <p className="help">
        本站目前处于测试版，全程由 GPT
        开发。公告随项目版本更新；已读记录仅属于你的账号，可跨设备保留。
      </p>
      {state.error && (
        <p role="alert">
          公告读取或保存失败：{state.error} <button onClick={state.retry}>重新读取公告</button>
        </p>
      )}
      {!state.items.length && !state.error && <p role="status">正在读取公告…</p>}
      {state.items.map((item) => (
        <article className="card" key={item.id}>
          <div className="announcement-heading">
            <h2>
              v{item.id} · {item.title}
            </h2>
            <span className={`badge ${state.unread.includes(item.id) ? 'success' : 'neutral'}`}>
              {state.unread.includes(item.id) ? '未读' : '已读'}
            </span>
          </div>
          <time dateTime={item.date}>{item.date}</time>
          <ul>
            {item.items.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
        </article>
      ))}
    </section>
  )
}

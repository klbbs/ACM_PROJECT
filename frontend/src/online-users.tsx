import { useEffect, useState } from 'react'
import { Users } from 'lucide-react'
import { api } from './api'

export function OnlineUsers() {
  const [items, setItems] = useState<{ username: string; self: boolean }[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    let alive = true
    const update = async () => {
      if (document.visibilityState === 'hidden') return
      try {
        await api('/presence', 'POST', {})
        const value = await api('/presence')
        if (alive) {
          setItems(value.items)
          setError('')
        }
      } catch {
        if (alive) setError('在线状态暂不可用')
      }
    }
    update()
    const timer = window.setInterval(update, 30000)
    document.addEventListener('visibilitychange', update)
    return () => {
      alive = false
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', update)
    }
  }, [])
  return (
    <details className="online-users">
      <summary>
        <Users size={16} /> 在线用户 <span>{items.length}</span>
      </summary>
      <div className="online-users-menu" role="region" aria-label="在线用户列表">
        {error ? (
          <p role="status">{error}</p>
        ) : (
          items.map((item) => (
            <p key={item.username}>
              <span className="online-dot" />
              {item.username}
              {item.self ? '（我）' : ''}
            </p>
          ))
        )}
        {!error && !items.length && <p>暂无在线用户</p>}
        <small>仅显示近90秒活动成员；协作待办将在后续接入。</small>
      </div>
    </details>
  )
}

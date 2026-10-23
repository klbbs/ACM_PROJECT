import { useEffect, useState } from 'react'
import { api, date } from './api'

type Row = Record<string, any>
type Props = { onError: (message: string) => void; onNotice: (message: string) => void }
const labels: Record<string, string> = { pending: '待管理员审核', approved: '已批准', rejected: '已拒绝' }

export function AdministratorApplication({ onError, onNotice }: Props) {
  const [items, setItems] = useState<Row[]>([])
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const reload = async () => setItems((await api('/auth/administrator-requests')).items)
  useEffect(() => { reload().catch(e => onError(e.message)) }, [])
  const pending = items.some(item => item.status === 'pending')
  return <section className="card settings-card">
    <h2>申请管理员</h2>
    <p className="help">需要已有管理员明确批准。申请期间保持普通成员权限；批准后请重新登录。</p>
    <form onSubmit={async event => {
      event.preventDefault()
      if (busy || pending || !reason.trim()) return
      setBusy(true)
      try {
        await api('/auth/administrator-requests', 'POST', { reason: reason.trim() })
        setReason('')
        await reload()
        onNotice('管理员申请已提交，等待已有管理员审核')
      } catch (error: any) { onError(error.message) } finally { setBusy(false) }
    }}>
      <label>申请理由<textarea rows={3} required maxLength={1000} value={reason}
        disabled={pending || busy} onChange={event => setReason(event.target.value)} /></label>
      <button className="primary" disabled={busy || pending || !reason.trim()}>
        {pending ? '已有申请待审核' : '提交管理员申请'}
      </button>
    </form>
    {items.map(item => <p className="help" key={item.id}>
      {date(item.created_at)} · {labels[item.status] || item.status}
      {item.reviewer && ` · 审核人：${item.reviewer}`}
      {item.review_note && ` · ${item.review_note}`}
    </p>)}
  </section>
}

export function AdministratorApproval({ onError, onNotice }: Props) {
  const [items, setItems] = useState<Row[]>([])
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const reload = async () => setItems((await api('/admin/administrator-requests')).items)
  useEffect(() => { reload().catch(e => onError(e.message)) }, [])
  const review = async (id: string, decision: 'approved' | 'rejected') => {
    if (busy) return
    setBusy(true)
    try {
      await api(`/admin/administrator-requests/${encodeURIComponent(id)}/review`, 'POST', { decision, note: notes[id] || '' })
      await reload()
      onNotice(decision === 'approved' ? '已批准管理员申请，对方需要重新登录后使用权限' : '已拒绝管理员申请')
    } catch (error: any) { onError(error.message) } finally { setBusy(false) }
  }
  return <section className="card admin-card">
    <div className="card-heading"><h2>管理员申请审批</h2>
      <button className="secondary" disabled={busy} onClick={() => reload().catch(e => onError(e.message))}>刷新申请</button>
    </div>
    <p className="help">批准后，该成员将能管理用户、邀请和共享审核。请核实身份后明确批准；不能审核自己的申请。</p>
    {!items.length && <p className="help">暂无管理员申请。</p>}
    {items.map(item => <article className="shared-entry" key={item.id}>
      <div className="card-heading"><h3>{item.username}</h3><span className="badge neutral">{labels[item.status] || item.status}</span></div>
      <p className="shared-body">{item.reason}</p>
      <small>{date(item.created_at)}{item.reviewer && ` · 审核人：${item.reviewer}`}</small>
      {item.status === 'pending' ? <>
        <label>审批备注<textarea rows={2} maxLength={1000} value={notes[item.id] || ''}
          onChange={event => setNotes(current => ({ ...current, [item.id]: event.target.value }))} /></label>
        <div className="settings-actions">
          <button disabled={busy} onClick={() => review(item.id, 'approved')}>同意授予管理员权限</button>
          <button className="secondary" disabled={busy} onClick={() => review(item.id, 'rejected')}>拒绝申请</button>
        </div>
      </> : item.review_note && <p className="help">{item.review_note}</p>}
    </article>)}
  </section>
}

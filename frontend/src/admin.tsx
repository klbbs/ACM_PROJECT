import React, { useEffect, useMemo, useState } from 'react'
import { Check, ClipboardList, RefreshCw, ShieldCheck, UserPlus, X } from 'lucide-react'
import { api, date } from './api'
import { TranslationStatement } from './translation-panel'

type Row = Record<string, any>
type Status = 'pending' | 'approved' | 'rejected'

const statusNames: Record<Status, string> = {
  pending: '待审核',
  approved: '已通过',
  rejected: '已拒绝',
}

const statusTone: Record<Status, string> = {
  pending: 'warning',
  approved: 'success',
  rejected: 'neutral',
}

function provenance(item: Row) {
  const source = item.provenance || {}
  return [source.vendor || source.provider, source.model || item.model].filter(Boolean).join(' · ')
}

export function AdminPage({
  user,
  onError,
  onNotice,
}: {
  user: Row
  onError: (message: string) => void
  onNotice: (message: string) => void
}) {
  const [status, setStatus] = useState<Status>('pending')
  const [queues, setQueues] = useState<Record<Status, Row[]>>({
    pending: [],
    approved: [],
    rejected: [],
  })
  const [users, setUsers] = useState<Row[]>([]),
    [audit, setAudit] = useState<Row[]>([])
  const [notes, setNotes] = useState<Record<string, string>>({}),
    [invitation, setInvitation] = useState<Row | null>(null),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(false)

  const reload = async () => {
    setLoading(true)
    try {
      const [pending, approved, rejected, members, events] = await Promise.all([
        api<{ items: Row[] }>('/translations/review?status=pending'),
        api<{ items: Row[] }>('/translations/review?status=approved'),
        api<{ items: Row[] }>('/translations/review?status=rejected'),
        api<{ items: Row[] }>('/admin/users'),
        api<{ items: Row[] }>('/admin/audit'),
      ])
      setQueues({
        pending: pending.items,
        approved: approved.items,
        rejected: rejected.items,
      })
      setUsers(members.items)
      setAudit(events.items)
    } catch (error) {
      onError(error instanceof Error ? error.message : '管理员数据加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    reload()
  }, [])

  const items = queues[status]
  const counts = useMemo(
    () => ({
      pending: queues.pending.length,
      approved: queues.approved.length,
      rejected: queues.rejected.length,
    }),
    [queues],
  )

  const review = async (item: Row, decision: Extract<Status, 'approved' | 'rejected'>) => {
    setBusy(true)
    try {
      await api(`/translations/${encodeURIComponent(item.id)}/review`, 'POST', {
        decision,
        note: notes[item.id] || '',
      })
      await reload()
      onNotice(decision === 'approved' ? '译文已通过审核并开放共享' : '译文已拒绝，暂不对其他用户开放')
    } catch (error) {
      onError(error instanceof Error ? error.message : '审核操作失败')
    } finally {
      setBusy(false)
    }
  }

  const action = async (fn: () => Promise<void>) => {
    setBusy(true)
    try {
      await fn()
      await reload()
    } catch (error) {
      onError(error instanceof Error ? error.message : '管理员操作失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="admin-dashboard">
      <section className="card admin-hero">
        <div>
          <p className="eyebrow"><ShieldCheck size={15} /> 管理员后台</p>
          <h2>维护共享内容与站点成员</h2>
          <p className="help">
            当前账号：{user.username}。审核通过的译文会进入共享库；个人题面、代码、Cookie 和 API Key
            不会显示在这里。
          </p>
        </div>
        <button className="secondary" onClick={() => reload()} disabled={loading || busy}>
          <RefreshCw size={15} /> 刷新后台数据
        </button>
      </section>

      <section className="admin-metrics" aria-label="翻译审核统计">
        {(Object.keys(statusNames) as Status[]).map((key) => (
          <button
            key={key}
            className={`admin-metric ${status === key ? 'selected' : ''}`}
            onClick={() => setStatus(key)}
          >
            <span>{statusNames[key]}</span>
            <strong>{counts[key]}</strong>
          </button>
        ))}
        <div className="admin-metric admin-metric-static">
          <span>本站成员</span>
          <strong>{users.length}</strong>
        </div>
      </section>

      <section className="card admin-card">
        <div className="card-heading">
          <div>
            <p className="eyebrow"><ClipboardList size={15} /> 题面翻译</p>
            <h2>{statusNames[status]}译文</h2>
          </div>
          <span className={`badge ${statusTone[status]}`}>{items.length} 条</span>
        </div>
        {!items.length && <p className="help">当前状态没有译文记录。</p>}
        <div className="admin-translation-list">
          {items.map((item) => (
            <article className="admin-translation" key={item.id}>
              <div className="admin-translation-heading">
                <div>
                  <h3>{item.problem_id}</h3>
                  <p className="muted">{provenance(item) || '未记录模型'} · {date(item.submitted_at)}</p>
                </div>
                <span className={`badge ${statusTone[status]}`}>{statusNames[status]}</span>
              </div>
              <div className="admin-translation-meta">
                <span>题面版本：{String(item.fingerprint || '').slice(0, 12) || '未知'}…</span>
                <span>语言：{item.target || 'zh-CN'}</span>
              </div>
              <div className="admin-translation-preview">
                <details>
                  <summary>查看原题对照</summary>
                  <TranslationStatement statement={item.source_statement} />
                </details>
                <details>
                  <summary>查看译文</summary>
                  <TranslationStatement statement={item.statement} />
                </details>
              </div>
              {status === 'pending' && (
                <>
                  <label>
                    审核意见
                    <textarea
                      rows={3}
                      maxLength={1000}
                      value={notes[item.id] || ''}
                      onChange={(event) => setNotes((current) => ({ ...current, [item.id]: event.target.value }))}
                      placeholder="指出公式、条件、样例或语义问题；拒绝时建议填写原因"
                    />
                  </label>
                  <div className="settings-actions">
                    <button disabled={busy} onClick={() => review(item, 'approved')}>
                      <Check size={15} /> 批准共享
                    </button>
                    <button
                      className="secondary"
                      disabled={busy || !notes[item.id]?.trim()}
                      onClick={() => review(item, 'rejected')}
                    >
                      <X size={15} /> 拒绝
                    </button>
                  </div>
                </>
              )}
              {item.review_note && <p className="help">审核意见：{item.review_note}</p>}
            </article>
          ))}
        </div>
      </section>

      <section className="card admin-card">
        <div className="card-heading">
          <div>
            <p className="eyebrow"><UserPlus size={15} /> 账号与权限</p>
            <h2>成员管理</h2>
          </div>
          <button
            className="secondary"
            disabled={busy}
            onClick={() => action(async () => setInvitation(await api('/admin/invitations', 'POST', {})))}
          >
            生成邀请码
          </button>
        </div>
        {invitation && (
          <div className="device-code admin-invitation">
            <strong>{invitation.invitation}</strong>
            <small>有效至 {date(invitation.expires_at)}；请通过安全渠道发送，离开页面后不再显示。</small>
          </div>
        )}
        <div className="member-table">
          <table>
            <thead><tr><th>用户</th><th>角色</th><th>状态</th><th>操作</th></tr></thead>
            <tbody>
              {users.map((member) => (
                <tr key={member.id}>
                  <td>{member.username}</td>
                  <td>{member.role === 'admin' ? '管理员' : '成员'}</td>
                  <td>{member.active ? '启用' : '停用'}</td>
                  <td>
                    {member.id === user.id ? '当前账号' : (
                      <div className="settings-actions">
                        <button className="text-button" disabled={busy} onClick={() => action(() => api(`/admin/users/${member.id}`, 'PUT', { role: member.role, active: !member.active }).then(() => undefined))}>
                          {member.active ? '停用' : '启用'}
                        </button>
                        <button className="text-button" disabled={busy} onClick={() => action(() => api(`/admin/users/${member.id}`, 'PUT', { role: member.role === 'admin' ? 'member' : 'admin', active: !!member.active }).then(() => undefined))}>
                          {member.role === 'admin' ? '设为成员' : '设为管理员'}
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card admin-card">
        <div className="card-heading"><h2>最近安全审计</h2><span className="muted">仅显示最近 100 条</span></div>
        <div className="audit-list">
          {audit.map((event) => (
            <p key={event.id}><time>{date(event.created_at)}</time> · {event.username || '未登录'} · {event.event}</p>
          ))}
          {!audit.length && <p className="help">暂无审计记录。</p>}
        </div>
      </section>
    </div>
  )
}

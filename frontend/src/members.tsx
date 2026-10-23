import { useEffect, useState } from 'react'
import { api, date } from './api'
import { AdministratorApplication } from './administrator-requests'

type Row = Record<string, any>

export function UserLogin({ status }: { status: Row }) {
  const [mode, setMode] = useState(status.setup_required ? 'setup' : 'login')
  const [username, setUsername] = useState(status.setup_required ? 'kendas' : ''),
    [password, setPassword] = useState('')
  const [code, setCode] = useState(''),
    [confirm, setConfirm] = useState('')
  const [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  return (
    <div className="login-page">
      <form
        onSubmit={async (e) => {
          e.preventDefault()
          setError('')
          if (mode !== 'login' && password !== confirm) {
            setError('两次密码不一致')
            return
          }
          setBusy(true)
          try {
            await api(`/auth/${mode}`, 'POST', {
              username,
              password,
              ...(mode === 'setup'
                ? { setup_token: code }
                : mode === 'register'
                  ? { invitation: code }
                  : {}),
            })
            window.location.reload()
          } catch (e: any) {
            setError(e.message)
          } finally {
            setBusy(false)
          }
        }}
      >
        <h1>
          {mode === 'setup'
            ? '初始化管理员'
            : mode === 'register'
              ? '受邀加入 ACM Lab'
              : '登录 ACM Lab'}
        </h1>
        <p>
          独立训练空间，按需共享资料。
        </p>
        <label>
          用户名
          <input
            value={username}
            readOnly={mode === 'setup'}
            autoComplete="username"
            minLength={3}
            maxLength={32}
            pattern="[a-zA-Z0-9_]+"
            required
            onChange={(e) => setUsername(e.target.value)}
            placeholder="3–32 位字母、数字或下划线"
          />
        </label>
        <label>
          密码
          <input
            type="password"
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            value={password}
            minLength={mode === 'login' ? 3 : 10}
            maxLength={128}
            required
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {mode !== 'login' && (
          <>
            <label>
              确认密码
              <input
                type="password"
                autoComplete="new-password"
                value={confirm}
                required
                onChange={(e) => setConfirm(e.target.value)}
              />
            </label>
            <label>
              {mode === 'setup' ? '部署初始化令牌' : '邀请码'}
              <input
                type="password"
                value={code}
                autoComplete="off"
                required
                onChange={(e) => setCode(e.target.value)}
              />
            </label>
          </>
        )}
        {mode === 'setup' && (
          <p className="help">
            使用服务器 .env 中的
            ACM_ACCESS_TOKEN，仅用于创建首位管理员。旧个人数据不会向其他用户开放。
          </p>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button className="primary" disabled={busy}>
          {busy
            ? '正在验证…'
            : mode === 'setup'
              ? '创建管理员'
              : mode === 'register'
                ? '使用邀请注册'
                : '登录'}
        </button>
        {!status.setup_required && (
          <button
            type="button"
            className="text-button"
            onClick={() => {
              setMode(mode === 'login' ? 'register' : 'login')
              setError('')
              setPassword('')
              setConfirm('')
              setCode('')
            }}
          >
            {mode === 'login' ? '我有邀请码，创建账号' : '已有账号，返回登录'}
          </button>
        )}
      </form>
    </div>
  )
}

export function MemberPage({
  user,
  onError,
  onNotice,
}: {
  user: Row
  onError: (s: string) => void
  onNotice: (s: string) => void
}) {
  const [items, setItems] = useState<Row[]>([]),
    [users, setUsers] = useState<Row[]>([]),
    [audit, setAudit] = useState<Row[]>([])
  const [search, setSearch] = useState(''),
    [offset, setOffset] = useState(0),
    [total, setTotal] = useState(0),
    [trashed, setTrashed] = useState(false)
  const empty = { title: '', body: '', visibility: 'private', version: 1 }
  const [draft, setDraft] = useState<Row>(empty),
    [invitation, setInvitation] = useState<Row | null>(null),
    [busy, setBusy] = useState(false)
  const [currentPassword, setCurrentPassword] = useState(''),
    [newPassword, setNewPassword] = useState('')
  const reload = async () => {
    const data = await api(
      `/shared?search=${encodeURIComponent(search)}&offset=${offset}&trashed=${trashed}`,
    )
    setItems(data.items)
    setTotal(data.total)
    if (user.role === 'admin') {
      setUsers((await api('/admin/users')).items)
      setAudit((await api('/admin/audit')).items)
    }
  }
  useEffect(() => {
    reload().catch((e) => onError(e.message))
  }, [search, offset, trashed])
  const action = async (fn: () => Promise<void>) => {
    setBusy(true)
    try {
      await fn()
      await reload()
    } catch (e: any) {
      onError(e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <section className="card settings-card">
        <h2>共享资料库</h2>
        <p className="help">
          个人草稿仅自己可见。发布共享后，本站所有已登录成员可以查看；仅作者能编辑，管理员可移除违规共享内容。
        </p>
        <div className="settings-actions">
          <input
            aria-label="搜索共享资料"
            placeholder="搜索标题或正文"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setOffset(0)
            }}
          />
          <label className="checkbox">
            <input
              type="checkbox"
              checked={trashed}
              onChange={(e) => {
                setTrashed(e.target.checked)
                setOffset(0)
              }}
            />
            查看回收站
          </label>
        </div>
        <div className="shared-list">
          {items.map((item) => (
            <article className="shared-entry" key={item.id}>
              <div className="card-heading">
                <h3>{item.title}</h3>
                <span className="badge neutral">
                  {item.visibility === 'shared' ? '成员共享' : '个人草稿'}
                </span>
              </div>
              <small>
                {item.author} · {date(item.updated_at)} · 版本 {item.version}
              </small>
              <p className="shared-body">{item.body}</p>
              <div className="settings-actions">
                {!trashed && item.owner_id === user.id && (
                  <button className="text-button" onClick={() => setDraft(item)}>
                    编辑
                  </button>
                )}
                {(item.owner_id === user.id || user.role === 'admin') && (
                  <button
                    disabled={busy}
                    className="text-button"
                    onClick={() =>
                      action(async () => {
                        await api(
                          `/shared/${item.id}${trashed ? '/restore' : ''}`,
                          trashed ? 'POST' : 'DELETE',
                          {},
                        )
                        if (draft.id === item.id) setDraft(empty)
                        onNotice(trashed ? '资料已恢复' : '已移至回收站，可恢复')
                      })
                    }
                  >
                    {trashed ? '恢复资料' : '移至回收站'}
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
        {!items.length && <p className="help">暂无资料。可以先写一份个人草稿。</p>}
        <div className="settings-actions">
          <button
            className="secondary"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 30))}
          >
            上一页
          </button>
          <span>{total} 条资料</span>
          <button
            className="secondary"
            disabled={offset + 30 >= total}
            onClick={() => setOffset(offset + 30)}
          >
            下一页
          </button>
        </div>
      </section>
      <section className="card settings-card">
        <h2>{draft.id ? '编辑资料' : '新建资料'}</h2>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            action(async () => {
              const payload = {
                title: draft.title,
                body: draft.body,
                visibility: draft.visibility,
                version: draft.version,
              }
              await api(
                draft.id ? `/shared/${draft.id}` : '/shared',
                draft.id ? 'PUT' : 'POST',
                payload,
              )
              setDraft(empty)
              onNotice(
                payload.visibility === 'shared' ? '资料已发布，本站成员可见' : '个人草稿已保存',
              )
            })
          }}
        >
          <label>
            资料标题
            <input
              required
              maxLength={150}
              value={draft.title}
              onChange={(e) => setDraft({ ...draft, title: e.target.value })}
            />
          </label>
          <label>
            资料正文
            <textarea
              required
              maxLength={20000}
              rows={8}
              value={draft.body}
              onChange={(e) => setDraft({ ...draft, body: e.target.value })}
            />
          </label>
          <label>
            可见范围
            <select
              value={draft.visibility}
              onChange={(e) => setDraft({ ...draft, visibility: e.target.value })}
            >
              <option value="private">仅自己可见</option>
              <option value="shared">所有本站成员可见</option>
            </select>
          </label>
          <p className="help">
            请勿在共享正文中粘贴 Cookie、密码或 API Key。代码与链接按纯文本显示。
          </p>
          <div className="settings-actions">
            <button className="primary" disabled={busy}>
              保存资料
            </button>
            {draft.id && (
              <button type="button" className="text-button" onClick={() => setDraft(empty)}>
                取消编辑
              </button>
            )}
          </div>
        </form>
      </section>
      {user.role === 'admin' && (
        <section className="card settings-card">
          <h2>用户与邀请管理</h2>
          <p className="help">
            邀请一次有效，24 小时过期。停用或更改角色会撤销该用户的全部登录会话。
          </p>
          <button
            className="primary"
            disabled={busy}
            onClick={() =>
              action(async () => {
                setInvitation(await api('/admin/invitations', 'POST', {}))
              })
            }
          >
            生成一次性邀请码
          </button>
          {invitation && (
            <div className="device-code">
              <p>请自行发送给受邀者；离开页面后不再显示原码。</p>
              <strong>{invitation.invitation}</strong>
              <small>有效至 {date(invitation.expires_at)}</small>
            </div>
          )}
          <div className="member-table">
            <table>
              <thead>
                <tr>
                  <th>用户</th>
                  <th>角色</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {users.map((member) => (
                  <tr key={member.id}>
                    <td>{member.username}</td>
                    <td>{member.role === 'admin' ? '管理员' : '成员'}</td>
                    <td>{member.active ? '启用' : '停用'}</td>
                    <td>
                      {member.id === user.id ? (
                        '当前账号'
                      ) : (
                        <div className="settings-actions">
                          <button
                            className="text-button"
                            disabled={busy}
                            onClick={() =>
                              action(async () => {
                                await api(`/admin/users/${member.id}`, 'PUT', {
                                  role: member.role,
                                  active: !member.active,
                                })
                              })
                            }
                          >
                            {member.active ? '停用' : '启用'}
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3>最近安全审计</h3>
          <div className="audit-list">
            {audit.map((event) => (
              <p key={event.id}>
                <time>{date(event.created_at)}</time> · {event.username || '未登录'} · {event.event}
              </p>
            ))}
          </div>
        </section>
      )}
      {user.role !== 'admin' && <AdministratorApplication onError={onError} onNotice={onNotice} />}
      <section className="card settings-card">
        <h2>账号安全</h2>
        <p className="help">当前用户：{user.username}。修改密码后全部设备需重新登录。</p>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            action(async () => {
              await api('/auth/password', 'POST', {
                current_password: currentPassword,
                new_password: newPassword,
              })
              window.location.reload()
            })
          }}
        >
          <label>
            当前密码
            <input
              type="password"
              autoComplete="current-password"
              required
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
            />
          </label>
          <label>
            新密码
            <input
              type="password"
              autoComplete="new-password"
              minLength={10}
              maxLength={128}
              required
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
            />
          </label>
          <div className="settings-actions">
            <button className="primary" disabled={busy}>
              修改密码并重新登录
            </button>
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() =>
                action(async () => {
                  await api('/auth/logout-all', 'POST', {})
                  window.location.reload()
                })
              }
            >
              退出全部设备
            </button>
          </div>
        </form>
      </section>
    </>
  )
}

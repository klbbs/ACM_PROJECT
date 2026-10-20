import { useEffect, useRef, useState } from 'react'
import { api, date, names } from './api'
import './social.css'

type Props = { onOpenProblem: (id: string) => void; onError?: (message: string) => void }
const relations: Record<string, string> = {
  self: '你自己',
  friend: '好友',
  outgoing: '等待对方确认',
  incoming: '对方申请了好友',
  none: '尚未添加',
}
const duelStatuses: Record<string, string> = {
  pending: '等待应战',
  starting: '正在核验并随机出题',
  active: '对战进行中',
  finished: '对战已结束',
  draw: '平局 / 到时未分胜负',
  expired: '邀请已过期',
  cancelled: '邀请已撤回',
  declined: '邀请已拒绝',
}
const today = () => new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Shanghai' })

export function SocialPage({ onOpenProblem, onError }: Props) {
  const [tab, setTab] = useState('leaderboard')
  const [me, setMe] = useState<any>(null)
  const [form, setForm] = useState<any>(null)
  const [friends, setFriends] = useState<any[]>([])
  const [duels, setDuels] = useState<any[]>([])
  const [board, setBoard] = useState<any>(null)
  const [day, setDay] = useState(today)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<any[]>([])
  const [profile, setProfile] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [available, setAvailable] = useState<boolean | null>(null)
  const [opponent, setOpponent] = useState('')
  const [difficulty, setDifficulty] = useState(1200)
  const [minutes, setMinutes] = useState(60)
  const mounted = useRef(true)
  const profileInitialized = useRef(false)
  function fail(e: any) {
    const message = e?.message || '请求未完成'
    if (mounted.current) setError(message)
    onError?.(message)
  }
  async function refresh() {
    const [own, fr, ds, lb] = await Promise.all([
      api('/social/me'),
      api('/social/friends'),
      api('/social/duels'),
      api(`/social/leaderboard?day=${day}`),
    ])
    if (!mounted.current) return
    setMe(own)
    setFriends(fr.items)
    setDuels(ds.items)
    setBoard(lb)
    setResults((previous) =>
      previous.map((u) => ({
        ...u,
        relationship:
          u.id === own.profile.id
            ? 'self'
            : fr.items.find((f: any) => f.id === u.id)?.relationship || 'none',
      })),
    )
    setProfile((previous: any) =>
      previous
        ? {
            ...previous,
            relationship:
              previous.profile.id === own.profile.id
                ? 'self'
                : fr.items.find((f: any) => f.id === previous.profile.id)?.relationship || 'none',
          }
        : previous,
    )
    if (!profileInitialized.current) {
      setForm(own.profile)
      profileInitialized.current = true
    }
  }
  useEffect(() => {
    mounted.current = true
    api('/auth/status')
      .then(async (status) => {
        if (!mounted.current) return
        setAvailable(Boolean(status.multi_user))
        if (status.multi_user) await refresh()
      })
      .catch(fail)
    return () => {
      mounted.current = false
    }
  }, [])
  useEffect(() => {
    if (!available) return
    let alive = true
    api(`/social/leaderboard?day=${day}`)
      .then((value) => {
        if (alive) setBoard(value)
      })
      .catch(fail)
    return () => {
      alive = false
    }
  }, [day, available])
  useEffect(() => {
    if (!available || tab !== 'duels') return
    const timer = window.setInterval(() => {
      api('/social/duels')
        .then((ds) => {
          if (mounted.current) setDuels(ds.items)
        })
        .catch(fail)
    }, 15000)
    return () => window.clearInterval(timer)
  }, [tab, available])
  async function act(fn: () => Promise<any>, message = '') {
    if (busy) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await fn()
      await refresh()
      if (message) setNotice(message)
    } catch (e) {
      fail(e)
    } finally {
      if (mounted.current) setBusy(false)
    }
  }
  async function openProfile(id: string) {
    setError('')
    try {
      setProfile(await api(`/social/users/${encodeURIComponent(id)}?day=${day}`))
    } catch (e) {
      fail(e)
    }
  }
  useEffect(() => {
    if (!profile) return
    const detail = document.querySelector<HTMLElement>('.social-profile-detail')
    detail?.scrollIntoView({
      behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
      block: 'start',
    })
    detail?.focus({ preventScroll: true })
  }, [profile])
  useEffect(() => {
    setProfile(null)
  }, [tab, day])
  const friendButtons = (u: any, showProfile = true) => (
    <div className="actions">
      {showProfile && (
        <button className="secondary" onClick={() => openProfile(u.id)}>
          查看主页
        </button>
      )}
      {u.relationship === 'none' && (
        <button
          disabled={busy}
          onClick={() =>
            act(() => api('/social/friends', 'POST', { user_id: u.id }), '好友申请已发送')
          }
        >
          申请好友
        </button>
      )}
      {u.relationship === 'incoming' && (
        <>
          <button
            disabled={busy}
            onClick={() =>
              act(() => api(`/social/friends/${u.id}`, 'POST', { action: 'accept' }), '已成为好友')
            }
          >
            接受申请
          </button>
          <button
            className="secondary"
            disabled={busy}
            onClick={() => act(() => api(`/social/friends/${u.id}`, 'POST', { action: 'decline' }))}
          >
            拒绝
          </button>
        </>
      )}
      {u.relationship === 'outgoing' && (
        <button
          className="secondary"
          disabled={busy}
          onClick={() => act(() => api(`/social/friends/${u.id}`, 'POST', { action: 'cancel' }))}
        >
          撤回申请
        </button>
      )}
      {u.relationship === 'friend' && (
        <>
          <button
            disabled={busy}
            onClick={() => {
              setOpponent(u.id)
              setTab('duels')
            }}
          >
            邀请对战
          </button>
          <button
            className="secondary"
            disabled={busy}
            onClick={() =>
              act(
                () => api(`/social/friends/${u.id}`, 'POST', { action: 'cancel' }),
                '已解除好友关系',
              )
            }
          >
            解除好友
          </button>
        </>
      )}
    </div>
  )
  if (available === false)
    return (
      <section className="card">
        <h2>一起练习，更有动力</h2>
        <p>好友、公开榜单与对战需要开启多人用户模式。</p>
      </section>
    )
  return (
    <div className="social-page">
      <header className="page-head">
        <div>
          <p className="eyebrow">LEARN TOGETHER</p>
          <h1>一起练，一起进步</h1>
          <p>每日小进步，好友来见证。公开范围由你决定。</p>
        </div>
        <button className="secondary" disabled={busy} onClick={() => act(async () => {})}>
          刷新数据
        </button>
      </header>
      <nav className="social-tabs" aria-label="社区功能">
        {[
          ['leaderboard', '每日做题榜'],
          ['friends', '找朋友'],
          ['duels', '好友对战'],
          ['profile', '我的主页'],
        ].map(([id, label]) => (
          <button
            key={id}
            className={tab === id ? 'active' : 'secondary'}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="social-notice" role="status">
          {notice}
        </p>
      )}
      {!me ? (
        <p role="status">正在打开社区…</p>
      ) : (
        <>
          {!me.profile.leaderboard && (
            <div className="social-onboarding card">
              <strong>你的榜单目前仅自己掌握</strong>
              <p>先验证平台身份，再在“我的主页”开启公开榜单。不开启时，你仍能找朋友和对战。</p>
              <button className="secondary" onClick={() => setTab('profile')}>
                设置公开范围与平台身份
              </button>
            </div>
          )}
          {tab === 'leaderboard' && (
            <section className="card">
              <div className="section-head">
                <h2>今日每一题，都值得记录</h2>
                <label>
                  查看日期
                  <input
                    aria-label="榜单日期"
                    type="date"
                    max={today()}
                    value={day}
                    onChange={(e) => setDay(e.target.value || today())}
                  />
                </label>
              </div>
              <p className="help">{board?.rule}</p>
              {board?.items?.length ? (
                <ol className="social-ranking">
                  {board.items.map((u: any) => (
                    <li key={u.id}>
                      <span className="social-rank">{u.rank}</span>
                      <button className="secondary" onClick={() => openProfile(u.id)}>
                        {u.display_name}
                        {u.self ? ' · 我' : ''}
                      </button>
                      <strong>{u.count} 题</strong>
                    </li>
                  ))}
                </ol>
              ) : (
                <p>还没有用户开启公开榜单。成为第一位分享进步的人吧。</p>
              )}
              <p className="help">
                这里显示已同步的官方通过记录。分数与标签保留原平台口径；没有提供的数据会标为“未标定”。
              </p>
            </section>
          )}
          {tab === 'friends' && (
            <>
              <section className="card">
                <h2>找一位练习伙伴</h2>
                <form
                  className="actions"
                  onSubmit={(e) => {
                    e.preventDefault()
                    act(async () =>
                      setResults(
                        (await api(`/social/users?q=${encodeURIComponent(query.trim())}`)).items,
                      ),
                    )
                  }}
                >
                  <input
                    aria-label="检索用户"
                    placeholder="用户名或昵称"
                    value={query}
                    maxLength={40}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                  <button disabled={busy || !query.trim()}>搜索用户</button>
                </form>
                <div className="social-user-list">
                  {results.map((u) => (
                    <article key={u.id}>
                      <div>
                        <strong>{u.display_name}</strong>
                        <p>
                          @{u.username} · {relations[u.relationship]}
                        </p>
                      </div>
                      {friendButtons(u)}
                    </article>
                  ))}
                </div>
                {query && !results.length && (
                  <p className="help">输入完整或部分用户名，点击搜索。</p>
                )}
              </section>
              <section className="card">
                <h2>我的好友与申请</h2>
                <div className="social-user-list">
                  {friends.map((u) => (
                    <article key={u.id}>
                      <div>
                        <strong>{u.display_name}</strong>
                        <p>
                          @{u.username} · {relations[u.relationship]}
                        </p>
                      </div>
                      {friendButtons(u)}
                    </article>
                  ))}
                </div>
                {!friends.length && <p>还没有好友，搜索用户名发送第一份邀请。</p>}
              </section>
            </>
          )}
          {tab === 'profile' && form && (
            <>
              <section className="card">
                <h2>我的个人主页</h2>
                <form
                  onSubmit={(e) => {
                    e.preventDefault()
                    act(
                      () =>
                        api('/social/me', 'PUT', {
                          display_name: form.display_name,
                          bio: form.bio,
                          public_profile: form.public_profile,
                          leaderboard: form.leaderboard,
                        }),
                      '主页设置已保存',
                    )
                  }}
                >
                  <label>
                    昵称
                    <input
                      maxLength={40}
                      value={form.display_name}
                      onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                    />
                  </label>
                  <label>
                    关于我
                    <textarea
                      maxLength={500}
                      rows={3}
                      value={form.bio}
                      onChange={(e) => setForm({ ...form, bio: e.target.value })}
                    />
                  </label>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={form.public_profile}
                      onChange={(e) => setForm({ ...form, public_profile: e.target.checked })}
                    />
                    公开主页与当日做题详情（站内登录用户可见，好友始终可见）
                  </label>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={form.leaderboard}
                      onChange={(e) => setForm({ ...form, leaderboard: e.target.checked })}
                    />
                    加入每日公开榜单，并公开榜单题目详情
                  </label>
                  <p className="help">
                    用户名和昵称可被站内搜索。Cookie、源码、草稿、AI 对话始终不会出现在主页和榜单。
                  </p>
                  <button disabled={busy}>保存主页设置</button>
                </form>
              </section>
              <section className="card">
                <h2>用于榜单与对战的平台身份</h2>
                <p>
                  先在“连接与设置”保存 Cookie
                  并点击“检测登录状态”，成功后在这里认领。每个平台账号仅能认领到一个站内用户；认领证明身份归属，不代表登录状态永久有效；实际登录状态仍以设置页检测为准。修改
                  Cookie 后需重新检测认领。
                </p>
                <div className="social-user-list">
                  {me.bindings.map((a: any) => {
                    const claimed = me.accounts.some(
                      (v: any) => v.platform === a.platform && v.handle === a.handle,
                    )
                    return (
                      <article key={`${a.platform}:${a.handle}`}>
                        <div>
                          <strong>
                            {names[a.platform]} · {a.handle}
                          </strong>
                          <p>
                            {claimed ? '身份已认领（历史身份验证）' : '尚未认领，不计入公开榜单'}
                          </p>
                        </div>
                        <button
                          className="secondary"
                          disabled={busy}
                          onClick={() =>
                            act(
                              () =>
                                claimed
                                  ? api(
                                      `/social/accounts/${a.platform}/${encodeURIComponent(a.handle)}`,
                                      'DELETE',
                                      {},
                                    )
                                  : api('/social/accounts', 'POST', a),
                              claimed ? '已解除认领' : '平台身份已认领',
                            )
                          }
                        >
                          {claimed ? '解除认领' : '检测成功后认领'}
                        </button>
                      </article>
                    )
                  })}
                </div>
                {!me.bindings.length && <p>请先到“连接与设置”绑定平台账号。</p>}
                <p className="help">对战双方各使用一个已验证 CF 身份，防止冒用他人账号。</p>
              </section>
            </>
          )}
          {tab === 'duels' && (
            <>
              <section className="card">
                <h2>向好友发起一场挑战</h2>
                <p>
                  双方同意精确 CF 难度和时长；接受后从官方题库随机抽取双方从未尝试的题目。按官方 AC
                  提交时间竞速，同秒 AC 为平局。结束前保留至少 30 秒判题确认时间。
                </p>
                <form
                  className="actions"
                  onSubmit={(e) => {
                    e.preventDefault()
                    act(
                      () =>
                        api('/social/duels', 'POST', {
                          user_id: opponent,
                          difficulty,
                          duration_minutes: minutes,
                        }),
                      '邀请已发送，对方接受后才会抽题并开始',
                    )
                  }}
                >
                  <label>
                    好友
                    <select
                      aria-label="对战好友"
                      value={opponent}
                      onChange={(e) => setOpponent(e.target.value)}
                    >
                      <option value="">选择好友</option>
                      {friends
                        .filter((u) => u.relationship === 'friend')
                        .map((u) => (
                          <option value={u.id} key={u.id}>
                            {u.display_name}
                          </option>
                        ))}
                    </select>
                  </label>
                  <label>
                    CF 难度
                    <input
                      aria-label="对战难度"
                      type="number"
                      min={800}
                      max={3500}
                      step={100}
                      value={difficulty}
                      onChange={(e) => setDifficulty(Number(e.target.value))}
                    />
                  </label>
                  <label>
                    对战时长（分钟）
                    <input
                      type="number"
                      min={5}
                      max={180}
                      value={minutes}
                      onChange={(e) => setMinutes(Number(e.target.value))}
                    />
                  </label>
                  <button disabled={busy || !opponent}>发出邀请</button>
                </form>
                <p className="help">
                  对战 AC 计入每日榜单与普通签到，不抵扣每日任务，不升级 K 签。邀请 24
                  小时有效；每人同一时间仅一场待处理或进行中对战。
                </p>
              </section>
              <div className="social-duel-list">
                {duels.map((d) => (
                  <article className={`card social-duel ${d.status}`} key={d.id}>
                    <div className="section-head">
                      <h3>
                        {d.sender} <span>VS</span> {d.target}
                      </h3>
                      <strong>{duelStatuses[d.status] || d.status}</strong>
                    </div>
                    <p>
                      CF {d.difficulty} · {d.duration_minutes} 分钟
                      {d.starts_at ? ` · 开始 ${date(d.starts_at)} · 截止 ${date(d.ends_at)}` : ''}
                    </p>
                    {d.problem && (
                      <div className="social-duel-problem">
                        <strong>{d.problem.title}</strong>
                        <p>
                          {d.problem.id} · {d.problem.difficulty} · {d.problem.tags?.join(' / ')}
                        </p>
                        <button onClick={() => onOpenProblem(d.problem.id)}>
                          在工作台打开对战题
                        </button>
                        <a href={d.problem.url} target="_blank" rel="noreferrer">
                          前往 CF 官方题目 / 交题
                        </a>
                      </div>
                    )}
                    {d.error && <p className="help">{d.error}</p>}
                    {d.status === 'pending' && (
                      <div className="actions">
                        {!d.self_is_sender && (
                          <button
                            disabled={busy}
                            onClick={() =>
                              act(
                                () =>
                                  api(`/social/duels/${d.id}/action`, 'POST', { action: 'accept' }),
                                '官方题目已核验，对战开始',
                              )
                            }
                          >
                            接受并随机抽题
                          </button>
                        )}
                        <button
                          className="secondary"
                          disabled={busy}
                          onClick={() =>
                            act(() =>
                              api(`/social/duels/${d.id}/action`, 'POST', {
                                action: d.self_is_sender ? 'cancel' : 'decline',
                              }),
                            )
                          }
                        >
                          {d.self_is_sender ? '撤回邀请' : '拒绝邀请'}
                        </button>
                      </div>
                    )}
                    {d.status === 'active' && (
                      <>
                        <button
                          disabled={busy}
                          onClick={() =>
                            act(
                              () => api(`/social/duels/${d.id}/check`, 'POST', {}),
                              '已核验官方提交；可能仍在等待判题或 30 秒确认期',
                            )
                          }
                        >
                          核验双方官方提交
                        </button>
                        <p className="help">
                          当前通过官方页交题，工作台可关联并同步。这里每 20
                          秒最多核验一次；后台不会自动代交。页面刷新只读取对战状态。
                        </p>
                      </>
                    )}
                    {d.sender_ac && (
                      <p>
                        {d.sender} 首次 AC：{date(d.sender_ac)}
                      </p>
                    )}
                    {d.target_ac && (
                      <p>
                        {d.target} 首次 AC：{date(d.target_ac)}
                      </p>
                    )}
                    {d.winner_id && (
                      <p className="social-victory">
                        🏆{' '}
                        {d.winner_id === me.profile.id
                          ? '你赢下了这一场！'
                          : `${d.winner_id === d.sender_id ? d.sender : d.target} 获胜`}
                      </p>
                    )}
                  </article>
                ))}
                {!duels.length && <p className="card">还没有对战记录，邀请好友开始第一场。</p>}
              </div>
            </>
          )}
        </>
      )}
      {profile && (
        <section className="card social-profile-detail" aria-label="用户主页" tabIndex={-1}>
          <div className="section-head">
            <h2>{profile.profile.display_name} 的主页</h2>
            <button className="secondary" onClick={() => setProfile(null)}>
              关闭主页
            </button>
          </div>
          <p>
            @{profile.profile.username} · {relations[profile.relationship]}
          </p>
          <p>{profile.profile.bio || '这位伙伴还没有填写简介。'}</p>
          {profile.relationship !== 'self' &&
            friendButtons({ ...profile.profile, relationship: profile.relationship }, false)}
          <h3>
            {profile.day} · {profile.items.length} 道已通过题目
          </h3>
          <div className="social-user-list">
            {profile.items.map((p: any) => (
              <article key={p.id}>
                <div>
                  {p.url ? (
                    <a href={p.url} target="_blank" rel="noreferrer">
                      {p.title}
                    </a>
                  ) : (
                    <strong>{p.title}</strong>
                  )}
                  <p>
                    {names[p.platform]} · {p.problem_id} · 难度 {p.difficulty ?? '未标定'}
                    {p.score != null ? ` · 得分 ${p.score}` : ''}
                  </p>
                  <p className="help">{p.tags.length ? p.tags.join(' / ') : '原平台未提供标签'}</p>
                </div>
                <button className="secondary" onClick={() => onOpenProblem(p.id)}>
                  去工作台查看
                </button>
              </article>
            ))}
          </div>
          {!profile.items.length && <p>当日还没有已同步、且来自已认领身份的官方 AC。</p>}
        </section>
      )}
    </div>
  )
}

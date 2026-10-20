import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import './nowcoder-qr-login.css'

type Login = {
  login_id: string
  status: 'waiting' | 'authorizing' | 'verified' | 'expired' | 'failed' | 'cancelled'
  qr_image?: string
  expires_at?: number
  message?: string
  poll_after_seconds?: number
  verified_uid?: string
}
type Props = { handles?: string[]; onBound: () => void | Promise<void> }

export function NowcoderQrLogin({ handles = [], onBound }: Props) {
  const [consent, setConsent] = useState(false)
  const [expected, setExpected] = useState('')
  const [session, setSession] = useState<Login | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef(0)
  const active = useRef<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const bound = useRef(onBound)
  bound.current = onBound
  function clearTimer() {
    if (timer.current !== null) clearTimeout(timer.current)
    timer.current = null
  }
  function discard(id: string) {
    return api('/platform-login/nowcoder/cancel', 'POST', { login_id: id })
  }
  useEffect(
    () => () => {
      ++generation.current
      clearTimer()
      if (active.current) void discard(active.current).catch(() => {})
      active.current = null
    },
    [],
  )

  async function cancel() {
    const epoch = ++generation.current
    clearTimer()
    const id = active.current
    active.current = null
    setBusy(false)
    setSession({ login_id: '', status: 'cancelled', message: '已取消本次扫码，二维码已清除。' })
    setError('')
    if (id) {
      try {
        await discard(id)
      } catch {
        if (generation.current === epoch)
          setError('取消请求未完成，请勿继续扫描旧二维码；可稍后重试。')
      }
    }
  }
  function schedule(login: Login, epoch: number) {
    clearTimer()
    const expires = login.expires_at ?? 0
    timer.current = setTimeout(
      async () => {
        if (generation.current !== epoch) return
        if (expires && Date.now() >= expires * 1000) {
          active.current = null
          setSession({
            ...login,
            qr_image: undefined,
            status: 'expired',
            message: '二维码已过期，请刷新二维码。',
          })
          void discard(login.login_id).catch(() => {})
          return
        }
        try {
          const result = await api<Login>('/platform-login/nowcoder/poll', 'POST', {
            login_id: login.login_id,
          })
          if (generation.current !== epoch) return
          if (result.status === 'waiting' || result.status === 'authorizing') {
            const next = {
              ...login,
              ...result,
              qr_image: login.qr_image,
              expires_at: login.expires_at,
            }
            setSession(next)
            schedule(next, epoch)
          } else {
            active.current = null
            setSession({ ...result, qr_image: undefined })
            if (result.status === 'verified') {
              try {
                await bound.current()
              } catch {
                if (generation.current === epoch)
                  setError('牛客身份已验证，但账号列表刷新失败，请重新打开连接与设置。')
              }
            }
          }
        } catch {
          if (generation.current !== epoch) return
          active.current = null
          setSession({
            login_id: login.login_id,
            status: 'failed',
            message: '扫码状态检测失败，请重新申请二维码。',
          })
          void discard(login.login_id).catch(() => {})
        }
      },
      Math.max(3000, (login.poll_after_seconds ?? 3) * 1000),
    )
  }
  async function start() {
    if (!consent || busy) return
    const epoch = ++generation.current
    clearTimer()
    const previous = active.current
    active.current = null
    setSession(null)
    setError('')
    setBusy(true)
    try {
      if (previous) await discard(previous)
      if (generation.current !== epoch) return
      const result = await api<Login>('/platform-login/nowcoder/start', 'POST', {
        consent: true,
        ...(expected ? { expected_handle: expected } : {}),
      })
      if (generation.current !== epoch) {
        void discard(result.login_id).catch(() => {})
        return
      }
      if (
        result.status !== 'waiting' ||
        !result.login_id ||
        !/^data:image\/(?:png|jpeg);base64,[A-Za-z0-9+/=]+$/.test(result.qr_image ?? '')
      ) {
        if (result.login_id) void discard(result.login_id).catch(() => {})
        throw new Error('invalid response')
      }
      active.current = result.login_id
      setSession(result)
      schedule(result, epoch)
    } catch (error: unknown) {
      if (generation.current === epoch) {
        // api() exposes only the backend's safe JSON detail, never the upstream response.
        const reason =
          error instanceof Error && error.message && error.message !== 'invalid response'
            ? error.message
            : '无法申请牛客二维码，请稍后重试。'
        setError(`${reason} 也可使用下方 Cookie 绑定。`)
      }
    } finally {
      if (generation.current === epoch) setBusy(false)
    }
  }
  const waiting = session?.status === 'waiting' || session?.status === 'authorizing'
  return (
    <section className="nowcoder-qr-login" aria-label="牛客微信扫码登录">
      <h4>微信扫码绑定牛客</h4>
      <p className="help">
        通过牛客官方微信登录入口，在手机上确认授权，无需填写牛客密码。二维码约 2
        分钟有效，只能本人扫描。扫码后还需验证真实牛客 UID，验证通过才会绑定。
      </p>
      <label>
        绑定目标
        <select
          value={expected}
          disabled={busy || waiting}
          onChange={(e) => setExpected(e.target.value)}
        >
          <option value="">自动识别我的牛客 UID</option>
          {handles.map((handle) => (
            <option key={handle} value={handle}>
              仅绑定已有 UID：{handle}
            </option>
          ))}
        </select>
      </label>
      <label className="nowcoder-qr-consent">
        <input
          type="checkbox"
          checked={consent}
          disabled={busy || waiting}
          onChange={(e) => setConsent(e.target.checked)}
        />
        我确认由本人微信扫码，并同意将牛客登录会话保存在我当前的 ACM Lab 账户中。
      </label>
      <div className="nowcoder-qr-actions">
        <button disabled={!consent || busy} onClick={() => void start()}>
          {busy ? '正在申请二维码…' : session ? '刷新二维码' : '获取微信登录二维码'}
        </button>
        {(busy || waiting) && (
          <button className="secondary" onClick={() => void cancel()}>
            取消扫码登录
          </button>
        )}
      </div>
      {session?.qr_image && waiting && (
        <img
          className="nowcoder-qr-image"
          src={session.qr_image}
          alt="牛客官方微信登录二维码，请本人用微信扫一扫"
        />
      )}
      {session && (
        <p role="status" aria-live="polite">
          {session.status === 'verified'
            ? `牛客身份验证通过，已绑定 UID：${session.verified_uid ?? '请查看账号列表'}。`
            : session.message ||
              (session.status === 'authorizing'
                ? '微信已确认，正在验证牛客身份…'
                : session.status === 'waiting'
                  ? '等待本人微信扫码确认…'
                  : session.status === 'expired'
                    ? '二维码已过期，请刷新二维码。'
                    : '本次登录未完成，请重新获取二维码。')}
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      <p className="help">
        不使用微信？仍可在牛客官网登录后，通过油猴助手或 F12 获取 Cookie，在下方手动绑定并检测。
      </p>
    </section>
  )
}

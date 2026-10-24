import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from './api'
import './nowcoder-qr-login.css'

type Login = {
  login_id: string
  status: 'waiting' | 'authorizing' | 'verified' | 'expired' | 'failed' | 'cancelled'
  qr_image?: string
  expires_at?: number
  message?: string
  poll_after_seconds?: number
  verified_uid?: string
  retryable?: boolean
}
type Props = { handles?: string[]; onBound: () => void | Promise<void> }

export function NowcoderQrLogin({ handles = [], onBound }: Props) {
  const [expected, setExpected] = useState('')
  const [session, setSession] = useState<Login | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef(0)
  const active = useRef<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const deadlineTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const bound = useRef(onBound)
  bound.current = onBound
  function clearTimer() {
    if (timer.current !== null) clearTimeout(timer.current)
    timer.current = null
  }
  function clearDeadline() {
    if (deadlineTimer.current !== null) clearTimeout(deadlineTimer.current)
    deadlineTimer.current = null
  }
  function discard(id: string) {
    return api('/platform-login/nowcoder/cancel', 'POST', { login_id: id })
  }
  useEffect(
    () => () => {
      ++generation.current
      clearTimer()
      clearDeadline()
      if (active.current) void discard(active.current).catch(() => {})
      active.current = null
    },
    [],
  )

  async function cancel() {
    const epoch = ++generation.current
    clearTimer()
    clearDeadline()
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
  function expire(login: Login, epoch: number) {
    if (generation.current !== epoch) return
    // Also invalidate an in-flight poll: a late success must not revive an expired flow.
    ++generation.current
    clearTimer()
    clearDeadline()
    active.current = null
    setSession({
      ...login,
      qr_image: undefined,
      status: 'expired',
      message: '二维码已过期，请刷新二维码。',
    })
    void discard(login.login_id).catch(() => {})
  }
  function schedule(login: Login, epoch: number, retryAfterSeconds = 0) {
    clearTimer()
    const expires = login.expires_at ?? 0
    const delay = Math.max(3000, (login.poll_after_seconds ?? 3) * 1000, retryAfterSeconds * 1000)
    timer.current = setTimeout(
      async () => {
        if (generation.current !== epoch) return
        if (expires && Date.now() >= expires * 1000) {
          expire(login, epoch)
          return
        }
        try {
          const result = await api<Login>('/platform-login/nowcoder/poll', 'POST', {
            login_id: login.login_id,
          })
          if (generation.current !== epoch) return
          if (expires && Date.now() >= expires * 1000) {
            expire(login, epoch)
            return
          }
          if (
            !result || typeof result !== 'object' ||
            result.login_id !== login.login_id ||
            !['waiting', 'authorizing', 'verified', 'expired', 'failed', 'cancelled'].includes(result.status) ||
            (result.status === 'verified' && !/^[1-9][0-9]{0,19}$/.test(result.verified_uid ?? ''))
          ) throw new Error('扫码结果缺少有效身份或流程不匹配，尚未确认绑定；请刷新账号列表后重试。')
          if (result.status === 'waiting' || result.status === 'authorizing') {
            const next = {
              ...login,
              ...result,
              qr_image: login.qr_image,
              expires_at: login.expires_at,
              message: result.retryable
                ? result.message
                  ? `${result.message} 正在重试，无需重新扫码。`
                  : '检测暂时失败，正在重试，无需重新扫码。'
                : result.message || (result.status === 'authorizing'
                  ? '微信已确认，正在验证牛客身份…'
                  : '等待本人微信扫码确认…'),
            }
            setSession(next)
            schedule(next, epoch)
          } else {
            clearDeadline()
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
        } catch (error: unknown) {
          if (generation.current !== epoch) return
          if (expires && Date.now() >= expires * 1000) {
            expire(login, epoch)
            return
          }
          const retryable = error instanceof ApiError
            ? error.status === 408 || error.status === 429 || (error.status >= 500 && error.status <= 599)
            : error instanceof TypeError || (error instanceof DOMException && error.name === 'NetworkError')
          if (retryable) {
            const next = {
              ...login,
              message: '检测暂时失败，正在重试，无需重新扫码。',
            }
            setSession(next)
            schedule(next, epoch, error instanceof ApiError ? error.retryAfterSeconds : undefined)
            return
          }
          clearDeadline()
          active.current = null
          setSession({
            login_id: login.login_id,
            status: 'failed',
            // api() only exposes the backend's safe JSON detail. Keep reasons
            // such as configuration conflicts and revoked site sessions visible.
            message: error instanceof Error && error.message
              ? error.message
              : '扫码状态检测失败，请重新申请二维码。',
          })
          void discard(login.login_id).catch(() => {})
        }
      },
      expires ? Math.min(delay, Math.max(0, expires * 1000 - Date.now())) : delay,
    )
  }
  async function start() {
    if (busy) return
    const epoch = ++generation.current
    clearTimer()
    clearDeadline()
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
      if (result.status === 'expired' && result.login_id) {
        setSession({ ...result, qr_image: undefined })
        return
      }
      if (
        result.status !== 'waiting' ||
        !result.login_id ||
        !Number.isFinite(result.expires_at) || (result.expires_at ?? 0) <= 0 ||
        !/^data:image\/(?:png|jpeg);base64,[A-Za-z0-9+/=]+$/.test(result.qr_image ?? '')
      ) {
        if (result.login_id) void discard(result.login_id).catch(() => {})
        throw new Error('invalid response')
      }
      active.current = result.login_id
      setSession(result)
      // Keep the original server deadline even while a network request hangs.
      deadlineTimer.current = setTimeout(
        () => expire(result, epoch),
        Math.max(0, result.expires_at! * 1000 - Date.now()),
      )
      schedule(result, epoch)
    } catch (error: unknown) {
      if (generation.current === epoch) {
        // api() exposes only the backend's safe JSON detail, never the upstream response.
        const reason =
          error instanceof Error && error.message && error.message !== 'invalid response'
            ? error.message
            : '无法申请牛客二维码，请稍后重试。'
        setError(`${reason} 也可切换上方“导入浏览器 Cookie”。`)
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
        请先在牛客官方账号设置中关联本人微信，再通过官方微信登录入口扫码确认授权，无需填写牛客密码。二维码约 2
        分钟有效，只能本人扫描。扫码后自动验证真实牛客 UID 并保存登录会话，无需再导入 Cookie。
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
      <p className="help">点击获取即申请本人扫码登录，验证后保存会话至当前账户。</p>
      <div className="nowcoder-qr-actions">
        <button disabled={busy} onClick={() => void start()}>
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
            ? `牛客身份验证通过，已绑定 UID：${session.verified_uid}。登录会话已保存，无需再次导入 Cookie。`
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
        扫码登录与 Cookie
        导入是两种独立的绑定入口，任选其一即可。扫码会自动保存本次官方登录会话，无需手动复制或再导入 Cookie；不使用微信时，可切换上方“导入浏览器 Cookie”。
      </p>
    </section>
  )
}

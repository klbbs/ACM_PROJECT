import { useEffect, useId, useRef, useState, type FormEvent, type MouseEvent } from 'react'
import { api } from './api'
import './browser-platform-login.css'

type Platform = 'codeforces' | 'atcoder' | 'luogu'
type LoginAccount = { handle?: string; [key: string]: unknown }
type LoginResponse = {
  login_id?: string
  status?: string
  message?: string
  width?: number
  height?: number
  expires_in?: number
  account?: LoginAccount
}
type Session = { id: string; generation: number; width: number; height: number; expiresAt: number }
const names: Record<Platform, string> = {
  codeforces: 'Codeforces',
  atcoder: 'AtCoder',
  luogu: '洛谷',
}
const route = '/platform-login/browser'

export function BrowserPlatformLogin({
  platform,
  onBound,
}: {
  platform: Platform
  onBound: (account?: LoginAccount) => void | Promise<void>
}) {
  const fieldId = useId()
  const [expected, setExpected] = useState('')
  const [session, setSession] = useState<Session | null>(null)
  const [cancelTarget, setCancelTarget] = useState<Session | null>(null)
  const [image, setImage] = useState('')
  const [text, setText] = useState('')
  const [masked, setMasked] = useState(true)
  const [zoomed, setZoomed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const generation = useRef(0)
  const current = useRef<Session | null>(null)
  const mounted = useRef(true)
  const submitting = useRef(false)
  const imageRequest = useRef(0)
  const pendingStart = useRef<{ promise: Promise<LoginResponse>; cancelRequested: boolean } | null>(
    null,
  )
  const bound = useRef(onBound)
  bound.current = onBound
  const alive = (version: number) => mounted.current && generation.current === version
  const discard = (id: string) =>
    void api(`${route}/cancel`, 'POST', { login_id: id }).catch(() => {})

  useEffect(() => {
    mounted.current = true
    submitting.current = false
    setSession(null)
    setCancelTarget(null)
    setImage('')
    setText('')
    setStatus('')
    setMessage('')
    setError('')
    setBusy(false)
    return () => {
      mounted.current = false
      generation.current++
      const previous = current.current
      current.current = null
      if (previous) discard(previous.id)
    }
  }, [platform])

  const clearSession = (nextStatus: string, nextMessage: string, cancelRemote = true) => {
    const previous = current.current
    generation.current++
    current.current = null
    submitting.current = false
    setSession(null)
    setCancelTarget(null)
    setImage('')
    setText('')
    setMasked(true)
    setZoomed(false)
    setBusy(false)
    setStatus(nextStatus)
    setMessage(nextMessage)
    setError('')
    if (previous && cancelRemote) discard(previous.id)
  }

  async function cancelExplicitly() {
    if (status === 'cancelling') return
    const pending = pendingStart.current
    if (pending) pending.cancelRequested = true
    const previous = current.current || cancelTarget
    const version = ++generation.current
    let target = previous ? { ...previous, generation: version } : null
    current.current = target
    setSession(null)
    setCancelTarget(target)
    setImage('')
    setText('')
    setMasked(true)
    setZoomed(false)
    submitting.current = true
    setBusy(true)
    setStatus('cancelling')
    setMessage('正在取消安全登录并关闭服务器窗口…')
    setError('')
    try {
      if (!target && pending) {
        const response = await pending.promise
        if (!alive(version)) {
          if (response.login_id) discard(response.login_id)
          return
        }
        if (response.login_id) {
          target = {
            id: response.login_id,
            generation: version,
            width: response.width || 1000,
            height: response.height || 720,
            expiresAt: Date.now() + Math.max(0, response.expires_in ?? 300) * 1000,
          }
          current.current = target
          setCancelTarget(target)
        }
      }
      if (target) await api(`${route}/cancel`, 'POST', { login_id: target.id })
      if (!alive(version)) return
      current.current = null
      setCancelTarget(null)
      setStatus('cancelled')
      setMessage('本次安全登录已取消，服务器窗口已关闭。')
    } catch (cause) {
      if (!alive(version)) return
      setStatus('cancel_failed')
      setMessage('尚未确认服务器窗口已关闭。请重试取消，或等待本次窗口的有效期结束后重新打开。')
      setError(cause instanceof Error ? cause.message : '取消请求未完成，请重试。')
    } finally {
      if (alive(version)) {
        submitting.current = false
        setBusy(false)
      }
    }
  }

  useEffect(() => {
    if (!cancelTarget) return
    const timer = setTimeout(
      () => {
        if (alive(cancelTarget.generation))
          clearSession('expired', '本次窗口已超过有效期；可重新打开安全登录窗口。')
      },
      Math.max(0, cancelTarget.expiresAt - Date.now()),
    )
    return () => clearTimeout(timer)
  }, [cancelTarget])

  async function acceptResult(response: LoginResponse, version: number) {
    if (!alive(version)) return true
    if (response.status === 'authenticated') {
      if (!response.account?.handle) throw new Error('服务器未返回已验证的账号，请重新检测登录。')
      clearSession(
        'authenticated',
        `官方身份已验证，已绑定账号：${response.account.handle}。`,
        false,
      )
      const finishedVersion = generation.current
      try {
        await bound.current(response.account)
      } catch {
        if (alive(finishedVersion)) setError('账号已绑定，但列表刷新失败，请重新打开连接与设置。')
      }
      return true
    }
    if (['expired', 'unavailable', 'failed', 'cancelled'].includes(response.status || '')) {
      const state = response.status!
      clearSession(
        state,
        state === 'expired'
          ? '安全登录窗口已过期，尚未绑定账号。请重新打开窗口。'
          : state === 'unavailable'
            ? '服务器暂时无法打开安全登录窗口，尚未绑定账号。可稍后重试或使用 Cookie 绑定。'
            : state === 'cancelled'
              ? '本次安全登录已取消。'
              : '本次登录未完成，尚未绑定账号。请重新打开窗口。',
      )
      if (response.message) setError(response.message)
      return true
    }
    const active = current.current
    if (
      response.status === 'human_required' &&
      active?.generation === version &&
      typeof response.expires_in === 'number' &&
      Number.isFinite(response.expires_in)
    ) {
      const refreshed = {
        ...active,
        expiresAt: Date.now() + Math.max(0, response.expires_in) * 1000,
      }
      current.current = refreshed
      setSession(refreshed)
    }
    if (response.message) setMessage(response.message)
    return false
  }

  async function refreshImage(active: Session) {
    const request = ++imageRequest.current
    const response = await api<{ image?: string }>(`${route}/image`, 'POST', {
      login_id: active.id,
    })
    if (!alive(active.generation) || request !== imageRequest.current) return
    if (!response.image || !/^data:image\/(png|jpeg|webp);base64,/.test(response.image))
      throw new Error('未能读取登录窗口截图，请点击刷新窗口重试。')
    setImage(response.image)
  }

  useEffect(() => {
    if (!session) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    const expire = setTimeout(
      () => {
        if (alive(session.generation))
          clearSession('expired', '安全登录窗口已过期，尚未绑定账号。请重新打开窗口。')
      },
      Math.max(0, session.expiresAt - Date.now()),
    )
    const poll = async () => {
      try {
        const response = await api<LoginResponse>(`${route}/status`, 'POST', {
          login_id: session.id,
        })
        if (cancelled || !alive(session.generation)) return
        if (await acceptResult(response, session.generation)) return
      } catch (cause) {
        if (cancelled || !alive(session.generation)) return
        setError(cause instanceof Error ? cause.message : '登录窗口状态读取失败，请手动刷新。')
      }
      if (!cancelled && alive(session.generation)) timer = setTimeout(poll, 15000)
    }
    timer = setTimeout(poll, 15000)
    return () => {
      cancelled = true
      clearTimeout(timer)
      clearTimeout(expire)
    }
  }, [session])

  // Official challenge pages may still be painting when the first screenshot arrives.
  // One delayed refresh catches that transition without continually polling images.
  useEffect(() => {
    if (!session) return
    const active = session
    const timer = setTimeout(async () => {
      if (!alive(active.generation) || submitting.current) return
      try {
        await refreshImage(active)
      } catch (cause) {
        if (alive(active.generation))
          setError(cause instanceof Error ? cause.message : '截图未能自动更新，请手动刷新窗口。')
      }
    }, 2000)
    return () => clearTimeout(timer)
  }, [session?.id])

  async function start() {
    if (submitting.current) return
    const target = expected.trim()
    if (
      target &&
      (target.length > 100 ||
        (platform === 'luogu' ? !/^\d+$/.test(target) : !/^[A-Za-z0-9_.-]+$/.test(target)))
    ) {
      setError(
        platform === 'luogu'
          ? '洛谷绑定目标请填写数字 UID。'
          : '绑定目标请填写有效的 handle / 用户名。',
      )
      return
    }
    const version = ++generation.current
    submitting.current = true
    setBusy(true)
    setError('')
    setImage('')
    setText('')
    setStatus('starting')
    setMessage('正在打开官方登录页面…')
    try {
      const request = {
        promise: api<LoginResponse>(`${route}/start`, 'POST', {
          platform,
          ...(target ? { expected_handle: target } : {}),
        }),
        cancelRequested: false,
      }
      pendingStart.current = request
      const response = await request.promise
      if (pendingStart.current === request) pendingStart.current = null
      if (!alive(version)) {
        if (response.login_id && !request.cancelRequested) discard(response.login_id)
        return
      }
      if (await acceptResult(response, version)) return
      if (!response.login_id || response.status !== 'human_required')
        throw new Error('服务器未建立安全登录窗口，请重试。')
      const active: Session = {
        id: response.login_id,
        generation: version,
        width: Number(response.width) > 0 ? Number(response.width) : 1000,
        height: Number(response.height) > 0 ? Number(response.height) : 720,
        expiresAt: Date.now() + Math.max(1, response.expires_in ?? 300) * 1000,
      }
      current.current = active
      setSession(active)
      setStatus('human_required')
      setMessage(response.message || '请在下方官方页面中登录，完成验证后点击“检测登录并绑定”。')
      await refreshImage(active)
    } catch (cause) {
      if (alive(version)) {
        setError(cause instanceof Error ? cause.message : '安全登录窗口暂时无法打开。')
        if (!current.current) {
          setStatus('unavailable')
          setMessage('安全登录窗口未能打开，尚未绑定账号。')
        }
      }
    } finally {
      if (alive(version)) {
        pendingStart.current = null
        submitting.current = false
        setBusy(false)
      }
    }
  }

  async function action(
    endpoint: 'input' | 'confirm' | 'image',
    payload: Record<string, unknown> = {},
  ) {
    const active = current.current
    if (!active || submitting.current) return
    submitting.current = true
    setBusy(true)
    setError('')
    try {
      if (endpoint === 'image') await refreshImage(active)
      else {
        const response = await api<LoginResponse>(`${route}/${endpoint}`, 'POST', {
          login_id: active.id,
          ...payload,
        })
        if (!alive(active.generation) || (await acceptResult(response, active.generation))) return
        await refreshImage(active)
      }
    } catch (cause) {
      if (alive(active.generation))
        setError(cause instanceof Error ? cause.message : '操作未完成，请重试。')
    } finally {
      // A typed secret is never retained in the request object after the request settles.
      if ('text' in payload) payload.text = ''
      if (alive(active.generation)) {
        submitting.current = false
        setBusy(false)
      }
    }
  }

  function clickImage(event: MouseEvent<HTMLImageElement>) {
    if (!session || busy) return
    const box = event.currentTarget.getBoundingClientRect()
    if (!box.width || !box.height) return
    const x = Math.min(
      session.width - 1,
      Math.max(0, Math.floor(((event.clientX - box.left) * session.width) / box.width)),
    )
    const y = Math.min(
      session.height - 1,
      Math.max(0, Math.floor(((event.clientY - box.top) * session.height) / box.height)),
    )
    void action('input', { kind: 'click', x, y })
  }

  function sendText(event: FormEvent) {
    event.preventDefault()
    if (!text || busy) return
    if (text.length > 512) {
      setError('每次最多输入 512 个字符，请缩短后重试。')
      return
    }
    const value = text
    setText('')
    void action('input', { kind: 'text', text: value })
  }

  return (
    <div className="browser-platform-login">
      <p className="help">
        在服务器的同一个官方登录窗口中操作，可亲自完成验证码。只有验证官方当前身份后才会保存会话并绑定账号。
      </p>
      {!session && !cancelTarget && status !== 'starting' && status !== 'cancelling' && (
        <div className="browser-login-start">
          <label htmlFor={`${fieldId}-expected`}>
            绑定目标{platform === 'luogu' ? ' UID' : ' handle / 用户名'}（可选）
          </label>
          <input
            id={`${fieldId}-expected`}
            value={expected}
            maxLength={100}
            inputMode={platform === 'luogu' ? 'numeric' : 'text'}
            autoComplete="off"
            spellCheck={false}
            placeholder={platform === 'luogu' ? '数字 UID；留空自动识别' : '留空使用官方验证的账号'}
            onChange={(event) => setExpected(event.target.value)}
          />
          <button type="button" className="primary" disabled={busy} onClick={() => void start()}>
            打开安全登录窗口
          </button>
        </div>
      )}
      {message && (
        <p role="status" aria-live="polite">
          {message}
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {session && (
        <div className="browser-login-session">
          <p className="help">
            点击图片中的输入框，再在下方发送文字。图片来自 {names[platform]}{' '}
            官方页面；可放大并横向滚动，输入和截图仅用于本次登录。
          </p>
          <button
            type="button"
            className="text-button"
            aria-pressed={zoomed}
            onClick={() => setZoomed(!zoomed)}
          >
            {zoomed ? '适应窗口宽度' : '放大登录页面'}
          </button>
          <div
            className="browser-login-viewport"
            tabIndex={0}
            aria-label={`${names[platform]} 官方登录页面，可横向滚动`}
          >
            {image ? (
              <img
                className={zoomed ? 'zoomed' : ''}
                src={image}
                alt={`${names[platform]} 官方登录窗口截图`}
                width={session.width}
                height={session.height}
                draggable={false}
                onClick={clickImage}
                style={zoomed ? { width: session.width } : undefined}
              />
            ) : (
              <p>正在读取登录页面；若没有显示，请刷新窗口。</p>
            )}
          </div>
          <form onSubmit={sendText} aria-label={`${names[platform]} 登录窗口输入`}>
            <label htmlFor={`${fieldId}-text`}>向选中位置输入</label>
            <input
              id={`${fieldId}-text`}
              type={masked ? 'password' : 'text'}
              value={text}
              autoComplete="off"
              maxLength={512}
              disabled={busy}
              onChange={(event) => setText(event.target.value)}
            />
            <div className="browser-login-controls">
              <button
                type="button"
                className="text-button"
                aria-pressed={!masked}
                onClick={() => setMasked(!masked)}
              >
                {masked ? '显示输入文字' : '隐藏输入文字'}
              </button>
              <button type="submit" disabled={busy || !text}>
                发送文字
              </button>
            </div>
          </form>
          <div className="browser-login-controls" role="group" aria-label="登录窗口按键">
            {['Tab', 'Enter', 'Backspace', 'Control+A'].map((key) => (
              <button
                key={key}
                type="button"
                disabled={busy}
                onClick={() => void action('input', { kind: 'key', key })}
              >
                {key}
              </button>
            ))}
          </div>
          <div className="browser-login-controls">
            <button type="button" disabled={busy} onClick={() => void action('image')}>
              刷新窗口截图
            </button>
            <button
              type="button"
              className="primary"
              disabled={busy}
              onClick={() => void action('confirm')}
            >
              {busy ? '正在处理…' : '检测登录并绑定'}
            </button>
          </div>
        </div>
      )}
      {(session || cancelTarget || status === 'starting' || status === 'cancelling') && (
        <button
          type="button"
          className="text-button"
          disabled={status === 'cancelling'}
          onClick={() => void cancelExplicitly()}
        >
          {status === 'cancelling'
            ? '正在取消安全登录…'
            : status === 'cancel_failed'
              ? '重试取消安全登录'
              : '取消安全登录'}
        </button>
      )}
    </div>
  )
}

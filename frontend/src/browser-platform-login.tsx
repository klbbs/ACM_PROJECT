import { useEffect, useId, useRef, useState, type FormEvent, type MouseEvent } from 'react'
import { api } from './api'
import './browser-platform-login.css'

type Platform = 'luogu'
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
  luogu: '洛谷',
}
const route = '/platform-login/browser'
// Keep the no-id recovery deadline aligned with BrowserLogin.MAX_SECONDS.
const maxSessionMs = 300_000
type PendingStart = { promise: Promise<LoginResponse>; cancelRequested: boolean; expiresAt: number }

export function BrowserPlatformLogin({
  platform,
  onBound,
}: {
  platform: Platform
  onBound: (account?: LoginAccount) => void | Promise<void>
}) {
  const fieldId = useId()
  const [expected, setExpected] = useState('')
  const [modalOpen, setModalOpen] = useState(false)
  const dialog = useRef<HTMLDialogElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const [session, setSession] = useState<Session | null>(null)
  const [cancelTarget, setCancelTarget] = useState<Session | null>(null)
  const [uncertainUntil, setUncertainUntil] = useState<number | null>(null)
  const [image, setImage] = useState('')
  const [text, setText] = useState('')
  const [masked, setMasked] = useState(true)
  const [zoomed, setZoomed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [retryAt, setRetryAt] = useState(0)
  const [retryRemaining, setRetryRemaining] = useState(0)
  const generation = useRef(0)
  const current = useRef<Session | null>(null)
  const mounted = useRef(true)
  const submitting = useRef(false)
  const cancelling = useRef(false)
  const dismissAfterCancel = useRef(false)
  const imageRequest = useRef(0)
  const pendingStart = useRef<PendingStart | null>(null)
  const bound = useRef(onBound)
  bound.current = onBound
  const alive = (version: number) => mounted.current && generation.current === version
  const discard = (id: string) =>
    void api(`${route}/cancel`, 'POST', { login_id: id }).catch(() => {})

  useEffect(() => {
    mounted.current = true
    submitting.current = false
    cancelling.current = false
    dismissAfterCancel.current = false
    setSession(null)
    setCancelTarget(null)
    setUncertainUntil(null)
    setImage('')
    setText('')
    setStatus('')
    setMessage('')
    setError('')
    setBusy(false)
    setModalOpen(false)
    return () => {
      mounted.current = false
      generation.current++
      const previous = current.current
      current.current = null
      pendingStart.current = null
      if (previous) discard(previous.id)
    }
  }, [platform])

  useEffect(() => {
    if (!retryAt) return
    const update = () => {
      const remaining = Math.max(0, Math.ceil((retryAt - Date.now()) / 1000))
      setRetryRemaining(remaining)
      return remaining
    }
    if (!update()) return
    const timer = setInterval(() => {
      if (!update()) clearInterval(timer)
    }, 1000)
    return () => clearInterval(timer)
  }, [retryAt])

  useEffect(() => {
    if (!modalOpen) return
    const element = dialog.current!
    if (!element.open) element.showModal()
    heading.current?.focus()
    const page = document.documentElement
    const previousOverflow = page.style.overflow
    const previousGutter = page.style.scrollbarGutter
    if (window.innerWidth > page.clientWidth) page.style.scrollbarGutter = 'stable'
    page.style.overflow = 'hidden'
    return () => {
      page.style.overflow = previousOverflow
      page.style.scrollbarGutter = previousGutter
      if (element.open) element.close()
      if (mounted.current) trigger.current?.focus()
    }
  }, [modalOpen])

  function closeWindow() {
    // Closing a live window must confirm remote cancellation before dismissing it.
    if (
      current.current ||
      pendingStart.current ||
      uncertainUntil ||
      status === 'starting' ||
      status === 'cancelling'
    ) {
      dismissAfterCancel.current = true
      void cancelExplicitly()
    } else {
      setModalOpen(false)
    }
  }

  const clearSession = (nextStatus: string, nextMessage: string, cancelRemote = true) => {
    const previous = current.current
    generation.current++
    current.current = null
    pendingStart.current = null
    submitting.current = false
    cancelling.current = false
    setSession(null)
    setCancelTarget(null)
    setUncertainUntil(null)
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
    if (cancelling.current) return
    if (uncertainUntil && !current.current && !pendingStart.current) return
    cancelling.current = true
    const pending = pendingStart.current
    if (pending) pending.cancelRequested = true
    const previous = current.current || cancelTarget
    const version = ++generation.current
    let target = previous ? { ...previous, generation: version } : null
    current.current = target
    setSession(null)
    setCancelTarget(target)
    if (!target && pending) setUncertainUntil(pending.expiresAt)
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
          setUncertainUntil(null)
          setCancelTarget(target)
        }
      }
      if (target) await api(`${route}/cancel`, 'POST', { login_id: target.id })
      if (!alive(version)) return
      current.current = null
      setCancelTarget(null)
      setUncertainUntil(null)
      setStatus('cancelled')
      setMessage('本次安全登录已取消，服务器窗口已关闭。')
      if (dismissAfterCancel.current) {
        dismissAfterCancel.current = false
        setModalOpen(false)
      }
    } catch (cause) {
      if (!alive(version)) return
      setStatus('cancel_failed')
      if (!target && pending) {
        // A transport failure can hide a server-created window. With no id we
        // cannot cancel it, so wait for the server's absolute maximum lifetime.
        setUncertainUntil(pending.expiresAt)
        setMessage(
          '启动响应中断，未取得窗口标识，尚未确认服务器窗口已关闭。请等待本次登录最长 5 分钟的有效期结束，届时会自动恢复。',
        )
      } else {
        setMessage('尚未确认服务器窗口已关闭。请重试取消，或等待本次窗口的有效期结束后重新打开。')
      }
      setError(cause instanceof Error ? cause.message : '取消请求未完成，请重试。')
    } finally {
      if (alive(version)) {
        submitting.current = false
        cancelling.current = false
        setBusy(false)
      }
    }
  }

  useEffect(() => {
    if (uncertainUntil === null) return
    const version = generation.current
    const timer = setTimeout(
      () => {
        if (alive(version))
          clearSession('expired', '本次登录已超过最长有效期；可重新打开安全登录窗口。')
      },
      Math.max(0, uncertainUntil - Date.now()),
    )
    return () => clearTimeout(timer)
  }, [uncertainUntil])

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
    if (
      submitting.current ||
      current.current ||
      cancelTarget ||
      uncertainUntil ||
      Date.now() < retryAt
    )
      return
    const target = expected.trim()
    if (target && (target.length > 100 || !/^\d+$/.test(target))) {
      setError('洛谷绑定目标请填写数字 UID。')
      return
    }
    dismissAfterCancel.current = false
    setModalOpen(true)
    heading.current?.focus()
    const version = ++generation.current
    submitting.current = true
    setBusy(true)
    setError('')
    setImage('')
    setText('')
    setStatus('starting')
    setMessage('正在打开官方登录页面…')
    let request: PendingStart | null = null
    try {
      request = {
        promise: api<LoginResponse>(`${route}/start`, 'POST', {
          platform,
          ...(target ? { expected_handle: target } : {}),
        }),
        cancelRequested: false,
        expiresAt: Date.now() + maxSessionMs,
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
        const detail =
          cause instanceof Error && cause.message
            ? cause.message
            : '安全登录窗口暂时无法打开，请稍后重试。'
        if (!current.current) {
          // Present the actual server explanation as the main result, including
          // service cooldowns, instead of obscuring it behind a generic failure.
          setStatus('unavailable')
          setMessage(detail)
          setError('')
          const failure = cause as { status?: number; retryAfterSeconds?: number } | null
          if (failure?.status === 429) {
            const seconds = failure.retryAfterSeconds
            const delay =
              typeof seconds === 'number' &&
              Number.isInteger(seconds) &&
              seconds >= 0 &&
              seconds <= 86400
                ? seconds
                : 10
            setRetryAt(Date.now() + delay * 1000)
            setRetryRemaining(delay)
          }
        } else {
          setError(detail)
        }
      }
    } finally {
      // Always release the settled promise, including when cancellation changed
      // the generation; an old response must never clear a newer start.
      if (pendingStart.current === request) pendingStart.current = null
      if (alive(version)) {
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
      <div className="browser-login-start">
        <label htmlFor={`${fieldId}-expected`}>绑定目标 UID（可选）</label>
        <input
          id={`${fieldId}-expected`}
          value={expected}
          maxLength={100}
          inputMode="numeric"
          autoComplete="off"
          spellCheck={false}
          disabled={modalOpen}
          placeholder="数字 UID；留空自动识别"
          onChange={(event) => setExpected(event.target.value)}
        />
        <button
          ref={trigger}
          type="button"
          className="primary"
          disabled={modalOpen || busy}
          aria-disabled={retryRemaining > 0 || undefined}
          onClick={() => void start()}
        >
          {modalOpen
            ? '安全登录窗口已打开'
            : retryRemaining > 0
              ? `请等待 ${retryRemaining} 秒后再打开`
              : '打开安全登录窗口'}
        </button>
      </div>
      {!modalOpen && message && <p role="status">{message}</p>}
      {!modalOpen && error && <p role="alert">{error}</p>}
      <dialog
        ref={dialog}
        className="browser-login-dialog"
        aria-labelledby={`${fieldId}-title`}
        aria-describedby={`${fieldId}-description`}
        onCancel={(event) => {
          event.preventDefault()
          closeWindow()
        }}
        onClose={() => {
          if (!mounted.current) return
          if (
            current.current ||
            pendingStart.current ||
            uncertainUntil ||
            status === 'starting' ||
            status === 'cancelling'
          ) {
            // Also guard against a programmatic/native close while a session is live.
            if (dialog.current && !dialog.current.open) dialog.current.showModal()
            dismissAfterCancel.current = true
            void cancelExplicitly()
          } else {
            setModalOpen(false)
          }
        }}
      >
        {modalOpen && (
          <>
            <div className="browser-login-dialog-header">
              <h2 id={`${fieldId}-title`} ref={heading} tabIndex={-1}>
                {names[platform]} 安全登录窗口
              </h2>
              <button
                type="button"
                className="text-button"
                aria-label="关闭安全登录窗口"
                disabled={status === 'cancelling'}
                onClick={closeWindow}
              >
                关闭
              </button>
            </div>
            <p id={`${fieldId}-description`} className="help">
              在本窗口完成官方登录和验证。关闭或按 Escape 会取消本次登录，并确认服务器窗口已关闭。
            </p>
            {message && (
              <p role="status" aria-live="polite">
                {message}
              </p>
            )}
            {error && <p role="alert">{error}</p>}
            {status === 'starting' && (
              <div className="browser-login-loading" aria-hidden="true">
                正在启动登录窗口…
              </div>
            )}
            {session && (
              <div className="browser-login-session">
                <p className="help">
                  下方是服务器上 {names[platform]} 官方页面的实时截图，可放大并横向滚动。
                  点击截图中的输入框，再在下方逐次发送文字；每次点击和发送文字都会立即传到该官方页面，输入仅用于本次登录。
                </p>
                <p className="help">
                  发送文字后，仍需点击截图中的官方登录按钮，或按下方
                  Enter。完成官方登录和验证后，再点“检测登录并绑定”。
                  页面仍在加载时，可点“刷新窗口截图”。
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
                  <label htmlFor={`${fieldId}-text`}>发送到官方页面当前选中的输入框</label>
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
            {(session ||
              cancelTarget ||
              status === 'starting' ||
              status === 'cancelling' ||
              status === 'cancel_failed') && (
              <button
                type="button"
                className="text-button"
                disabled={status === 'cancelling' || uncertainUntil !== null}
                onClick={() => void cancelExplicitly()}
              >
                {status === 'cancelling'
                  ? '正在取消安全登录…'
                  : uncertainUntil !== null
                    ? '等待窗口自动过期'
                    : status === 'cancel_failed'
                      ? '重试取消安全登录'
                      : '取消安全登录'}
              </button>
            )}
            {status === 'unavailable' && !session && !cancelTarget && !uncertainUntil && (
              <div className="browser-login-retry">
                {retryRemaining > 0 && (
                  <p id={`${fieldId}-retry`} className="help" aria-live="off">
                    请等待 {retryRemaining} 秒后重试。
                  </p>
                )}
                <button
                  type="button"
                  className="primary"
                  disabled={busy || retryRemaining > 0}
                  aria-describedby={retryRemaining > 0 ? `${fieldId}-retry` : undefined}
                  onClick={() => void start()}
                >
                  重试打开登录窗口
                </button>
              </div>
            )}
            {!session &&
              !cancelTarget &&
              status !== 'starting' &&
              status !== 'cancelling' &&
              status !== 'cancel_failed' && (
                <button type="button" className="primary" onClick={closeWindow}>
                  返回账号卡片
                </button>
              )}
          </>
        )}
      </dialog>
    </div>
  )
}

import { useId, useRef, useState, type FormEvent } from 'react'
import { api } from './api'
import './platform-password-login.css'

type Platform = 'codeforces' | 'atcoder'
type LoginResult = {
  status: 'authenticated' | 'blocked' | 'rejected' | 'unknown'
  reason?: string
  message?: string
  official_url?: string
  account?: { handle?: string }
}
type Props = { platform: Platform; onBound: () => void | Promise<void> }
const officialPages: Record<Platform, string> = {
  codeforces: 'https://codeforces.com/enter',
  atcoder: 'https://atcoder.jp/login',
}
const platformNames: Record<Platform, string> = { codeforces: 'Codeforces', atcoder: 'AtCoder' }

export function PlatformPasswordLogin({ platform, onBound }: Props) {
  const id = useId()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [expected, setExpected] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<LoginResult | null>(null)
  const [error, setError] = useState('')
  const submitting = useRef(false)

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting.current) return
    if (!username.trim() || !password.trim()) {
      setError('请填写账号和密码，不能仅包含空白。')
      return
    }
    submitting.current = true
    setBusy(true)
    setError('')
    setResult(null)
    // The password exists only for this request; clear the form before awaiting the response.
    const request = {
      platform,
      username: username.trim(),
      password,
      ...(expected.trim() ? { expected_handle: expected.trim() } : {}),
    }
    setPassword('')
    try {
      const response = await api<LoginResult>('/platform-login/password', 'POST', request)
      if (!['authenticated', 'blocked', 'rejected', 'unknown'].includes(response.status))
        throw new Error('invalid response')
      setResult(response)
      if (response.status === 'authenticated') {
        try {
          await onBound()
        } catch {
          setError('平台身份已验证，但账号列表刷新失败，请重新打开连接与设置。')
        }
      }
    } catch {
      setError(
        '本次登录请求未完成，当前服务器尚未验证登录。请稍后重试或在官网登录后使用 Cookie 绑定。',
      )
    } finally {
      request.password = ''
      setPassword('')
      setBusy(false)
      submitting.current = false
    }
  }

  return (
    <details className="platform-password-login">
      <summary>账号密码登录</summary>
      <p className="help">
        密码仅本次发送至 {platformNames[platform]} 官方，本站不保存。 可靠验证身份后才保存 Cookie
        并绑定。
      </p>
      <form
        onSubmit={(event) => void login(event)}
        aria-label={`${platformNames[platform]} 账号密码登录`}
      >
        <label htmlFor={`${id}-username`}>
          {platform === 'codeforces' ? '账号（handle 或邮箱）' : '账号（用户名）'}
        </label>
        <input
          id={`${id}-username`}
          name="username"
          autoComplete="username"
          autoCapitalize="none"
          spellCheck={false}
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          disabled={busy}
          required
        />
        <label htmlFor={`${id}-password`}>密码</label>
        <input
          id={`${id}-password`}
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          disabled={busy}
          required
        />
        <label htmlFor={`${id}-expected`}>
          绑定目标{platform === 'codeforces' ? ' handle' : '用户名'}（可选）
        </label>
        <input
          id={`${id}-expected`}
          name="expected_handle"
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          placeholder="留空则使用官方验证得到的账号"
          value={expected}
          onChange={(event) => setExpected(event.target.value)}
          disabled={busy}
        />
        <p className="help">
          {platform === 'codeforces'
            ? '使用邮箱登录时可填 handle 核对；留空则自动识别。'
            : '留空则自动识别；填写后须与官方返回身份一致。'}
        </p>
        <button type="submit" disabled={busy || !username.trim() || !password.trim()}>
          {busy ? '正在登录并验证身份…' : '尝试登录并绑定'}
        </button>
      </form>
      {result && (
        <div role="status" aria-live="polite">
          <p>
            {result.status === 'authenticated'
              ? `官方身份验证通过，已绑定${result.account?.handle ? `账号：${result.account.handle}` : '账号'}。`
              : result.status === 'blocked'
                ? '当前服务器尚未验证登录，账号未绑定。'
                : result.status === 'rejected'
                  ? '本次登录未通过，账号未绑定。'
                  : '无法可靠确认官方身份，账号未绑定。'}
          </p>
          {result.status !== 'authenticated' && result.message && <p>{result.message}</p>}
          {result.status !== 'authenticated' && (
            <p className="help">
              可用官方登录后的完整 Cookie 手动绑定；官方浏览器中的验证不会自动传回本站。
            </p>
          )}
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      <a href={officialPages[platform]} target="_blank" rel="noreferrer">
        打开 {platformNames[platform]} 官方登录 ↗
      </a>
      <p className="help">交题方式仍为官方网页交题；当前浏览器仍需在官方平台登录。</p>
    </details>
  )
}

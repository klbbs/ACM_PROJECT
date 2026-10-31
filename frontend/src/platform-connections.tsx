import { useEffect, useRef, useState } from 'react'
import { api, date, names } from './api'
import './platform-connections.css'
import { NowcoderQrLogin } from './nowcoder-qr-login'
import { BrowserPlatformLogin } from './browser-platform-login'

// These links are controlled locally, never copied from challenge HTML or response URLs.
const officialPages: Record<string, string> = {
  codeforces: 'https://codeforces.com/enter',
  luogu: 'https://www.luogu.com.cn/',
  nowcoder: 'https://ac.nowcoder.com/',
  atcoder: 'https://atcoder.jp/login',
}
const labels: Record<string, string> = {
  authenticated: '已验证登录',
  account_mismatch: '登录账号不符',
  expired: '需要重新登录',
  blocked: '平台验证 / 访问限制',
  unknown: '无法确认登录',
  missing: '未填写 Cookie',
  invalid: 'Cookie 格式需修正',
}
const reasonLabels: Record<string, string> = {
  captcha: '验证码',
  browser_challenge: '浏览器验证',
  access_restricted: '平台访问限制',
  rate_limited: '请求过于频繁',
  network_error: '服务器网络异常',
  timeout: '平台响应超时',
  page_changed: '平台页面结构变化',
  response_too_large: '平台返回内容过大',
  upstream_unavailable: '平台服务暂不可用',
}
function browserAction(result: any) {
  if (result.status === 'expired')
    return {
      title: '平台会话需要重新登录',
      firstStep: '在官方平台重新登录，确认当前账号与你在本站配置的 handle / UID 相同。',
      link: '打开官方平台重新登录 ↗',
    }
  if (result.status === 'account_mismatch')
    return {
      title: '请切换为需要绑定的官方账号',
      firstStep:
        '核对上方平台返回账号。到官方退出当前账号，切换为本站配置的账号；若配置目标写错，请先编辑本站 handle / UID。',
      link: '打开官方平台切换账号 ↗',
    }
  return {
    title:
      result.reason === 'captcha'
        ? '平台返回了验证码，请你在官方完成'
        : result.reason === 'browser_challenge'
          ? '平台要求浏览器验证，请在官方完成'
          : '平台暂时未允许服务器访问',
    firstStep:
      result.reason === 'captcha'
        ? '在已登录平台的浏览器打开官方页面，亲自按平台要求完成验证码。'
        : result.reason === 'browser_challenge'
          ? '在已登录平台的浏览器打开官方页面，按平台要求完成浏览器验证；不一定包含验证码。'
          : '在已登录平台的浏览器打开官方页面，确认能正常访问；如出现验证，请按平台要求处理。',
    link: '打开官方平台处理验证 ↗',
  }
}
export function PlatformConnections({ revision, run, onError, onBound }: any) {
  const [items, setItems] = useState<any[]>([])
  const [checks, setChecks] = useState<Record<string, any>>({})
  const [connections, setConnections] = useState<Record<string, any>>({})
  const epoch = useRef(0)
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [nowcoderMethod, setNowcoderMethod] = useState<'qr' | 'cookie'>('qr')
  useEffect(() => {
    const values = Object.values(checks)
    if (!values.length) return
    const expires = Math.min(...values.map((value) => value.checked_at * 1000 + 300000))
    const timer = window.setTimeout(
      () => {
        setChecks((previous) =>
          Object.fromEntries(
            Object.entries(previous).filter(
              ([, value]) => Date.now() - value.checked_at * 1000 < 300000,
            ),
          ),
        )
      },
      Math.max(0, expires - Date.now()) + 5,
    )
    return () => window.clearTimeout(timer)
  }, [checks])
  useEffect(() => {
    const current = ++epoch.current
    let alive = true
    let timer: number | undefined
    setChecks({})
    setConnections({})
    setBusy({})
    const applyChecks = (results: any) => {
      if (!alive || epoch.current !== current) return
      setChecks(Object.fromEntries(results.items.map((v: any) => [`${v.platform}:${v.handle}`, v])))
      const pending = results.connections || []
      setConnections(Object.fromEntries(pending.map((v: any) => [`${v.platform}:${v.handle}`, v])))
      if (pending.some((v: any) => ['queued', 'checking'].includes(v.status)))
        timer = window.setTimeout(() => {
          api('/accounts/checks')
            .then(applyChecks)
            .catch((e: Error) => {
              if (alive && epoch.current === current) {
                setConnections((previous) =>
                  Object.fromEntries(
                    Object.entries(previous).map(([key, value]) => [
                      key,
                      ['queued', 'checking'].includes(value.status)
                        ? {
                            ...value,
                            status: 'failed',
                            message: '暂时无法更新检测进度，可手动重新检测。',
                          }
                        : value,
                    ]),
                  ),
                )
                onError(e.message)
              }
            })
        }, 1500)
    }
    Promise.all([api('/platforms'), api('/accounts/checks')])
      .then(([platforms, results]) => {
        if (!alive || epoch.current !== current) return
        setItems(platforms.items)
        applyChecks(results)
      })
      .catch((e: Error) => {
        if (alive && epoch.current === current) onError(e.message)
      })
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [revision])
  async function check(platform: string, handle: string, forceRefresh = false) {
    const key = `${platform}:${handle}`
    const current = epoch.current
    setBusy((previous) => ({ ...previous, [key]: true }))
    setConnections((previous) => {
      const next = { ...previous }
      delete next[key]
      return next
    })
    try {
      const result = await api('/accounts/check', 'POST', {
        platform,
        handle,
        ...(forceRefresh ? { force_refresh: true } : {}),
      })
      if (epoch.current !== current) return
      setChecks((previous) => ({ ...previous, [key]: result }))
    } catch (e: any) {
      if (epoch.current === current) onError(e.message)
    } finally {
      if (epoch.current === current) setBusy((previous) => ({ ...previous, [key]: false }))
    }
  }
  return (
    <section className="card settings-card">
      <h2>平台信息、登录检测与交题</h2>
      <p className="help">
        Codeforces、AtCoder 使用油猴助手获取
        Cookie，按提示确认完整性后保存检测；洛谷支持官方登录窗口，牛客支持微信扫码。
        公开资料可采集不代表登录成功；检测只读取官方页面，不会提交代码。 牛客扫码与 Cookie
        导入二选一；扫码验证成功会自动保存会话，无需再次去浏览器登录或复制 Cookie。 CF、AtCoder
        的真实交题入口在工作台编辑器下方，提交前读取官方编译器并检查会话；官方备用页仍使用浏览器自己的登录状态。
      </p>
      <div className="platform-action-grid">
        {items.map((p) => (
          <article key={p.platform}>
            <h3>{names[p.platform] || p.platform}</h3>
            {['codeforces', 'atcoder'].includes(p.platform) && (
              <div className="platform-cookie-login cookie-helper-note">
                <strong>通过油猴助手绑定 Cookie</strong>
                <ol>
                  <li>在自己的浏览器打开 {names[p.platform]} 官方网站，正常登录并完成验证。</li>
                  <li>用 ACM Lab 油猴助手获取 Cookie，并按提示确认是否完整。</li>
                  <li>回到本站，在下方配置同一个账号、粘贴 Cookie，保存并检测登录状态。</li>
                </ol>
                <div className="platform-browser-actions">
                  <a
                    className="secondary"
                    href={officialPages[p.platform]}
                    target="_blank"
                    rel="noreferrer"
                  >
                    在自己的浏览器登录 {names[p.platform]} ↗
                  </a>
                  <a
                    className="secondary"
                    href="/api/browser-userscript/acm-lab-cookie-helper.user.js"
                    download
                  >
                    下载油猴 Cookie 助手 ↓
                  </a>
                  <button
                    className="text-button"
                    onClick={() => window.dispatchEvent(new Event('acm:open-guide'))}
                  >
                    查看油猴安装与 Cookie 教程
                  </button>
                </div>
              </div>
            )}
            {p.platform === 'luogu' && (
              <BrowserPlatformLogin platform="luogu" onBound={onBound || (() => {})} />
            )}
            {p.platform === 'nowcoder' && (
              <div className="nowcoder-login-method">
                <p>
                  <strong>登录方式（二选一）</strong>
                </p>
                <div className="platform-browser-actions" role="group" aria-label="牛客登录方式">
                  <button
                    className="secondary"
                    aria-pressed={nowcoderMethod === 'qr'}
                    onClick={() => setNowcoderMethod('qr')}
                  >
                    微信扫码（推荐）
                  </button>
                  <button
                    className="secondary"
                    aria-pressed={nowcoderMethod === 'cookie'}
                    onClick={() => setNowcoderMethod('cookie')}
                  >
                    导入浏览器 Cookie
                  </button>
                </div>
                {nowcoderMethod === 'qr' ? (
                  <NowcoderQrLogin
                    handles={(p.accounts || []).map((a: any) => a.handle)}
                    onBound={onBound || (() => {})}
                  />
                ) : (
                  <div className="cookie-helper-note">
                    <p>
                      已有浏览器登录会话时，可在下方平台账号中填写数字 UID、粘贴 Cookie
                      并保存检测；选择此方式无需扫码。
                    </p>
                    <p>
                      两种方式都会保存一份平台会话。平台若限制多处登录，另一次登录可能使旧会话失效，请选择一种方式持续使用。
                    </p>
                    <a href={officialPages.nowcoder} target="_blank" rel="noreferrer">
                      打开牛客浏览器会话 ↗
                    </a>
                  </div>
                )}
              </div>
            )}
            {p.accounts?.map((a: any) => {
              const key = `${p.platform}:${a.handle}`
              const connection = connections[key]
              const pending = connection && ['queued', 'checking'].includes(connection.status)
              if (p.platform === 'luogu') {
                return (
                  <div key={a.handle} className="platform-account-check">
                    <p>
                      <strong>{a.handle}</strong> ·{' '}
                      {checks[key]?.status === 'authenticated' &&
                      checks[key]?.login_verified === true
                        ? '已验证登录'
                        : '公开资料同步'}
                    </p>
                    <p className="help">
                      使用数字 UID
                      同步可公开访问的资料与提交记录。完成题目后，请在洛谷官方网页交题，再同步记录。
                    </p>
                    {pending && <p role="status">正在自动检测平台登录并关联身份…</p>}
                    {connection?.message && <p role="status">{connection.message}</p>}
                    {checks[key]?.message && <p role="status">{checks[key].message}</p>}
                    {checks[key]?.identity_claim?.message && (
                      <p role="status">{checks[key].identity_claim.message}</p>
                    )}
                    <button
                      className="secondary"
                      disabled={busy[key] || pending}
                      onClick={() => check(p.platform, a.handle)}
                    >
                      {busy[key] || pending ? '正在检测…' : '检测登录状态'}
                    </button>
                  </div>
                )
              }
              const result = checks[key]
              const action = result ? browserAction(result) : null
              return (
                <div key={a.handle} className="platform-account-check">
                  <p>
                    <strong>{a.handle}</strong> · {a.has_cookie ? '已保存凭据' : '未保存凭据'} ·{' '}
                    {result
                      ? result.reason === 'captcha'
                        ? '需要在官方完成验证'
                        : labels[result.status] || '无法确认登录'
                      : '尚未检测'}
                  </p>
                  {p.platform === 'nowcoder' && a.login_method === 'qr' && (
                    <p className="help">
                      已通过微信扫码保存牛客会话，无需额外填写
                      Cookie。会话失效时重新扫码即可；浏览器另行登录可能影响现有会话。
                    </p>
                  )}
                  {a.cookie_info && (
                    <p className="help">
                      {a.cookie_info.status === 'valid'
                        ? `已保存 ${a.cookie_info.pair_count} 项请求 Cookie，格式通过；是否已登录以平台检测为准。`
                        : a.cookie_info.message}
                    </p>
                  )}
                  {pending && <p role="status">正在自动检测平台登录并关联身份…</p>}
                  {connection?.message && <p role="status">{connection.message}</p>}
                  <button
                    className="secondary"
                    disabled={busy[key] || pending}
                    onClick={() => check(p.platform, a.handle)}
                  >
                    {busy[key] || pending ? '正在检测…' : '检测登录状态'}
                  </button>
                  {result && (
                    <div role="status" aria-live="polite">
                      <p>{result.message}</p>
                      {result.identity_claim?.message && <p>{result.identity_claim.message}</p>}
                      {result.matched_account && <p>平台返回账号：{result.matched_account}</p>}
                      {p.platform === 'codeforces' && result.cookie_diagnostics && (
                        <p className="help">
                          CF 常见会话字段 JSESSIONID：
                          {result.cookie_diagnostics.cf_session_cookie_present
                            ? '已检测到'
                            : '未检测到'}
                          。 此项仅辅助检查 Cookie 导出完整性，登录以 CF 返回的当前身份为准。
                        </p>
                      )}
                      {(reasonLabels[result.reason] ||
                        result.upstream_status ||
                        result.retry_after_seconds > 0) && (
                        <p className="help">
                          {reasonLabels[result.reason] && (
                            <span>原因：{reasonLabels[result.reason]}。 </span>
                          )}
                          {result.upstream_status && (
                            <span>平台 HTTP {result.upstream_status}。 </span>
                          )}
                          {result.retry_after_seconds > 0 && (
                            <span>请至少等待 {result.retry_after_seconds} 秒后重试。</span>
                          )}
                        </p>
                      )}
                      {Array.isArray(result.guidance) && result.guidance.length > 0 && (
                        <ul className="help">
                          {result.guidance.map((text: string, index: number) => (
                            <li key={index}>{text}</li>
                          ))}
                        </ul>
                      )}
                      {(result.browser_action_required ||
                        (result.status === 'blocked' && !result.reason)) && (
                        <div className="platform-browser-action">
                          <strong>{action?.title}</strong>
                          <ol>
                            <li>{action?.firstStep}</li>
                            <li>
                              页面恢复后，用油猴助手或 F12 获取最新完整
                              Cookie，在本站编辑账号、保存。
                            </li>
                            <li>返回此处点击「已处理，重新检测」。</li>
                          </ol>
                          {result.status === 'blocked' && (
                            <p className="help">
                              浏览器验证与服务器访问是两次请求。若验证绑定浏览器或 IP，更新 Cookie
                              后服务器仍可能受限；这不等于 Cookie 过期。本站不会嵌入或代填验证码。
                            </p>
                          )}
                          <div className="platform-browser-actions">
                            {officialPages[p.platform] && (
                              <a
                                className="secondary"
                                href={officialPages[p.platform]}
                                target="_blank"
                                rel="noreferrer"
                              >
                                {action?.link}
                              </a>
                            )}
                            <button
                              className="secondary"
                              disabled={busy[key]}
                              onClick={() => check(p.platform, a.handle, true)}
                            >
                              {busy[key] ? '正在重新检测…' : '已处理，重新检测'}
                            </button>
                            <button
                              className="text-button"
                              onClick={() => window.dispatchEvent(new Event('acm:open-guide'))}
                            >
                              查看 Cookie 获取教程
                            </button>
                          </div>
                        </div>
                      )}
                      <p className="help">
                        检测时间：{date(result.checked_at)}
                        {result.cached ? '（近期结果）' : ''}。此结果不代表自动交题已启用。
                      </p>
                    </div>
                  )}
                </div>
              )
            })}
            <p>信息采集：{p.accounts?.length > 0 ? '已配置账号' : '请先在下方绑定账号'}</p>
            <button
              disabled={!(p.accounts?.length > 0)}
              onClick={() => run(`/platforms/${p.platform}/collect`)}
            >
              同步此平台信息
            </button>
            <p>交题：打开工作台题目，复制代码后进入该平台官方提交页。</p>
            {p.platform === 'luogu' && (
              <p>
                <a
                  className="secondary"
                  href={officialPages.luogu}
                  target="_blank"
                  rel="noreferrer"
                >
                  打开洛谷官方网页 ↗
                </a>
              </p>
            )}
            <button
              className="secondary"
              onClick={() => window.dispatchEvent(new Event('acm:open-guide'))}
            >
              {p.platform === 'luogu' ? '查看资料同步与交题教程' : '查看登录与交题教程'}
            </button>
          </article>
        ))}
      </div>
    </section>
  )
}

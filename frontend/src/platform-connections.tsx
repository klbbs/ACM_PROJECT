import { useEffect, useRef, useState } from 'react'
import { api, date, names } from './api'
import './platform-connections.css'

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
  invalid: 'Cookie 格式不完整',
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
export function PlatformConnections({ revision, run, onError }: any) {
  const [items, setItems] = useState<any[]>([])
  const [checks, setChecks] = useState<Record<string, any>>({})
  const epoch = useRef(0)
  const [busy, setBusy] = useState<Record<string, boolean>>({})
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
    setChecks({})
    setBusy({})
    Promise.all([api('/platforms'), api('/accounts/checks')])
      .then(([platforms, results]) => {
        if (!alive || epoch.current !== current) return
        setItems(platforms.items)
        setChecks(
          Object.fromEntries(results.items.map((v: any) => [`${v.platform}:${v.handle}`, v])),
        )
      })
      .catch((e: Error) => onError(e.message))
    return () => {
      alive = false
    }
  }, [revision])
  async function check(platform: string, handle: string, forceRefresh = false) {
    const key = `${platform}:${handle}`
    const current = epoch.current
    setBusy((previous) => ({ ...previous, [key]: true }))
    try {
      const result = await api('/accounts/check', 'POST', {
        platform,
        handle,
        ...(forceRefresh ? { force_refresh: true } : {}),
      })
      if (epoch.current !== current) return
      setChecks((previous) => ({ ...previous, [key]: result }))
    } catch (e: any) {
      onError(e.message)
    } finally {
      if (epoch.current === current) setBusy((previous) => ({ ...previous, [key]: false }))
    }
  }
  return (
    <section className="card settings-card">
      <h2>平台信息、登录检测与交题</h2>
      <p className="help">
        保存 Cookie
        后，请逐个账号检测。公开资料可采集不代表登录成功；检测只读取官方页面，不会提交代码。
        本站目前通过官方页面交题，不支持用 Cookie 自动代提交。官方页仍需在你当前浏览器中登录。
      </p>
      <div className="platform-action-grid">
        {items.map((p) => (
          <article key={p.platform}>
            <h3>{names[p.platform] || p.platform}</h3>
            {p.accounts?.map((a: any) => {
              const key = `${p.platform}:${a.handle}`
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
                  <button
                    className="secondary"
                    disabled={busy[key]}
                    onClick={() => check(p.platform, a.handle)}
                  >
                    {busy[key] ? '正在检测…' : '检测登录状态'}
                  </button>
                  {result && (
                    <div role="status" aria-live="polite">
                      <p>{result.message}</p>
                      {result.matched_account && <p>平台返回账号：{result.matched_account}</p>}
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
            <button
              className="secondary"
              onClick={() => window.dispatchEvent(new Event('acm:open-guide'))}
            >
              查看登录与交题教程
            </button>
          </article>
        ))}
      </div>
    </section>
  )
}

import { useEffect, useRef, useState } from 'react'
import { api, date, names } from './api'

const labels: Record<string, string> = {
  authenticated: '已验证登录',
  account_mismatch: '登录账号不符',
  expired: '需要重新登录',
  blocked: '平台验证 / 访问限制',
  unknown: '无法确认登录',
  missing: '未填写 Cookie',
  invalid: 'Cookie 格式不完整',
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
  async function check(platform: string, handle: string) {
    const key = `${platform}:${handle}`
    const current = epoch.current
    setBusy((previous) => ({ ...previous, [key]: true }))
    try {
      const result = await api('/accounts/check', 'POST', { platform, handle })
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
              return (
                <div key={a.handle} className="platform-account-check">
                  <p>
                    <strong>{a.handle}</strong> · {a.has_cookie ? '已保存凭据' : '未保存凭据'} ·{' '}
                    {result ? labels[result.status] : '尚未检测'}
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

import { useEffect, useState } from 'react'
import { api, names } from './api'
export function PlatformConnections({ revision, run, onError }: any) {
  const [items, setItems] = useState<any[]>([])
  useEffect(() => {
    let alive = true
    api('/platforms')
      .then((v) => {
        if (alive) setItems(v.items)
      })
      .catch((e: Error) => onError(e.message))
    return () => {
      alive = false
    }
  }, [revision])
  return (
    <section className="card settings-card">
      <h2>平台信息与交题入口</h2>
      <p className="help">
        同步信息只采集账号记录，不提交代码。交题使用你自己浏览器中的平台登录；本站保存的 Cookie
        不等于浏览器已登录。
      </p>
      <div className="platform-action-grid">
        {items.map((p) => (
          <article key={p.platform}>
            <h3>{names[p.platform] || p.platform}</h3>
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

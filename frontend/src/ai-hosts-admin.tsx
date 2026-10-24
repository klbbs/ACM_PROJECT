import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Save, Server, ShieldCheck } from 'lucide-react'
import { api } from './api'

type Hosts = { default_hosts: string[]; custom_hosts: string[] }

export function AIHostsAdmin({ onError, onNotice }: {
  onError: (message: string) => void
  onNotice: (message: string) => void
}) {
  const [hosts, setHosts] = useState<Hosts | null>(null)
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [loadError, setLoadError] = useState('')
  const lifecycle = useRef(0)
  const busy = useRef(false)
  const callbacks = useRef({ onError, onNotice })
  callbacks.current = { onError, onNotice }
  const reload = useCallback(async () => {
    if (busy.current) return
    busy.current = true
    const requestLifecycle = lifecycle.current
    setLoading(true)
    setLoadError('')
    try {
      const value = await api<Hosts>('/admin/ai-hosts')
      if (requestLifecycle !== lifecycle.current) return
      setHosts(value)
      setDraft(value.custom_hosts.join('\n'))
    } catch (error) {
      if (requestLifecycle !== lifecycle.current) return
      const message = error instanceof Error ? error.message : '白名单读取失败'
      setHosts(null)
      setLoadError(message)
      callbacks.current.onError(message)
    } finally {
      if (requestLifecycle === lifecycle.current) {
        busy.current = false
        setLoading(false)
      }
    }
  }, [])
  useEffect(() => {
    void reload()
    return () => {
      lifecycle.current += 1
      busy.current = false
    }
  }, [reload])
  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (busy.current || !hosts || loadError) return
    busy.current = true
    const requestLifecycle = lifecycle.current
    setSaving(true)
    try {
      const requested = draft.split(/[\n,，]+/).map((host) => host.trim()).filter(Boolean)
      const value = await api<Hosts>('/admin/ai-hosts', 'PUT', { hosts: requested })
      if (requestLifecycle !== lifecycle.current) return
      setHosts(value)
      setDraft(value.custom_hosts.join('\n'))
      callbacks.current.onNotice('AI 中转站域名白名单已保存并即时生效')
    } catch (error) {
      if (requestLifecycle !== lifecycle.current) return
      callbacks.current.onError(error instanceof Error ? error.message : '白名单保存失败')
    } finally {
      if (requestLifecycle === lifecycle.current) {
        busy.current = false
        setSaving(false)
      }
    }
  }
  return <section className="card admin-card ai-hosts-admin">
    <div className="card-heading">
      <div><h2><Server size={18} /> AI 中转站域名白名单</h2>
        <p className="help">管理员批准的 HTTPS 域名可供成员使用自定义 AI API。变更立即生效并保存在服务器数据库。</p></div>
      <ShieldCheck size={18} aria-hidden="true" />
    </div>
    <p className="help">内置提供方域名由服务器环境配置管理，不能在此移除。</p>
    <div className="ai-host-defaults" aria-label="内置提供方域名">
      {loading ? <span className="help">正在读取…</span> : hosts?.default_hosts.map((host) => <code key={host}>{host}</code>)}
    </div>
    {loadError && <div role="alert">
      <p>白名单读取失败：{loadError}。读取成功前不能保存，以免覆盖已有配置。</p>
      <button type="button" onClick={() => void reload()} disabled={loading}>重新读取</button>
    </div>}
    <form onSubmit={save}>
      <label htmlFor="ai-hosts-custom">已批准的自定义域名</label>
      <textarea id="ai-hosts-custom" rows={5} maxLength={26000} value={draft} disabled={loading || saving || !hosts || !!loadError}
        onChange={(event) => setDraft(event.target.value)} placeholder="api.example.com，每行一个域名" />
      <p className="help">只填写主机名，例如 api.example.com；不含协议、端口、路径或通配符。删除域名后，该域名配置将无法继续调用。</p>
      <button className="primary" disabled={loading || saving || !hosts || !!loadError}>
        <Save size={15} /> {saving ? '正在保存…' : '保存白名单'}
      </button>
    </form>
  </section>
}

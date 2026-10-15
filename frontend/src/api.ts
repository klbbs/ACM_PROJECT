export async function api<T = any>(path: string, method = 'GET', data?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, { method, credentials: 'same-origin', headers: data === undefined ? {} : { 'Content-Type': 'application/json' }, body: data === undefined ? undefined : JSON.stringify(data) })
  const body = await res.json()
  if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '请求未完成，请检查输入或连接')
  return body
}
export const names: Record<string,string> = {codeforces:'Codeforces',luogu:'洛谷',nowcoder:'牛客',atcoder:'AtCoder'}
export const fmt = (n: number | string) => Number(n || 0).toLocaleString('zh-CN')
export const date = (ts?: number) => ts ? new Date(ts * 1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}) : '尚未同步'
export const shortDate = (ts: number) => new Date(ts * 1000).toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit'})

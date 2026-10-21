let csrfToken = ''
export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly retryAfterSeconds?: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

function retryAfterSeconds(value: string | null): number | undefined {
  if (value === null || !/^\d+$/.test(value.trim())) return undefined
  const seconds = Number(value)
  return Number.isSafeInteger(seconds) && seconds <= 86400 ? seconds : undefined
}

export async function api<T = any>(path: string, method = 'GET', data?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: 'same-origin',
    headers: {
      'X-ACM-Request': '1',
      ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
      ...(data === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    body: data === undefined ? undefined : JSON.stringify(data),
  })
  if (res.status === 401 && !path.startsWith('/auth/'))
    window.dispatchEvent(new Event('acm:session-expired'))
  const body = await res.json().catch((error: unknown) => {
    if (res.ok) throw error
    return undefined
  })
  if (path === '/auth/status') csrfToken = body?.csrf_token || ''
  if (!res.ok)
    throw new ApiError(
      typeof body?.detail === 'string' ? body.detail : '请求未完成，请检查输入或连接',
      res.status,
      retryAfterSeconds(res.headers.get('Retry-After')),
    )
  return body
}
export const names: Record<string, string> = {
  codeforces: 'Codeforces',
  luogu: '洛谷',
  nowcoder: '牛客',
  atcoder: 'AtCoder',
}
export const fmt = (n: number | string) => Number(n || 0).toLocaleString('zh-CN')
export const date = (ts?: number) =>
  ts
    ? new Date(ts * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
    : '尚未同步'
export const shortDate = (ts: number) =>
  new Date(ts * 1000).toLocaleDateString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
  })

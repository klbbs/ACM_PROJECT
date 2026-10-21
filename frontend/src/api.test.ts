import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

let api: typeof import('./api').api
let ApiError: typeof import('./api').ApiError
const fetchMock = vi.fn<typeof fetch>()

function response(body: unknown, status = 200, headers?: HeadersInit) {
  fetchMock.mockResolvedValueOnce(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json', ...headers },
    }),
  )
}

beforeEach(async () => {
  vi.resetModules()
  ;({ api, ApiError } = await import('./api'))
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

it('preserves same-origin requests, JSON bodies and CSRF headers', async () => {
  response({ csrf_token: 'test-csrf' })
  await api('/auth/status')
  expect(fetchMock).toHaveBeenLastCalledWith('/api/auth/status', {
    method: 'GET',
    credentials: 'same-origin',
    headers: { 'X-ACM-Request': '1' },
    body: undefined,
  })

  response({ saved: true })
  await expect(api('/accounts', 'POST', { handle: 'fixture' })).resolves.toEqual({ saved: true })
  expect(fetchMock).toHaveBeenLastCalledWith('/api/accounts', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'X-ACM-Request': '1',
      'X-CSRF-Token': 'test-csrf',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ handle: 'fixture' }),
  })
})

it('exposes rate limit status and Retry-After without losing the server message', async () => {
  response({ detail: '请求过于频繁，请稍后重试' }, 429, { 'Retry-After': '10' })
  const error = await api('/platform-login/browser/start', 'POST').catch((error) => error)
  expect(error).toBeInstanceOf(ApiError)
  expect(error).toBeInstanceOf(Error)
  expect(error).toMatchObject({
    name: 'ApiError',
    status: 429,
    retryAfterSeconds: 10,
    message: '请求过于频繁，请稍后重试',
  })
})

describe('Retry-After validation', () => {
  it.each(['0', ' 10 ', '86400'])('accepts bounded whole seconds %j', async (value) => {
    response({ detail: '稍后重试' }, 429, { 'Retry-After': value })
    await expect(api('/limited')).rejects.toMatchObject({ retryAfterSeconds: Number(value) })
  })

  it.each([null, '', '-1', '1.5', '10seconds', 'Infinity', '86401', '999999999999999999999'])(
    'does not supply a countdown for invalid seconds %j',
    async (value) => {
      response({}, 429, value === null ? undefined : { 'Retry-After': value })
      await expect(api('/limited')).rejects.toMatchObject({
        status: 429,
        retryAfterSeconds: undefined,
      })
    },
  )
})

it('notifies session expiry for protected routes but excludes auth routes', async () => {
  const expired = vi.fn()
  window.addEventListener('acm:session-expired', expired)
  try {
    response({ detail: '登录已过期' }, 401)
    await expect(api('/accounts')).rejects.toMatchObject({ status: 401 })
    expect(expired).toHaveBeenCalledOnce()
    response({ detail: '登录失败' }, 401)
    await expect(api('/auth/login', 'POST')).rejects.toMatchObject({ status: 401 })
    expect(expired).toHaveBeenCalledOnce()
  } finally {
    window.removeEventListener('acm:session-expired', expired)
  }
})

it('uses the safe fallback for validation detail instead of reflecting sensitive inputs', async () => {
  response({ detail: [{ msg: 'invalid', input: 'private-password' }] }, 422)
  const error = await api('/platform-login/browser/start', 'POST', {
    password: 'private-password',
  }).catch((error) => error)
  expect(error).toMatchObject({ status: 422, message: '请求未完成，请检查输入或连接' })
  expect(String(error)).not.toContain('private-password')
})

it('preserves HTTP error status and safe fallback when an error body is not JSON', async () => {
  fetchMock.mockResolvedValueOnce(new Response('<html>gateway failure</html>', { status: 502 }))
  await expect(api('/accounts')).rejects.toMatchObject({
    status: 502,
    message: '请求未完成，请检查输入或连接',
  })
})

it('propagates network failures without inventing an HTTP response', async () => {
  const failure = new TypeError('Failed to fetch')
  fetchMock.mockRejectedValueOnce(failure)
  await expect(api('/accounts')).rejects.toBe(failure)
  expect(failure).not.toBeInstanceOf(ApiError)
})

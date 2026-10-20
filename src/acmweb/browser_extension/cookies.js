// Kept independent of browser APIs so host boundaries and serialization are testable.
export const platforms = Object.freeze({
  'codeforces.com': { id: 'codeforces', name: 'Codeforces' },
  'www.luogu.com.cn': { id: 'luogu', name: '洛谷' },
  'www.nowcoder.com': { id: 'nowcoder', name: '牛客' },
  'ac.nowcoder.com': { id: 'nowcoder', name: '牛客算法' },
  'atcoder.jp': { id: 'atcoder', name: 'AtCoder' },
})

export function identify(rawUrl) {
  try {
    const url = new URL(rawUrl)
    const platform = platforms[url.hostname]
    if (url.protocol !== 'https:' || url.port || !platform) return null
    // Neither queries nor fragments (which might contain tokens) are needed.
    return { ...platform, origin: url.origin, url: url.origin + url.pathname, host: url.hostname }
  } catch { return null }
}

export function cookieHeader(cookies) {
  // Third-party partitioned cookies cannot be replayed by the standalone server.
  const eligible = cookies.filter(c => !c.firstPartyDomain && !c.partitionKey?.topLevelSite)
  if (!eligible.length) throw Error('此页面没有可复制的非分区 Cookie。请先登录平台、刷新页面后再试。')
  const sorted = [...eligible].sort((a, b) => (b.path || '/').length - (a.path || '/').length)
  const parts = sorted.map(c => {
    if (!/^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/.test(c.name) || !/^[\x20-\x7e]*$/.test(c.value) || /[;\r\n]/.test(c.value))
      throw Error('Cookie 含无法安全转换为请求标头的字符，请使用网站中的 F12 教程。')
    return c.name + '=' + c.value
  })
  const header = parts.join('; ')
  if (header.length > 16384) throw Error('Cookie 超过本站 16 KiB 限制，请使用 F12 复制平台实际请求的 Cookie。')
  return { header, count: sorted.length, skipped: cookies.length - sorted.length }
}

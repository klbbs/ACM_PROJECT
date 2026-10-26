type ApiDraft = {
  api_key?: string
  api_secret?: string
  has_api_key?: boolean
  clearApi?: boolean
}

export function cfApiPayload(value: ApiDraft) {
  if (value.clearApi) return { api_key: '', api_secret: '' }
  if (value.api_key || value.api_secret)
    return { api_key: value.api_key || '', api_secret: value.api_secret || '' }
  return {}
}

export function cfApiValid(value: ApiDraft) {
  return (
    value.clearApi ||
    (!value.api_key && !value.api_secret) ||
    (!!value.api_key?.match(/^(?:[\da-fA-F]{32}|[\da-fA-F]{40})$/) &&
      !!value.api_secret?.match(/^(?:[\da-fA-F]{32}|[\da-fA-F]{40})$/))
  )
}

export function CfApiFields({
  value,
  disabled,
  onChange,
}: {
  value: ApiDraft
  disabled: boolean
  onChange: (next: ApiDraft) => void
}) {
  const field = (name: 'api_key' | 'api_secret', label: string) => (
    <label>
      {label}
      <input
        type="password"
        autoComplete="new-password"
        value={value[name] || ''}
        maxLength={40}
        disabled={disabled || value.clearApi}
        placeholder={value.has_api_key ? '已保存，留空保留' : '粘贴官方提供的 API 密钥'}
        onChange={(event) => onChange({ ...value, [name]: event.target.value })}
      />
    </label>
  )
  return (
    <details className="settings-guide">
      <summary>Codeforces 官方源码 API{value.has_api_key ? ' · 已保存' : ' · 未配置'}</summary>
      <p className="help">
        CF 获取官方源码及 AI 复盘需绑定 API Key / Secret。仅用于读取该账号本人的提交源码，
        判题同步和交题仍使用各自的方式，不代替交题所需的 Cookie。 请在{' '}
        <a href="https://codeforces.com/settings/api" target="_blank" rel="noreferrer">
          Codeforces 官方 API 设置
        </a>
        手动创建并填写 Key 与 Secret；我们不会自动采集。两项均留空保留已保存的密钥。
      </p>
      <div className="practice-filters">
        {field('api_key', 'CF API Key')}
        {field('api_secret', 'CF API Secret')}
      </div>
      {!cfApiValid(value) && (
        <p role="alert">请完整填写官方提供的 Key 和 Secret（32 或 40 位十六进制）。</p>
      )}
      {value.has_api_key && (
        <button
          type="button"
          className="text-button"
          disabled={disabled}
          onClick={() =>
            onChange({ ...value, api_key: '', api_secret: '', clearApi: !value.clearApi })
          }
        >
          {value.clearApi ? '取消清除，保留已保存密钥' : '清除已保存的 CF API 密钥'}
        </button>
      )}
      {value.clearApi && <p className="help">保存平台账号后将清除这对密钥。</p>}
    </details>
  )
}

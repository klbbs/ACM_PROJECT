import { useEffect, useId, useState } from 'react'
import './account-cookie-field.css'

type Info = { status?: string; pair_count?: number; message?: string }
type Props = {
  value: string
  onChange: (value: string) => void
  saved?: boolean
  info?: Info
  disabled?: boolean
  loginMethod?: string
}

export function AccountCookieField({ value, onChange, saved, info, disabled, loginMethod }: Props) {
  const id = useId()
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    if (!value) setVisible(false)
  }, [value])
  return (
    <div className="account-cookie-field">
      {saved && loginMethod === 'qr' && (
        <p className="help">
          本账号已通过扫码保存登录会话，不需要额外导入 Cookie；留空即可保留扫码会话。
        </p>
      )}
      {saved && (
        <p className="help" role="status">
          {info?.status === 'invalid'
            ? `服务器保留了凭据，但格式需修正：${info.message}`
            : `服务器已保存 Cookie${info?.pair_count ? `（${info.pair_count} 项）` : ''}。`}{' '}
          为避免泄露，编辑框不会回填原值。留空保留同一账号的凭据；填写新值则替换，修改账号名时请重新填写。
        </p>
      )}
      <label htmlFor={id}>Cookie（可选）</label>
      <input
        id={id}
        type={visible ? 'text' : 'password'}
        autoComplete="off"
        autoCapitalize="none"
        spellCheck={false}
        value={value}
        maxLength={16384}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        placeholder={saved ? '已保存 · 留空保留；粘贴新 Cookie 可替换' : 'name=value; name2=value2'}
        aria-describedby={`${id}-help`}
      />
      <div className="settings-actions">
        <button
          type="button"
          className="text-button"
          disabled={!value || disabled}
          onClick={() => setVisible((old) => !old)}
        >
          {visible ? '隐藏本次输入' : '显示本次输入'}
        </button>
        {value && (
          <small>本次输入 {value.length} 个字符；保存时校验格式，支持的平台会检测登录。</small>
        )}
      </div>
      <p className="help" id={`${id}-help`}>
        可直接粘贴油猴导出的值，也可粘贴单行 Cookie: 字段，保存时自动移除前缀。 不要粘贴响应
        Set-Cookie、JSON 或整段请求头。格式通过不代表凭据完整或平台已登录。
      </p>
    </div>
  )
}

import { useRef, useState } from 'react'

export function AIConnectionTest({ disabled, save, run, onError }: {
  disabled: boolean
  save: () => Promise<boolean>
  run: () => Promise<unknown>
  onError: (message: string) => void
}) {
  const locked = useRef(false)
  const [busy, setBusy] = useState(false)
  return <button disabled={disabled || busy} onClick={async () => {
    if (disabled || locked.current) return
    locked.current = true
    setBusy(true)
    try {
      if (await save()) await run()
    } catch (error) {
      onError(error instanceof Error ? error.message : '连接测试未能启动，请重试')
    } finally {
      locked.current = false
      setBusy(false)
    }
  }}>{busy ? '正在保存并启动测试…' : '保存并测试连接（会计费）'}</button>
}

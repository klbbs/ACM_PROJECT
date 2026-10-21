import { useCallback, useRef, useState, type SetStateAction } from 'react'

type RefreshToken = { version: number; request: number }

/** Background reads may replace saved values, but never a newer local draft. */
export function useSettingsDraft<T>(initial: T) {
  const [value, setRawValue] = useState(initial)
  const version = useRef(0)
  const request = useRef(0)
  const dirty = useRef(false)
  const markDirty = useCallback(() => {
    dirty.current = true
    version.current++
  }, [])
  const setValue = useCallback(
    (next: SetStateAction<T>) => {
      markDirty()
      setRawValue(next)
    },
    [markDirty],
  )
  const getVersion = useCallback(() => version.current, [])
  const beginRefresh = useCallback(
    (): RefreshToken => ({ version: version.current, request: ++request.current }),
    [],
  )
  const refresh = useCallback((next: T, token: RefreshToken) => {
    if (dirty.current || token.version !== version.current || token.request !== request.current)
      return false
    setRawValue(next)
    return true
  }, [])
  const commit = useCallback((next: T, savedVersion: number) => {
    if (savedVersion !== version.current) return false
    dirty.current = false
    version.current++
    request.current++
    setRawValue(next)
    return true
  }, [])
  return { value, setValue, markDirty, getVersion, beginRefresh, refresh, commit }
}

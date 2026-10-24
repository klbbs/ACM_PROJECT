import { useEffect, useState } from 'react'
import { api } from './api'

type Report = Record<string, any>

/** Recover saved recommendations independently of the latest analysis or candidate filters. */
export function useRecommendationReport(
  account: string | null,
  revision: number,
  onError: (message: string) => void,
) {
  const [saved, setSaved] = useState<{ account: string; report: Report | null } | null>(null)
  useEffect(() => {
    if (!account) return
    let current = true
    const restore = async () => {
      try {
        const data = await api('/reports?kind=recommend')
        const latest = data.items.find((item: Report) => item.kind === 'recommend')
        const report = latest ? await api(`/reports/${encodeURIComponent(latest.id)}`) : null
        if (current) setSaved({ account, report: report?.kind === 'recommend' ? report : null })
      } catch (error: any) {
        if (current) onError(`读取已保存的 AI 选题失败：${error.message}`)
      }
    }
    void restore()
    return () => {
      current = false
    }
  }, [account, revision, onError])
  // Do not briefly render another account's recommendations before its request completes.
  return saved?.account === account ? saved.report : null
}

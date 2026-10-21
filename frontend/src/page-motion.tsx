import { useEffect, useRef, useState, type ReactNode } from 'react'

/** Animate the existing surface, without replacing it or delaying navigation. */
export function usePageMotion(page: string) {
  const surface = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    const animation = surface.current?.animate?.(
      [
        { opacity: 0.65, transform: 'translateY(5px)' },
        { opacity: 1, transform: 'translateY(0)' },
      ],
      { duration: 180, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' },
    )
    return () => animation?.cancel()
  }, [page])
  return surface
}

/** Keep sensitive settings drafts only in memory while visiting another feature. */
export function RetainedPage({ active, children }: { active: boolean; children: ReactNode }) {
  const [visited, setVisited] = useState(active)
  if (active && !visited) setVisited(true)
  return visited || active ? <div hidden={!active}>{children}</div> : null
}

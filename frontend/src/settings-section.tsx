import { useState, type ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'

/** Native disclosure keeps form controls mounted, so collapsing never clears a draft. */
export function SettingsSection({
  id,
  title,
  description,
  defaultOpen = false,
  children,
}: {
  id: string
  title: string
  description: string
  defaultOpen?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <details
      id={id}
      className="settings-section"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <span>
          <h2>{title}</h2>
          <span className="settings-section-description">{description}</span>
        </span>
        <ChevronDown size={19} aria-hidden="true" />
      </summary>
      <div className="settings-section-body">{children}</div>
    </details>
  )
}

/** Open the target and every containing disclosure before measuring its scroll position. */
export function revealSettingsSection(id: string) {
  const target = document.getElementById(id)
  if (!target) return
  for (let node: HTMLElement | null = target; node; node = node.parentElement) {
    if (node instanceof HTMLDetailsElement) node.open = true
  }
  requestAnimationFrame(() => {
    target.scrollIntoView({
      block: 'start',
      behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
        ? 'instant'
        : 'smooth',
    })
  })
}

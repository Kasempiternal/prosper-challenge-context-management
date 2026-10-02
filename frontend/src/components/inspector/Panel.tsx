import { X } from 'lucide-react'
import type { ReactNode } from 'react'
import { IconButton } from '../ui/Button'

interface PanelHeaderProps {
  icon: ReactNode
  eyebrow: string
  title: ReactNode
  onClose: () => void
  actions?: ReactNode
}

export function PanelHeader({ icon, eyebrow, title, onClose, actions }: PanelHeaderProps) {
  return (
    <div className="flex items-start gap-3 px-5 pt-4 pb-3">
      <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-[10px] bg-raised text-ink-soft">
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[11px] font-semibold tracking-[0.06em] text-muted uppercase">{eyebrow}</p>
        <h2 className="truncate text-[15px] font-semibold tracking-[-0.01em]">{title}</h2>
      </div>
      {actions}
      <IconButton label="Close panel" onClick={onClose} className="-mr-1.5">
        <X className="size-4" />
      </IconButton>
    </div>
  )
}

export function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 className="text-[11px] font-semibold tracking-[0.06em] text-muted uppercase">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  )
}

export function PanelBody({ children }: { children: ReactNode }) {
  return <div className="scrollbar-thin flex-1 overflow-y-auto px-5 pt-4 pb-6">{children}</div>
}

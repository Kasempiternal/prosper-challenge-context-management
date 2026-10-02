import { motion } from 'motion/react'
import { useId } from 'react'
import { cn } from '../../lib/cn'
import { spring } from '../../lib/motion'

interface TabsProps<T extends string> {
  value: T
  onChange: (value: T) => void
  tabs: ReadonlyArray<{ value: T; label: string; badge?: number; count?: number }>
}

export function Tabs<T extends string>({ value, onChange, tabs }: TabsProps<T>) {
  const id = useId()
  return (
    <div role="tablist" className="flex gap-5 border-b border-border-subtle px-5">
      {tabs.map((tab) => {
        const active = tab.value === value
        return (
          <button
            key={tab.value}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => onChange(tab.value)}
            className={cn(
              'relative flex items-center gap-1.5 pt-1 pb-2.5 text-[13px] font-medium transition-colors duration-200',
              active ? 'text-ink' : 'text-muted hover:text-ink-soft',
            )}
          >
            {tab.label}
            {!!tab.count && (
              <span className="rounded-full bg-raised px-1.5 font-mono text-[11px] font-medium text-muted tabular-nums">{tab.count}</span>
            )}
            {!!tab.badge && (
              <span className="rounded-full bg-danger-soft px-1.5 text-[11px] font-semibold text-danger">{tab.badge}</span>
            )}
            {active && (
              <motion.span
                layoutId={`tab-underline-${id}`}
                className="absolute inset-x-0 -bottom-px h-[2px] rounded-full bg-ink"
                transition={spring}
              />
            )}
          </button>
        )
      })}
    </div>
  )
}

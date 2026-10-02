import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useRef, type ReactNode } from 'react'
import { cn } from '../../lib/cn'
import { softSpring } from '../../lib/motion'

interface PopoverProps {
  open: boolean
  onClose: () => void
  anchor: ReactNode
  children: ReactNode
  align?: 'start' | 'end'
  className?: string
}

export function Popover({ open, onClose, anchor, children, align = 'start', className }: PopoverProps) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose()
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('pointerdown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('pointerdown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open, onClose])

  return (
    <div ref={ref} className="relative">
      {anchor}
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.98 }}
            transition={softSpring}
            style={{ transformOrigin: align === 'start' ? 'top left' : 'top right' }}
            className={cn(
              'absolute top-[calc(100%+8px)] z-40 rounded-[14px] border border-border-subtle bg-card p-1.5 shadow-panel',
              align === 'start' ? 'left-0' : 'right-0',
              className,
            )}
          >
            {children}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded-[5px] border border-border bg-surface px-1 font-sans text-[11px] font-medium text-ink-soft shadow-[0_1px_0_var(--color-border)]">
      {children}
    </kbd>
  )
}

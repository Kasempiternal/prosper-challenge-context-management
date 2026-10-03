import { AnimatePresence, motion } from 'motion/react'
import { useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { fade, softSpring } from '../../lib/motion'
import { useModalFocus } from './useModalFocus'

interface SheetProps {
  open: boolean
  onClose: () => void
  /** The dialog's accessible name. */
  label: string
  describedBy?: string
  /** The id of the element to focus on open. */
  initialFocus?: string | null
  children: ReactNode
}

/** A modal panel from the right edge, shaped like the inspector panel it covers. */
export function Sheet({ open, onClose, label, describedBy, initialFocus, children }: SheetProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  useModalFocus(open, panelRef, onClose, initialFocus)

  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex justify-end p-3"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={fade}
        >
          <div className="absolute inset-0 bg-overlay backdrop-blur-[2px]" onClick={onClose} aria-hidden />
          <motion.div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-label={label}
            aria-describedby={describedBy}
            tabIndex={-1}
            className="relative flex h-full w-full max-w-[440px] flex-col overflow-hidden rounded-[20px] border border-border-subtle bg-card shadow-panel outline-none"
            initial={{ opacity: 0, x: 28 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 20 }}
            transition={softSpring}
          >
            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  )
}

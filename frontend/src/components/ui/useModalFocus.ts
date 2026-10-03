import { useEffect, useRef, type RefObject } from 'react'

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * A modal's keyboard contract while `open`: Esc closes (before any page shortcut sees it), Tab
 * stays inside the panel, focus starts on `#initialFocus`, else the first field or
 * [data-autofocus], else the panel, and goes back to whatever had it once the modal closes.
 */
export function useModalFocus(open: boolean, panelRef: RefObject<HTMLElement | null>, onClose: () => void, initialFocus?: string | null) {
  const close = useRef(onClose)
  useEffect(() => {
    close.current = onClose
  })

  useEffect(() => {
    if (!open) return
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const panel = panelRef.current
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        close.current()
      } else if (e.key === 'Tab' && panel) {
        const items = [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.getClientRects().length > 0)
        const active = document.activeElement
        const first = items[0] ?? panel
        const last = items.at(-1) ?? panel
        if (e.shiftKey ? active === first || !panel.contains(active) : active === last || !panel.contains(active)) {
          e.preventDefault()
          ;(e.shiftKey ? last : first).focus()
        }
      }
    }
    window.addEventListener('keydown', onKey, true)
    const target =
      (initialFocus ? document.getElementById(initialFocus) : null) ??
      panel?.querySelector<HTMLElement>('input, textarea, [data-autofocus]') ??
      panel
    target?.focus()
    return () => {
      window.removeEventListener('keydown', onKey, true)
      if (opener?.isConnected) opener.focus()
    }
  }, [open, panelRef, initialFocus])
}

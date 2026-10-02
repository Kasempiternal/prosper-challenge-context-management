import { motion } from 'motion/react'
import { cn } from '../../lib/cn'
import { spring } from '../../lib/motion'

interface ToggleProps {
  checked: boolean
  onChange: (checked: boolean) => void
  label: string
  id?: string
}

export function Toggle({ checked, onChange, label, id }: ToggleProps) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={cn(
        'relative inline-flex h-[22px] w-[38px] shrink-0 items-center rounded-full p-[3px] transition-colors duration-200 ease-out-soft',
        checked ? 'justify-end bg-accent' : 'justify-start bg-border-strong hover:bg-faint/60',
      )}
    >
      <motion.span layout transition={spring} className="size-4 rounded-full bg-accent-ink shadow-knob" />
    </button>
  )
}

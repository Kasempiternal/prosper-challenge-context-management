import { motion } from 'motion/react'
import { useId, useRef, type KeyboardEvent } from 'react'
import { cn } from '../../lib/cn'
import { spring } from '../../lib/motion'

interface SegmentedProps<T extends string> {
  value: T
  onChange: (value: T) => void
  options: ReadonlyArray<{ value: T; label: string; title?: string }>
  label: string
  disabled?: boolean
  id?: string
}

/** A radio group drawn as one track with a sliding pill. Arrow keys move the choice. */
export function Segmented<T extends string>({ value, onChange, options, label, disabled, id }: SegmentedProps<T>) {
  const pill = useId()
  const refs = useRef<(HTMLButtonElement | null)[]>([])

  const onKeyDown = (e: KeyboardEvent, i: number) => {
    const step = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0
    if (!step) return
    e.preventDefault()
    const next = (i + step + options.length) % options.length
    onChange(options[next].value)
    refs.current[next]?.focus()
  }

  return (
    <div
      id={id}
      role="radiogroup"
      aria-label={label}
      aria-disabled={disabled || undefined}
      className={cn('flex rounded-[10px] border border-border-subtle bg-raised p-[3px]', disabled && 'opacity-60')}
    >
      {options.map((o, i) => {
        const active = o.value === value
        return (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el
            }}
            type="button"
            role="radio"
            aria-checked={active}
            title={o.title}
            tabIndex={active ? 0 : -1}
            disabled={disabled}
            onClick={() => onChange(o.value)}
            onKeyDown={(e) => onKeyDown(e, i)}
            className={cn(
              'relative h-7 flex-auto rounded-[7px] px-2.5 text-[12.5px] font-medium whitespace-nowrap outline-none',
              'transition-colors duration-150 focus-visible:shadow-focus disabled:cursor-not-allowed',
              active ? 'text-ink' : 'text-muted enabled:hover:text-ink-soft',
            )}
          >
            {active && (
              <motion.span
                layoutId={`segmented-${pill}`}
                transition={spring}
                className="absolute inset-0 rounded-[7px] border border-accent/45 bg-card shadow-card"
                aria-hidden
              />
            )}
            <span className="relative">{o.label}</span>
          </button>
        )
      })}
    </div>
  )
}

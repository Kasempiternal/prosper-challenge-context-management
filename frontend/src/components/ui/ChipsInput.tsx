import { AnimatePresence, motion } from 'motion/react'
import { X } from 'lucide-react'
import { useState } from 'react'
import { cn } from '../../lib/cn'
import { spring } from '../../lib/motion'

interface ChipsInputProps {
  values: string[]
  onChange: (values: string[]) => void
  placeholder?: string
  label: string
}

/** Enter or comma commits a chip; Backspace on an empty draft removes the last one. */
export function ChipsInput({ values, onChange, placeholder, label }: ChipsInputProps) {
  const [draft, setDraft] = useState('')

  const commit = () => {
    const value = draft.trim()
    if (value && !values.includes(value)) onChange([...values, value])
    setDraft('')
  }

  return (
    <div
      className={cn(
        'flex min-h-8 flex-wrap items-center gap-1 rounded-[8px] border border-border bg-card px-1.5 py-1',
        'transition-[border-color,box-shadow] duration-200 focus-within:border-accent focus-within:shadow-focus',
      )}
    >
      <AnimatePresence initial={false}>
        {values.map((v) => (
          <motion.span
            key={v}
            layout
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.8 }}
            transition={spring}
            className="inline-flex items-center gap-0.5 rounded-full bg-raised py-0.5 pr-0.5 pl-2 text-[12px] text-ink-soft"
          >
            {v}
            <button
              type="button"
              aria-label={`Remove ${v}`}
              onClick={() => onChange(values.filter((x) => x !== v))}
              className="rounded-full p-0.5 text-muted transition-colors hover:bg-hover hover:text-ink"
            >
              <X className="size-3" />
            </button>
          </motion.span>
        ))}
      </AnimatePresence>
      <input
        aria-label={label}
        value={draft}
        placeholder={values.length ? '' : placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault()
            commit()
          } else if (e.key === 'Backspace' && !draft && values.length) {
            onChange(values.slice(0, -1))
          }
        }}
        className="h-6 min-w-[60px] flex-1 bg-transparent px-1 text-[12.5px] outline-none placeholder:text-faint"
      />
    </div>
  )
}

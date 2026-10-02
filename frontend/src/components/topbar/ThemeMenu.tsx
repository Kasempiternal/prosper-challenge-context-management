import { Check, Monitor, Moon, Sun } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { cn } from '../../lib/cn'
import { useTheme, type ThemePreference } from '../../store/theme'
import { IconButton } from '../ui/Button'
import { Popover } from '../ui/Popover'

const OPTIONS: ReadonlyArray<{ value: ThemePreference; label: string; icon: ReactNode }> = [
  { value: 'light', label: 'Light', icon: <Sun className="size-4" /> },
  { value: 'dark', label: 'Dark', icon: <Moon className="size-4" /> },
  { value: 'system', label: 'System', icon: <Monitor className="size-4" /> },
]

export function ThemeMenu() {
  const preference = useTheme((s) => s.preference)
  const resolved = useTheme((s) => s.resolved)
  const setPreference = useTheme((s) => s.setPreference)
  const [open, setOpen] = useState(false)
  const label = `Theme: ${preference === 'system' ? `System (${resolved})` : preference}`

  return (
    <Popover
      open={open}
      onClose={() => setOpen(false)}
      align="end"
      className="w-[180px]"
      anchor={
        <IconButton label={label} active={open} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
          {preference === 'system' ? <Monitor className="size-4" /> : resolved === 'dark' ? <Moon className="size-4" /> : <Sun className="size-4" />}
        </IconButton>
      }
    >
      <p className="px-2.5 pt-1.5 pb-2 text-[11px] font-semibold tracking-[0.06em] text-muted uppercase">Theme</p>
      <div role="menu" aria-label="Theme">
        {OPTIONS.map((o) => {
          const checked = o.value === preference
          return (
            <button
              key={o.value}
              type="button"
              role="menuitemradio"
              aria-checked={checked}
              onClick={() => {
                setPreference(o.value)
                setOpen(false)
              }}
              className={cn(
                'flex w-full items-center gap-2.5 rounded-[8px] px-2.5 py-1.5 text-left text-[13px] transition-colors duration-150 outline-none',
                'hover:bg-hover focus-visible:bg-hover',
                checked ? 'text-ink' : 'text-ink-soft',
              )}
            >
              <span className={checked ? 'text-accent' : 'text-muted'}>{o.icon}</span>
              <span className="flex-1">{o.label}</span>
              {checked && <Check className="size-3.5 text-accent" strokeWidth={2.5} />}
            </button>
          )
        })}
      </div>
    </Popover>
  )
}

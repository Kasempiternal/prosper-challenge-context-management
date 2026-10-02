import { cn } from '../../lib/cn'

export const controlClass = (invalid?: boolean) =>
  cn(
    'w-full rounded-[10px] border bg-card px-3 text-[13.5px] text-ink placeholder:text-faint',
    'transition-[border-color,box-shadow] duration-200 ease-out-soft outline-none',
    'focus:border-accent focus:shadow-focus',
    invalid ? 'border-danger/60' : 'border-border hover:border-border-strong',
  )

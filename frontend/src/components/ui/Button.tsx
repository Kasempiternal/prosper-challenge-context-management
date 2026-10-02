import { motion, type HTMLMotionProps } from 'motion/react'
import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'
import { spring } from '../../lib/motion'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'danger-ghost'
type Size = 'sm' | 'md'

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-accent text-accent-ink shadow-[0_1px_2px_color-mix(in_srgb,var(--color-accent)_30%,transparent)] hover:bg-accent-hover active:bg-accent-pressed',
  secondary: 'bg-card text-ink border border-border hover:border-border-strong hover:bg-surface shadow-card',
  ghost: 'text-ink-soft hover:bg-hover hover:text-ink',
  danger: 'bg-danger text-danger-ink hover:bg-danger-hover',
  'danger-ghost': 'text-danger hover:bg-danger-soft',
}

const SIZES: Record<Size, string> = {
  sm: 'h-8 px-3 text-[13px] gap-1.5',
  md: 'h-9 px-4 text-sm gap-2',
}

export interface ButtonProps extends Omit<HTMLMotionProps<'button'>, 'children'> {
  variant?: Variant
  size?: Size
  icon?: ReactNode
  children?: ReactNode
}

export function Button({ variant = 'secondary', size = 'md', icon, className, children, ...rest }: ButtonProps) {
  return (
    <motion.button
      type="button"
      whileTap={{ scale: 0.97 }}
      transition={spring}
      className={cn(
        'inline-flex shrink-0 select-none items-center justify-center rounded-full font-medium whitespace-nowrap',
        'transition-colors duration-200 ease-out-soft disabled:pointer-events-none disabled:opacity-45',
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {icon}
      {children}
    </motion.button>
  )
}

export interface IconButtonProps extends Omit<HTMLMotionProps<'button'>, 'children'> {
  label: string
  children: ReactNode
  active?: boolean
}

export function IconButton({ label, children, active, className, ...rest }: IconButtonProps) {
  return (
    <motion.button
      type="button"
      aria-label={label}
      title={label}
      whileTap={{ scale: 0.92 }}
      transition={spring}
      className={cn(
        'inline-flex size-8 shrink-0 items-center justify-center rounded-full text-ink-soft',
        'transition-colors duration-200 ease-out-soft hover:bg-hover hover:text-ink',
        'disabled:pointer-events-none disabled:opacity-35',
        active && 'bg-press text-ink',
        className,
      )}
      {...rest}
    >
      {children}
    </motion.button>
  )
}

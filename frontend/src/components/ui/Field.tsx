import { AnimatePresence, motion } from 'motion/react'
import type { ComponentProps, ReactNode } from 'react'
import { AlertCircle } from 'lucide-react'
import { cn } from '../../lib/cn'
import { controlClass } from './control'
import { fade } from '../../lib/motion'

interface FieldProps {
  label: string
  htmlFor?: string
  hint?: ReactNode
  errors?: string[]
  aside?: ReactNode
  children: ReactNode
}

export function Field({ label, htmlFor, hint, errors = [], aside, children }: FieldProps) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={htmlFor} className="text-[12.5px] font-medium text-ink-soft">
          {label}
        </label>
        {aside}
      </div>
      {children}
      {hint && errors.length === 0 && <p className="text-[12px] leading-snug text-muted">{hint}</p>}
      <FieldErrors errors={errors} />
    </div>
  )
}

export function FieldErrors({ errors }: { errors: string[] }) {
  return (
    <AnimatePresence initial={false}>
      {errors.map((message) => (
        <motion.p
          key={message}
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: 'auto' }}
          exit={{ opacity: 0, height: 0 }}
          transition={fade}
          className="flex items-start gap-1.5 overflow-hidden text-[12px] leading-snug text-danger"
        >
          <AlertCircle className="mt-px size-3.5 shrink-0" aria-hidden />
          {message}
        </motion.p>
      ))}
    </AnimatePresence>
  )
}

export function Input({ invalid, className, ...rest }: ComponentProps<'input'> & { invalid?: boolean }) {
  return <input className={cn(controlClass(invalid), 'h-9', className)} {...rest} />
}

export function Textarea({ invalid, className, ...rest }: ComponentProps<'textarea'> & { invalid?: boolean }) {
  return (
    <textarea
      className={cn(controlClass(invalid), 'field-sizing-content min-h-[76px] resize-none py-2.5 leading-relaxed', className)}
      {...rest}
    />
  )
}

export function Select({ invalid, className, children, ...rest }: ComponentProps<'select'> & { invalid?: boolean }) {
  return (
    <select
      className={cn(
        controlClass(invalid),
        'h-9 appearance-none bg-[url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A//www.w3.org/2000/svg%27%20width%3D%2712%27%20height%3D%2712%27%20fill%3D%27none%27%20stroke%3D%27%23676059%27%20stroke-width%3D%271.6%27%20stroke-linecap%3D%27round%27%3E%3Cpath%20d%3D%27M3%204.5l3%203%203-3%27/%3E%3C/svg%3E")] dark:bg-[url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A//www.w3.org/2000/svg%27%20width%3D%2712%27%20height%3D%2712%27%20fill%3D%27none%27%20stroke%3D%27%23a8a097%27%20stroke-width%3D%271.6%27%20stroke-linecap%3D%27round%27%3E%3Cpath%20d%3D%27M3%204.5l3%203%203-3%27/%3E%3C/svg%3E")] bg-[position:right_10px_center] bg-no-repeat pr-8',
        className,
      )}
      {...rest}
    >
      {children}
    </select>
  )
}

import { useEffect, useMemo, useState } from 'react'
import { Check, RotateCcw } from 'lucide-react'
import { cn } from '../../lib/cn'
import { useEditor } from '../../store/editor'
import { Button } from '../ui/Button'
import { FieldErrors } from '../ui/Field'

interface JsonEditorProps<T> {
  value: T
  /** Returns an error message, or null when the parsed value is acceptable. */
  check: (parsed: unknown) => string | null
  onApply: (parsed: T) => void
  hint: string
}

/** Raw JSON view of one node/edge. Unknown keys are edited here and round-trip untouched elsewhere. */
export function JsonEditor<T>({ value, check, onApply, hint }: JsonEditorProps<T>) {
  const source = useMemo(() => JSON.stringify(value, null, 2), [value])
  const [text, setText] = useState(source)
  const [base, setBase] = useState(source)
  if (base !== source) {
    setBase(source)
    setText(source)
  }

  const error = useMemo(() => {
    try {
      return check(JSON.parse(text))
    } catch (err) {
      return err instanceof Error ? err.message : 'Invalid JSON'
    }
  }, [text, check])

  const changed = text !== source

  useEffect(() => {
    const { setJsonPending } = useEditor.getState()
    setJsonPending(changed)
    return () => setJsonPending(false)
  }, [changed])

  return (
    <div className="flex flex-col gap-3">
      <p className="text-[12.5px] leading-snug text-muted">{hint}</p>
      <textarea
        aria-label="Raw JSON"
        spellCheck={false}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && !error && changed) onApply(JSON.parse(text) as T)
        }}
        className={cn(
          'field-sizing-content min-h-[280px] w-full resize-none rounded-[12px] border bg-surface p-3.5 font-mono text-[12px] leading-[1.6] text-ink-soft outline-none placeholder:text-faint',
          'transition-[border-color,box-shadow] duration-200 focus:border-accent focus:shadow-focus',
          error ? 'border-danger/60' : 'border-border',
        )}
      />
      <FieldErrors errors={error ? [error] : []} />
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" disabled={!changed} icon={<RotateCcw className="size-3.5" />} onClick={() => setText(source)}>
          Revert
        </Button>
        <Button
          size="sm"
          variant="primary"
          disabled={!changed || !!error}
          icon={<Check className="size-3.5" />}
          onClick={() => onApply(JSON.parse(text) as T)}
        >
          Apply
        </Button>
      </div>
    </div>
  )
}

import { motion, useAnimate } from 'motion/react'
import { AlertTriangle, Check, CloudOff, Loader2, ChevronRight } from 'lucide-react'
import { useEffect } from 'react'
import { cn } from '../../lib/cn'
import { describeTarget, type Issue } from '../../lib/issues'
import { useEditor } from '../../store/editor'
import { Popover } from '../ui/Popover'

export function ValidationChip() {
  const status = useEditor((s) => s.validation)
  const issues = useEditor((s) => s.issues)
  const shakeNonce = useEditor((s) => s.shakeNonce)
  const doc = useEditor((s) => s.doc)
  const open = useEditor((s) => s.issuesOpen)
  const setOpen = useEditor((s) => s.setIssuesOpen)
  const [scope, animate] = useAnimate()

  useEffect(() => {
    if (shakeNonce && scope.current) {
      void animate(scope.current, { x: [0, -6, 6, -4, 4, -2, 0] }, { duration: 0.45 })
    }
  }, [shakeNonce, animate, scope])

  const invalid = status === 'invalid' && issues.length > 0

  const jump = (issue: Issue) => {
    const { select } = useEditor.getState()
    const t = issue.target
    if (t.kind === 'node') select({ kind: 'node', key: t.key }, { focus: true })
    else if (t.kind === 'edge') select({ kind: 'edge', key: t.key, index: t.index }, { focus: true })
    else select({ kind: 'settings' })
    setOpen(false)
  }

  return (
    <Popover
      open={open && issues.length > 0}
      onClose={() => setOpen(false)}
      className="w-[340px]"
      anchor={
        <motion.button
          ref={scope}
          type="button"
          onClick={() => invalid && setOpen(!open)}
          aria-expanded={open && invalid}
          aria-label={invalid ? `${issues.length} validation issues` : 'Validation status'}
          className={cn(
            'flex h-7 shrink-0 items-center gap-1.5 rounded-full border px-2.5 text-[12px] font-medium transition-colors duration-200',
            invalid && 'cursor-pointer border-danger/25 bg-danger-soft text-danger hover:border-danger/45',
            status === 'valid' && 'cursor-default border-success/20 bg-success-soft text-success',
            (status === 'pending' || status === 'idle' || (status === 'invalid' && !invalid)) &&
              'cursor-default border-border bg-card text-muted',
            status === 'offline' && 'cursor-default border-border bg-card text-muted',
          )}
        >
          {invalid && (
            <>
              <AlertTriangle className="size-3.5" />
              {issues.length} {issues.length === 1 ? 'issue' : 'issues'}
            </>
          )}
          {status === 'valid' && (
            <>
              <Check className="size-3.5" strokeWidth={2.5} />
              Valid
            </>
          )}
          {(status === 'pending' || status === 'idle') && (
            <>
              <Loader2 className="size-3.5 animate-spin" />
              Checking
            </>
          )}
          {status === 'offline' && (
            <>
              <CloudOff className="size-3.5" />
              Can’t validate
            </>
          )}
        </motion.button>
      }
    >
      <p className="px-2.5 pt-1.5 pb-1 text-[11px] font-semibold tracking-[0.06em] text-muted uppercase">
        Fix before saving
      </p>
      <ul className="scrollbar-thin max-h-[320px] overflow-y-auto">
        {doc &&
          issues.map((issue, i) => (
            <li key={`${issue.path}-${i}`}>
              <button
                type="button"
                onClick={() => jump(issue)}
                className="group flex w-full items-start gap-2.5 rounded-[10px] px-2.5 py-2 text-left transition-colors duration-150 hover:bg-raised"
              >
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-danger" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12px] font-medium text-ink-soft">
                    {describeTarget(doc, issue.target)}
                  </span>
                  <span className="block text-[13px] leading-snug text-ink">{issue.message}</span>
                </span>
                <ChevronRight className="mt-0.5 size-3.5 shrink-0 text-muted opacity-0 transition-opacity group-hover:opacity-100" />
              </button>
            </li>
          ))}
      </ul>
    </Popover>
  )
}

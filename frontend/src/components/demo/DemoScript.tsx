import { AnimatePresence, motion } from 'motion/react'
import { Check, ChevronDown, Copy, ScrollText, X } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { AGENT_NAMES, DEMO_BEATS, DEMO_GROUPS, type DemoBeat } from '../../lib/demoScript'
import { cn } from '../../lib/cn'
import { fade, softSpring } from '../../lib/motion'
import { useAgents } from '../../store/agents'
import { useDemo } from '../../store/demo'
import { Button, IconButton } from '../ui/Button'

/**
 * The demo script as a floating card over the canvas, so the lines stay in view during a call.
 * Not modal: the test call panel and the Decisions tab stay usable beside it.
 */
export function DemoScript() {
  const open = useDemo((s) => s.open)
  const done = useDemo((s) => s.done)
  const [expanded, setExpanded] = useState<number | null>(() => DEMO_BEATS.find((b) => !done.includes(b.id))?.id ?? null)

  return (
    <AnimatePresence>
      {open && (
        <motion.aside
          aria-label="Demo script"
          className="absolute bottom-3 left-3 z-20 flex max-h-[calc(100%-1.5rem)] w-[400px] flex-col overflow-hidden rounded-[20px] border border-border-subtle bg-card shadow-panel"
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 12 }}
          transition={softSpring}
        >
          <header className="flex items-center gap-2 border-b border-border-subtle px-4 py-3">
            <ScrollText className="size-4 text-muted" aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="text-[11px] font-semibold tracking-[0.06em] text-muted uppercase">Demo script</p>
              <p className="text-[13px] text-ink-soft">
                {done.length} of {DEMO_BEATS.length} done · click a line to copy it
              </p>
            </div>
            {done.length > 0 && (
              <button
                type="button"
                onClick={useDemo.getState().reset}
                className="text-[12px] text-muted underline-offset-2 hover:text-ink hover:underline"
              >
                Reset
              </button>
            )}
            <IconButton label="Close demo script" onClick={useDemo.getState().close}>
              <X className="size-4" />
            </IconButton>
          </header>
          <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto p-2">
            {DEMO_GROUPS.map((group) => (
              <section key={group.key} aria-label={group.label}>
                <p className="px-2 pt-2 pb-1 text-[11px] font-semibold tracking-[0.06em] text-muted uppercase">{group.label}</p>
                <p className="px-2 pb-1.5 text-[12px] leading-snug text-faint">{group.hint}</p>
                <ol>
                  {DEMO_BEATS.filter((beat) => beat.group === group.key).map((beat) => (
                    <Beat
                      key={beat.id}
                      beat={beat}
                      done={done.includes(beat.id)}
                      expanded={expanded === beat.id}
                      onToggle={() => setExpanded(expanded === beat.id ? null : beat.id)}
                    />
                  ))}
                </ol>
              </section>
            ))}
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  )
}

function Beat({ beat, done, expanded, onToggle }: { beat: DemoBeat; done: boolean; expanded: boolean; onToggle: () => void }) {
  const activeId = useAgents((s) => s.activeId)
  const onThisAgent = activeId === beat.agent
  return (
    <li className="rounded-[14px]">
      <div className="flex items-center gap-2 rounded-[14px] px-2 py-1.5 hover:bg-hover">
        <button
          type="button"
          role="checkbox"
          aria-checked={done}
          aria-label={`Beat ${beat.id} done`}
          onClick={() => useDemo.getState().toggleDone(beat.id)}
          className={cn(
            'flex size-5 shrink-0 items-center justify-center rounded-full border transition-colors',
            done ? 'border-success bg-success text-white' : 'border-border-strong text-transparent hover:border-ink-soft',
          )}
        >
          <Check className="size-3" strokeWidth={3} />
        </button>
        <button type="button" onClick={onToggle} aria-expanded={expanded} className="flex min-w-0 flex-1 items-center gap-2 text-left">
          <span className={cn('truncate text-[13.5px] font-medium', done ? 'text-muted line-through' : 'text-ink')}>
            {beat.id}. {beat.title}
          </span>
          <span className="ml-auto shrink-0 rounded-full bg-raised px-2 py-0.5 text-[11px] text-muted">
            {beat.agent === 'clinic-scheduler' ? 'Clinic' : 'National'}
          </span>
          <ChevronDown className={cn('size-4 shrink-0 text-faint transition-transform', expanded && 'rotate-180')} aria-hidden />
        </button>
      </div>
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={fade}
            className="overflow-hidden"
          >
            <div className="flex flex-col gap-2.5 px-2 pt-1 pb-3 pl-9">
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant={onThisAgent ? 'ghost' : 'secondary'}
                  disabled={onThisAgent}
                  onClick={() => useAgents.getState().requestOpen(beat.agent)}
                >
                  {onThisAgent ? `On ${AGENT_NAMES[beat.agent]}` : `Open ${AGENT_NAMES[beat.agent]}`}
                </Button>
                {beat.setup && <span className="text-[12px] leading-snug text-muted">{beat.setup}</span>}
              </div>
              {beat.steps.map((step, i) => (
                <div key={i} className="flex flex-col gap-1">
                  {step.when && <p className="px-1 text-[12px] font-semibold text-amber-600 dark:text-warning">If {step.when}:</p>}
                  <button
                    type="button"
                    onClick={() => copy(step.say)}
                    title="Copy"
                    className="group flex items-start gap-2 rounded-[12px] border border-border-subtle bg-surface px-3 py-2 text-left text-[13.5px] leading-snug text-ink transition-colors hover:border-border-strong"
                  >
                    <span className="min-w-0 flex-1">“{step.say}”</span>
                    <Copy className="mt-0.5 size-3.5 shrink-0 text-faint group-hover:text-ink-soft" aria-hidden />
                  </button>
                  <p className="px-1 text-[12.5px] leading-snug text-muted">→ {step.expect}</p>
                </div>
              ))}
              {beat.failIf && (
                <p className="px-1 text-[12.5px] leading-snug text-danger">
                  <span className="font-semibold">Fail if</span> {beat.failIf}
                </p>
              )}
              <p className="px-1 text-[12px] leading-snug text-faint">Shows: {beat.why}</p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  )
}

function copy(text: string) {
  navigator.clipboard
    .writeText(text)
    .then(() => toast('Copied', { description: text, duration: 1500 }))
    .catch(() => toast.error('Could not copy; select the line instead'))
}

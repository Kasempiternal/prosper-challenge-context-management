import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { GitBranch } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { naiveBaseline, useCatalogs } from '../../lib/catalogs'
import { cn } from '../../lib/cn'
import { softSpring } from '../../lib/motion'
import { clock } from '../../lib/time'
import { useCall, type TimedDecision } from '../../store/call'
import { useEditor } from '../../store/editor'

const STATUS_STYLE: Record<string, { pill: string; dot: string }> = {
  offer: { pill: 'bg-info-soft text-info', dot: 'bg-info' },
  ask: { pill: 'bg-warning-soft text-warning', dot: 'bg-warning' },
  refuse: { pill: 'bg-danger-soft text-danger', dot: 'bg-danger' },
  confirm: { pill: 'bg-success-soft text-success', dot: 'bg-success' },
}
const OTHER_STATUS = { pill: 'bg-raised text-ink-soft', dot: 'bg-faint' }

export function Decisions() {
  const decisions = useCall((s) => s.decisions)
  const startedAt = useCall((s) => s.startedAt)
  const reduce = useReducedMotion()
  const catalogPath = useEditor((s) => s.doc?.agent.catalog)
  const catalogs = useCatalogs()
  const baseline = naiveBaseline(catalogs?.find((c) => c.path === catalogPath)?.naive_tokens)
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end', behavior: reduce ? 'auto' : 'smooth' })
  }, [decisions.length, reduce])

  const counted = decisions.filter((d) => d.tokens !== null)
  const avgTokens = counted.length ? Math.round(counted.reduce((sum, d) => sum + (d.tokens ?? 0), 0) / counted.length) : null
  const jevCalls = decisions.filter((d) => d.jev).length

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-col gap-2.5 border-b border-border-subtle px-4 py-3">
        <dl className="grid grid-cols-3 gap-3">
          <Stat label="Decisions" value={String(decisions.length)} />
          <Stat label="Avg tokens" value={avgTokens === null ? '—' : String(avgTokens)} />
          <Stat label="JEV calls" value={String(jevCalls)} />
        </dl>
        <p className="flex flex-wrap items-center gap-1.5 text-[11.5px] text-muted">
          Baseline
          <span
            title="Tokens per turn if the whole catalog were pasted into the prompt instead of resolved by tools"
            className={cn(
              'rounded-full border px-2 py-px font-mono text-[10.5px] whitespace-nowrap',
              baseline.exceedsContext ? 'border-danger/40 bg-danger-soft text-danger' : 'border-dashed border-border-strong text-muted',
            )}
          >
            {baseline.label}
          </span>
        </p>
      </div>

      <div className="scrollbar-thin flex min-h-0 flex-1 flex-col overflow-y-auto px-4 py-4">
        {decisions.length === 0 ? (
          <div className="m-auto flex max-w-[250px] flex-col items-center gap-2 text-center">
            <span className="flex size-9 items-center justify-center rounded-full bg-card text-muted shadow-card">
              <GitBranch className="size-4" />
            </span>
            <p className="text-[12.5px] leading-snug text-muted">
              Each scheduling decision appears here: what the resolver chose, what the caller heard, and what it cost in context.
            </p>
          </div>
        ) : (
          <ol className="relative flex flex-col gap-2.5" aria-live="polite">
            <AnimatePresence initial={false}>
              {decisions.map((d, i) => (
                <motion.li
                  key={i}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={softSpring}
                  className="relative pl-5"
                >
                  {i < decisions.length - 1 && <span className="absolute top-6 -bottom-2.5 left-[5px] w-px bg-border-strong" aria-hidden />}
                  <DecisionCard decision={d} elapsed={startedAt === null ? null : d.at - startedAt} />
                </motion.li>
              ))}
            </AnimatePresence>
          </ol>
        )}
        <div ref={end} />
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <dt className="text-[10.5px] font-semibold tracking-[0.06em] text-muted uppercase">{label}</dt>
      <dd className="font-mono text-[16px] font-semibold text-ink tabular-nums">{value}</dd>
    </div>
  )
}

function DecisionCard({ decision: d, elapsed }: { decision: TimedDecision; elapsed: number | null }) {
  const style = STATUS_STYLE[d.status] ?? OTHER_STATUS
  return (
    <>
      <span className={cn('absolute top-[13px] left-0 size-[11px] rounded-full ring-[3px] ring-surface', style.dot)} aria-hidden />
      <article className="flex flex-col gap-2 rounded-[12px] border border-border-subtle bg-card px-3 py-2.5 shadow-card">
        <header className="flex flex-wrap items-center gap-1.5">
          <span className={cn('rounded-full px-2 py-px text-[10.5px] font-bold tracking-[0.06em] uppercase', style.pill)}>{d.status}</span>
          {elapsed !== null && <span className="font-mono text-[11px] text-faint tabular-nums">{clock(elapsed)}</span>}
          {d.tokens !== null && (
            <span title="Tokens in the tool result the LLM saw" className="ml-auto rounded-[6px] bg-raised px-1.5 py-px font-mono text-[10.5px] text-ink-soft tabular-nums">
              {d.tokens} tok
            </span>
          )}
        </header>

        {d.say && <p className="text-[13px] leading-snug text-ink">“{d.say}”</p>}

        {d.offers.length > 0 && (
          <ul className="flex flex-col gap-0.5">
            {d.offers.map((o) => {
              const [n, ...rest] = o.split(' ')
              return (
                <li key={o} className="flex items-baseline gap-2 font-mono text-[11.5px] text-ink-soft">
                  <span className="w-3 shrink-0 text-right text-info">{n}</span>
                  <span className="truncate" title={rest.join(' ')}>
                    {rest.join(' ')}
                  </span>
                </li>
              )
            })}
          </ul>
        )}

        {d.ask && (
          <div className="flex flex-wrap items-center gap-1">
            <span className="font-mono text-[11px] text-warning">{d.ask.field}?</span>
            {d.ask.options.map((o) => (
              <span key={o} className="rounded-full border border-border-subtle bg-surface px-2 py-px text-[11.5px] text-ink-soft">
                {o}
              </span>
            ))}
          </div>
        )}

        {d.reason && <p className="font-mono text-[11px] text-danger">reason: {d.reason}</p>}
        {d.jev && <JevChip p={d.jev.p} ms={d.jev.ms} />}
      </article>
    </>
  )
}

function JevChip({ p, ms }: { p: number | null; ms: number }) {
  return (
    <span
      title="The JEV disambiguator was consulted for this turn"
      className="inline-flex items-center gap-1.5 self-start rounded-[6px] border border-border-subtle bg-surface px-1.5 py-px font-mono text-[10.5px] text-ink-soft tabular-nums"
    >
      JEV
      {p !== null && (
        <>
          <span className="relative h-1 w-8 overflow-hidden rounded-full bg-border-strong" aria-hidden>
            <span className="absolute inset-y-0 left-0 rounded-full bg-info" style={{ width: `${Math.round(p * 100)}%` }} />
          </span>
          p={p.toFixed(2)}
        </>
      )}
      <span className="text-faint">·</span>
      {ms} ms
    </span>
  )
}

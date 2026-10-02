import { AnimatePresence, motion } from 'motion/react'
import { Database } from 'lucide-react'
import { spring } from '../../lib/motion'
import { useCall } from '../../store/call'

function display(value: unknown): string {
  if (value === null || value === undefined) return '—'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

export function CollectedData() {
  const collected = useCall((s) => s.collected)
  const entries = Object.entries(collected)

  return (
    <section className="border-t border-border-subtle bg-card px-4 py-3.5">
      <h3 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold tracking-[0.06em] text-muted uppercase">
        <Database className="size-3" />
        Collected data
      </h3>
      {entries.length === 0 ? (
        <p className="text-[12.5px] text-faint">Nothing collected yet.</p>
      ) : (
        <dl className="scrollbar-thin grid max-h-[140px] grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 overflow-y-auto">
          <AnimatePresence initial={false}>
            {entries.map(([k, v]) => (
              <motion.div
                key={k}
                className="contents"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={spring}
              >
                <dt className="font-mono text-[12px] text-muted">{k}</dt>
                <dd className="truncate text-[12.5px] font-medium text-ink" title={display(v)}>
                  {display(v)}
                </dd>
              </motion.div>
            ))}
          </AnimatePresence>
        </dl>
      )}
    </section>
  )
}

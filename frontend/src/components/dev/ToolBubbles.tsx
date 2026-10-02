import { useStore } from '@xyflow/react'
import { AnimatePresence, motion } from 'motion/react'
import { AlertTriangle, Scale, Wrench } from 'lucide-react'
import { useEffect, useState } from 'react'
import { cn } from '../../lib/cn'
import { softSpring } from '../../lib/motion'
import { useDevView } from '../../store/devView'
import { useTelemetry, type Bubble } from '../../store/telemetry'

const TTL_MS = 4500
const MAX_VISIBLE = 3
const ICON = { tool: Wrench, jev: Scale, warn: AlertTriangle } as const

/** Tool results floating above the live node: they rise in, linger, and fade. */
export function ToolBubbles({ node }: { node: string }) {
  const on = useDevView((s) => s.on)
  const bubbles = useTelemetry((s) => s.bubbles)
  // Bubbles live in the node, so they shrink with the canvas; undo that so they stay readable when zoomed out.
  const zoom = useStore((s) => s.transform[2])
  const [now, setNow] = useState(() => Date.now())
  const visible = on ? bubbles.filter((b) => (b.node === node || b.node === null) && now - b.at < TTL_MS).slice(-MAX_VISIBLE) : []
  const nextExpiry = visible.length ? Math.min(...visible.map((b) => b.at)) + TTL_MS : null

  useEffect(() => {
    if (nextExpiry === null) return
    const t = window.setTimeout(() => setNow(Date.now()), Math.max(50, nextExpiry - Date.now()))
    return () => window.clearTimeout(t)
  }, [nextExpiry])

  return (
    <div
      style={{ scale: Math.min(1.8, Math.max(1, 1 / zoom)), transformOrigin: 'bottom center' }}
      className="pointer-events-none absolute bottom-full left-1/2 mb-3 flex w-max -translate-x-1/2 flex-col items-center gap-1.5"
    >
      <AnimatePresence initial={false}>
        {visible.map((b) => (
          <BubbleChip key={b.id} bubble={b} />
        ))}
      </AnimatePresence>
    </div>
  )
}

function BubbleChip({ bubble }: { bubble: Bubble }) {
  const Icon = ICON[bubble.tone]
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 10, scale: 0.9 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -8, transition: { duration: 0.35 } }}
      transition={softSpring}
      className={cn(
        'flex items-center gap-1.5 rounded-full border bg-card/90 py-1 pr-2.5 pl-1.5 font-mono text-[11px] whitespace-nowrap shadow-lift backdrop-blur-sm',
        bubble.tone === 'warn' ? 'border-danger/40 text-danger' : 'border-border-subtle text-ink',
      )}
    >
      <span
        className={cn(
          'flex size-[18px] items-center justify-center rounded-full',
          bubble.tone === 'tool' ? 'bg-warning-soft text-warning' : bubble.tone === 'jev' ? 'bg-raised text-ink-soft' : 'bg-danger-soft text-danger',
        )}
      >
        <Icon className="size-3" aria-hidden />
      </span>
      <span className="font-medium">{bubble.label}</span>
      <span className="text-muted tabular-nums">· {bubble.detail}</span>
    </motion.div>
  )
}

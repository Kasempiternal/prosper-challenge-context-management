import { useEffect, useState } from 'react'
import { costLines, usePricing, type CostLine } from '../../lib/pricing'
import { useTelemetry } from '../../store/telemetry'

/** Seconds the call has been live, ticking while it is. */
function useCallSeconds(): number {
  const liveFrom = useTelemetry((s) => s.liveFrom)
  const endedAt = useTelemetry((s) => s.endedAt)
  const [now, setNow] = useState(() => Date.now())
  const ticking = liveFrom !== null && endedAt === null
  useEffect(() => {
    if (!ticking) return
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [ticking])
  if (liveFrom === null) return 0
  return Math.max(0, ((endedAt ?? now) - liveFrom) / 1000)
}

export function useCost(): { lines: CostLine[]; total: number } {
  const usage = useTelemetry((s) => s.usage)
  const prices = usePricing((s) => s.prices)
  const callSeconds = useCallSeconds()
  const lines = costLines(usage, usage.sttSeconds ?? callSeconds, prices)
  return { lines, total: lines.reduce((sum, l) => sum + l.usd, 0) }
}

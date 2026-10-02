import { RotateCcw, SlidersHorizontal } from 'lucide-react'
import { useState } from 'react'
import { useCatalogs } from '../../lib/catalogs'
import { cn } from '../../lib/cn'
import { PRICE_FIELDS, usd, usePricing, type CostLine } from '../../lib/pricing'
import { useEditor } from '../../store/editor'
import { useTelemetry } from '../../store/telemetry'
import { Popover } from '../ui/Popover'
import { fmtMs as fmt } from './format'
import { useCost } from './useCost'

const SWATCH: Record<CostLine['key'], string> = {
  openai: 'bg-accent',
  tts: 'bg-success',
  stt: 'bg-info',
  jev: 'bg-ink-soft',
}

export function CostPanel() {
  const { lines, total } = useCost()
  return (
    <div className="scrollbar-thin flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="flex items-end justify-between gap-3 border-b border-border-subtle px-4 py-3">
        <div>
          <p className="text-[10.5px] font-medium tracking-[0.06em] text-faint uppercase">This call · running total</p>
          <p className="mt-1 font-mono text-[26px] leading-none font-semibold tracking-[-0.02em] text-ink tabular-nums">{usd(total)}</p>
        </div>
        <PricesButton />
      </div>

      <div className="px-4 pt-3">
        <div className="flex h-1.5 overflow-hidden rounded-full bg-raised" aria-hidden>
          {total > 0 &&
            lines.map((l) => <span key={l.key} className={SWATCH[l.key]} style={{ width: `${(l.usd / total) * 100}%` }} />)}
        </div>
        <ul className="mt-2.5 flex flex-col">
          {lines.map((l) => (
            <li key={l.key} className="grid grid-cols-[1fr_auto] items-baseline gap-x-3 py-1.5">
              <span className="flex items-center gap-1.5 text-[12.5px] font-medium text-ink-soft">
                <span className={cn('h-2 w-2 shrink-0 rounded-[2px]', SWATCH[l.key])} aria-hidden />
                {l.label}
                {l.estimate && <span className="rounded-full bg-raised px-1.5 text-[10px] font-medium text-muted">est.</span>}
              </span>
              <span className="text-right font-mono text-[12.5px] text-ink tabular-nums">{usd(l.usd)}</span>
              <span className="col-span-2 pl-3.5 font-mono text-[11px] text-muted tabular-nums">{l.detail}</span>
            </li>
          ))}
        </ul>
      </div>

      <ContextGauge />
    </div>
  )
}

function PricesButton() {
  const [open, setOpen] = useState(false)
  const prices = usePricing((s) => s.prices)
  const { setPrice, resetPrices } = usePricing.getState()
  return (
    <Popover
      open={open}
      onClose={() => setOpen(false)}
      align="end"
      className="w-[264px] p-3"
      anchor={
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="flex h-7 items-center gap-1.5 rounded-full border border-border bg-card px-2.5 text-[12px] font-medium text-ink-soft transition-colors duration-150 hover:border-border-strong hover:text-ink"
        >
          <SlidersHorizontal className="size-3.5" aria-hidden />
          Prices
        </button>
      }
    >
      <div className="flex items-center justify-between pb-2">
        <p className="text-[11px] font-semibold tracking-[0.06em] text-muted uppercase">Unit prices</p>
        <button
          type="button"
          onClick={resetPrices}
          className="flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[11.5px] text-muted hover:bg-hover hover:text-ink"
        >
          <RotateCcw className="size-3" aria-hidden />
          Defaults
        </button>
      </div>
      <div className="flex flex-col gap-2">
        {PRICE_FIELDS.map((f) => (
          <label key={f.key} className="grid grid-cols-[1fr_84px] items-center gap-2">
            <span className="flex flex-col leading-tight">
              <span className="text-[12.5px] text-ink-soft">{f.label}</span>
              <span className="text-[10.5px] text-faint">{f.unit}</span>
            </span>
            <input
              type="number"
              min={0}
              step="any"
              value={prices[f.key]}
              onChange={(e) => {
                const v = e.currentTarget.valueAsNumber
                if (Number.isFinite(v) && v >= 0) setPrice(f.key, v)
              }}
              className="h-8 w-full rounded-[8px] border border-border bg-surface px-2 text-right font-mono text-[12.5px] text-ink tabular-nums outline-none focus:border-accent"
            />
          </label>
        ))}
      </div>
    </Popover>
  )
}

function ContextGauge() {
  const calls = useTelemetry((s) => s.usage.promptPerCall)
  const catalogPath = useEditor((s) => s.doc?.agent.catalog)
  const catalogs = useCatalogs()
  const naive = catalogs?.find((c) => c.path === catalogPath)?.naive_tokens ?? null
  const latest = calls.at(-1) ?? null

  return (
    <div className="mx-4 mt-2 mb-4 rounded-[10px] border border-border-subtle bg-surface px-3 py-2.5">
      <div className="flex items-baseline justify-between">
        <p className="text-[10.5px] font-medium tracking-[0.06em] text-faint uppercase">Prompt tokens per LLM call</p>
        <p className="font-mono text-[13px] font-semibold text-ink tabular-nums">{latest === null ? '—' : fmt(latest)}</p>
      </div>
      <Sparkline values={calls} />
      <p className="mt-1.5 text-[12px] leading-snug text-muted">
        {latest === null ? (
          'Fills in after the first LLM call.'
        ) : naive ? (
          <>
            <span className="font-mono text-ink tabular-nums">≈ {fmt(latest)}</span> tok vs{' '}
            <span className="font-mono tabular-nums">{fmt(naive)}</span> naive{' '}
            <span className="font-mono font-semibold text-success tabular-nums">({fmt(naive / latest)}× smaller)</span>
          </>
        ) : (
          <>
            <span className="font-mono text-ink tabular-nums">≈ {fmt(latest)}</span> tok in the last prompt
          </>
        )}
      </p>
    </div>
  )
}

function Sparkline({ values }: { values: number[] }) {
  const W = 100
  const H = 32
  if (values.length < 2) return <div className="mt-2 h-8 rounded-[4px] bg-raised/60" aria-hidden />
  const max = Math.max(...values)
  const min = Math.min(...values)
  const span = max - min || 1
  const pts = values.map((v, i) => [(i / (values.length - 1)) * W, H - 3 - ((v - min) / span) * (H - 6)] as const)
  const line = pts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(' ')
  const [lx, ly] = pts[pts.length - 1]
  return (
    <div className="relative mt-2 h-8">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="absolute inset-0 h-full w-full" aria-hidden>
        <polygon points={`0,${H} ${line} ${W},${H}`} className="fill-accent/10" />
        <polyline points={line} fill="none" className="stroke-accent" strokeWidth={1.5} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      </svg>
      <span
        className="absolute size-[7px] -translate-x-1/2 -translate-y-1/2 rounded-full border-[1.5px] border-card bg-accent"
        style={{ left: `${(lx / W) * 100}%`, top: `${(ly / H) * 100}%` }}
        aria-hidden
      />
    </div>
  )
}

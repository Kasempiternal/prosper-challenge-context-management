import { motion } from 'motion/react'
import { Timer } from 'lucide-react'
import { useState } from 'react'
import { cn } from '../../lib/cn'
import { softSpring } from '../../lib/motion'
import { useTelemetry, type Segment, type SegmentKind, type Turn } from '../../store/telemetry'
import { fmtMs } from './format'

const KIND: Record<SegmentKind, { label: string; bar: string; lane: 0 | 1 }> = {
  stt: { label: 'STT final', bar: 'bg-info', lane: 0 },
  llm: { label: 'LLM TTFB', bar: 'bg-accent', lane: 0 },
  tts: { label: 'TTS TTFB', bar: 'bg-success', lane: 0 },
  tool: { label: 'Tool call', bar: 'bg-warning/35', lane: 1 },
  resolver: { label: 'Resolver', bar: 'bg-warning', lane: 1 },
  model: { label: 'Disambiguator', bar: 'bg-ink-soft', lane: 1 },
}
const LEGEND: SegmentKind[] = ['stt', 'llm', 'tool', 'resolver', 'model', 'tts']
const DRAW_ORDER: SegmentKind[] = ['stt', 'llm', 'tts', 'tool', 'resolver', 'model']

const extent = (t: Turn) => Math.max(t.totalMs ?? 0, ...t.segments.map((g) => g.start + g.ms))

function median(values: number[]): number | null {
  if (values.length === 0) return null
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}

export function LatencyWaterfall() {
  const turns = useTelemetry((s) => s.turns)
  const [picked, setPicked] = useState<number | null>(null)
  const newestFirst = [...turns].reverse()
  const selected = turns.find((t) => t.id === picked) ?? turns.at(-1) ?? null
  const scale = Math.max(1000, ...turns.map(extent))
  const totals = turns.flatMap((t) => (t.totalMs === null ? [] : [t.totalMs]))
  const p50 = median(totals)
  const latest = turns.at(-1)?.totalMs ?? null

  if (turns.length === 0) {
    return (
      <div className="m-auto flex max-w-[250px] flex-col items-center gap-2 px-4 text-center">
        <span className="flex size-9 items-center justify-center rounded-full bg-card text-muted shadow-card">
          <Timer className="size-4" />
        </span>
        <p className="text-[12.5px] leading-snug text-muted">
          Each turn is timed from the moment you stop speaking until the agent’s voice starts.
        </p>
      </div>
    )
  }

  return (
    <div className="scrollbar-thin flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="flex items-end justify-between gap-3 border-b border-border-subtle px-4 py-3">
        <div>
          <p className="text-[10.5px] font-medium tracking-[0.06em] text-faint uppercase">Voice-to-voice · last turn</p>
          <p className="mt-1 font-mono text-[26px] leading-none font-semibold tracking-[-0.02em] text-ink tabular-nums">
            {latest === null ? '—' : fmtMs(latest)}
            <span className="ml-1 text-[13px] font-normal text-muted">ms</span>
          </p>
        </div>
        <p className="pb-0.5 text-right font-mono text-[11.5px] text-muted tabular-nums">
          p50 {p50 === null ? '—' : `${fmtMs(p50)} ms`}
          <br />
          {totals.length} {totals.length === 1 ? 'turn' : 'turns'}
        </p>
      </div>

      <ul className="flex flex-wrap gap-x-3 gap-y-1 px-4 pt-3" aria-label="Legend">
        {LEGEND.map((k) => (
          <li key={k} className="flex items-center gap-1.5 text-[11px] text-muted">
            <span className={cn('h-2 w-2 rounded-[2px]', KIND[k].bar)} aria-hidden />
            {KIND[k].label}
          </li>
        ))}
      </ul>

      <ol className="flex flex-col gap-1 px-2 pt-2">
        {newestFirst.map((turn) => (
          <li key={turn.id}>
            <TurnRow
              turn={turn}
              index={turns.indexOf(turn) + 1}
              scale={scale}
              selected={turn === selected}
              onSelect={() => setPicked(turn.id)}
            />
          </li>
        ))}
      </ol>

      {selected && <Breakdown turn={selected} />}
    </div>
  )
}

function TurnRow({ turn, index, scale, selected, onSelect }: { turn: Turn; index: number; scale: number; selected: boolean; onSelect: () => void }) {
  const pct = (v: number) => `${(v / scale) * 100}%`
  const ordered = [...turn.segments].sort((a, b) => DRAW_ORDER.indexOf(a.kind) - DRAW_ORDER.indexOf(b.kind))
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`Turn ${index}: ${turn.totalMs === null ? 'waiting for the agent' : `${fmtMs(turn.totalMs)} ms voice to voice`}`}
      className={cn(
        'grid w-full grid-cols-[26px_1fr_58px] items-center gap-2 rounded-[8px] px-2 py-1.5 text-left transition-colors duration-150',
        selected ? 'bg-raised' : 'hover:bg-hover',
      )}
    >
      <span className="font-mono text-[10.5px] text-faint">T{index}</span>
      <span className="relative h-[22px] overflow-hidden rounded-[4px] bg-surface">
        {ordered.map((g, i) => (
          <motion.span
            key={i}
            title={`${g.label}: ${fmtMs(g.ms)} ms at +${fmtMs(g.start)} ms`}
            initial={{ scaleX: 0 }}
            animate={{ scaleX: 1 }}
            transition={softSpring}
            style={{ left: pct(g.start), width: `max(2px, ${pct(g.ms)})`, originX: 0 }}
            className={cn('absolute rounded-[2px]', KIND[g.kind].bar, KIND[g.kind].lane === 0 ? 'top-[3px] h-[8px]' : 'top-[13px] h-[6px]')}
          />
        ))}
        {turn.totalMs !== null && (
          <span className="absolute top-0 bottom-0 w-[2px] rounded-full bg-ink" style={{ left: `calc(${pct(turn.totalMs)} - 1px)` }} aria-hidden />
        )}
      </span>
      <span className={cn('text-right font-mono text-[11.5px] tabular-nums', turn.totalMs === null ? 'text-faint' : 'text-ink')}>
        {turn.totalMs === null ? '…' : `${fmtMs(turn.totalMs)}`}
        {turn.totalMs !== null && <span className="text-[10px] text-muted"> ms</span>}
      </span>
    </button>
  )
}

function Breakdown({ turn }: { turn: Turn }) {
  const rows: Segment[] = [...turn.segments].sort((a, b) => a.start - b.start)
  return (
    <div className="mx-4 mt-3 mb-4 rounded-[10px] border border-border-subtle bg-surface px-3 py-2.5">
      <p className="mb-1.5 text-[10.5px] font-medium tracking-[0.06em] text-faint uppercase">
        Breakdown{turn.node ? ` · ${turn.node}` : ''}
      </p>
      <table className="w-full font-mono text-[11.5px] tabular-nums">
        <tbody>
          {rows.map((g, i) => (
            <tr key={i}>
              <td className="py-[3px]">
                <span className="flex items-center gap-1.5 font-sans text-ink-soft">
                  <span className={cn('h-2 w-2 shrink-0 rounded-[2px]', KIND[g.kind].bar)} aria-hidden />
                  {g.label}
                </span>
              </td>
              <td className="py-[3px] text-right text-faint">+{fmtMs(g.start)}</td>
              <td className="w-[64px] py-[3px] text-right text-ink">{fmtMs(g.ms)} ms</td>
            </tr>
          ))}
          <tr className="border-t border-border-subtle">
            <td className="pt-1.5 font-sans font-medium text-ink">Voice-to-voice</td>
            <td />
            <td className="pt-1.5 text-right font-semibold text-ink">{turn.totalMs === null ? '…' : `${fmtMs(turn.totalMs)} ms`}</td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

import { AnimatePresence, motion } from 'motion/react'
import { AudioLines, Brain, Mic, Speaker, Volume2, Wrench, type LucideIcon } from 'lucide-react'
import { Fragment } from 'react'
import { cn } from '../../lib/cn'
import { softSpring } from '../../lib/motion'
import { usd } from '../../lib/pricing'
import type { Stage } from '../../lib/telemetry'
import { STAGES, useTelemetry, type StageState } from '../../store/telemetry'
import { fmtMs as ms } from './format'
import { useCost } from './useCost'

const META: Record<Stage, { label: string; vendor: string; icon: LucideIcon; metric: string }> = {
  mic: { label: 'Mic', vendor: 'caller', icon: Mic, metric: '' },
  stt: { label: 'STT', vendor: 'ElevenLabs', icon: AudioLines, metric: 'TTFB' },
  llm: { label: 'LLM', vendor: 'OpenAI', icon: Brain, metric: 'TTFB' },
  tools: { label: 'Tools', vendor: 'resolver · JEV', icon: Wrench, metric: 'last call' },
  tts: { label: 'TTS', vendor: 'ElevenLabs', icon: Volume2, metric: 'TTFB' },
  speaker: { label: 'Speaker', vendor: 'agent', icon: Speaker, metric: '' },
}

export function PipelineStrip() {
  const stages = useTelemetry((s) => s.stages)
  const v2v = stages.speaker.ms
  const { total } = useCost()

  return (
    <div className="pointer-events-none absolute top-4 right-[calc(var(--panel-inset)+16px)] left-[140px] z-10 flex justify-center transition-[right] duration-300 ease-out-soft">
      <motion.div
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -8 }}
        transition={softSpring}
        role="group"
        aria-label="Voice pipeline"
        className="pointer-events-auto flex max-w-full items-center overflow-hidden rounded-[14px] border border-border-subtle bg-card/75 p-1 shadow-lift backdrop-blur-md"
      >
        {STAGES.map((stage, i) => (
          <Fragment key={stage}>
            {i > 0 && (
              <span
                aria-hidden
                className={cn(
                  'h-px w-1.5 shrink-0 transition-colors duration-200',
                  stages[STAGES[i - 1]].active || stages[stage].active ? 'bg-accent' : 'bg-border-strong',
                )}
              />
            )}
            <StageChip stage={stage} state={stages[stage]} />
          </Fragment>
        ))}
        <span className="mx-1.5 h-7 w-px shrink-0 bg-border" aria-hidden />
        <Readout label="voice→voice" value={v2v === null ? '—' : ms(v2v)} unit={v2v === null ? '' : 'ms'} strong />
        <Readout label="cost" value={usd(total)} />
      </motion.div>
    </div>
  )
}

function StageChip({ stage, state }: { stage: Stage; state: StageState }) {
  const { label, vendor, icon: Icon, metric } = META[stage]
  const latency = stage === 'speaker' || stage === 'mic' ? null : state.ms
  return (
    <div
      title={`${label} · ${vendor}${latency === null ? '' : ` · ${metric} ${ms(latency)} ms`}`}
      className={cn(
        'flex shrink-0 items-center gap-1.5 rounded-[10px] border py-1 pr-1.5 pl-1 transition-colors duration-200',
        state.active ? 'border-accent/35 bg-accent-soft' : 'border-transparent',
      )}
    >
      <span
        className={cn(
          'relative flex size-6 items-center justify-center rounded-[7px] transition-colors duration-200',
          state.active ? 'bg-accent text-accent-ink' : 'bg-raised text-muted',
        )}
      >
        <Icon className="size-3.5" aria-hidden />
        {state.active && <span className="absolute -inset-0.5 animate-ping rounded-[8px] border border-accent/50" aria-hidden />}
      </span>
      <span className="flex flex-col leading-none">
        <span className="flex items-baseline gap-1.5">
          <span className={cn('text-[11.5px] font-semibold', state.active ? 'text-ink' : 'text-ink-soft')}>{label}</span>
          {latency !== null && (
            <AnimatePresence mode="popLayout" initial={false}>
              <motion.span
                key={latency}
                initial={{ opacity: 0, y: -3 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 3 }}
                transition={{ duration: 0.18 }}
                className="font-mono text-[10.5px] text-muted tabular-nums"
              >
                {ms(latency)}
                <span className="text-faint">ms</span>
              </motion.span>
            </AnimatePresence>
          )}
        </span>
        <span className="mt-[3px] text-[9.5px] whitespace-nowrap text-faint">{vendor}</span>
      </span>
    </div>
  )
}

function Readout({ label, value, unit, strong }: { label: string; value: string; unit?: string; strong?: boolean }) {
  return (
    <div className="flex shrink-0 flex-col items-end px-1.5 leading-none">
      <span className="text-[9.5px] font-medium tracking-[0.06em] text-faint uppercase">{label}</span>
      <span className={cn('mt-1 font-mono tabular-nums', strong ? 'text-[14px] font-semibold text-ink' : 'text-[12.5px] text-ink-soft')}>
        {value}
        {unit && <span className="ml-0.5 text-[10.5px] font-normal text-muted">{unit}</span>}
      </span>
    </div>
  )
}

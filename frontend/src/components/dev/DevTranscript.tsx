import { useReducedMotion } from 'motion/react'
import { ArrowRight, MessageSquareText } from 'lucide-react'
import { useEffect, useRef, type ReactNode } from 'react'
import { cn } from '../../lib/cn'
import { useTelemetry, type Line } from '../../store/telemetry'

export function DevTranscript() {
  const lines = useTelemetry((s) => s.lines)
  const streaming = useTelemetry((s) => s.stages.llm.active)
  const reduce = useReducedMotion()
  const end = useRef<HTMLDivElement>(null)
  const visible = lines.filter((l) => l.role !== 'bot' || l.llm || l.tts)

  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end', behavior: reduce ? 'auto' : 'smooth' })
  }, [lines, reduce])

  if (visible.length === 0) {
    return (
      <div className="m-auto flex max-w-[250px] flex-col items-center gap-2 px-4 text-center">
        <span className="flex size-9 items-center justify-center rounded-full bg-card text-muted shadow-card">
          <MessageSquareText className="size-4" />
        </span>
        <p className="text-[12.5px] leading-snug text-muted">
          Raw pipeline text: interim and final STT, LLM tokens as they stream, and what TTS actually spoke.
        </p>
      </div>
    )
  }

  return (
    <div className="scrollbar-thin flex min-h-0 flex-1 flex-col overflow-y-auto px-4 py-3">
      <ol className="flex flex-col gap-2.5">
        {visible.map((line, i) => (
          <li key={line.id}>
            <LineView line={line} caret={streaming && i === visible.length - 1} />
          </li>
        ))}
      </ol>
      <div ref={end} />
    </div>
  )
}

function LineView({ line, caret }: { line: Line; caret: boolean }) {
  if (line.role === 'edge') {
    return (
      <div className="flex items-center gap-1.5 py-0.5 font-mono text-[10.5px] text-muted">
        <span className="h-px flex-1 bg-border-subtle" />
        <ArrowRight className="size-3 text-accent" aria-hidden />
        <span className="text-ink-soft">{line.fn || 'transition'}</span>
        <span className="text-faint">→ {line.to}</span>
        <span className="h-px flex-1 bg-border-subtle" />
      </div>
    )
  }
  if (line.role === 'user') {
    return (
      <Row tag="STT" tagClass="text-info">
        <span className="text-ink">{line.final}</span>
        {line.interim && <span className="text-muted italic">{line.final ? ' ' : ''}{line.interim}</span>}
      </Row>
    )
  }
  return (
    <div className="flex flex-col gap-1">
      {line.llm && (
        <Row tag="LLM" tagClass="text-accent">
          <span className="text-ink-soft">{line.llm}</span>
          {caret && line.open && <span className="ml-0.5 inline-block h-3 w-[5px] translate-y-px animate-pulse bg-accent" aria-hidden />}
        </Row>
      )}
      {line.tts && (
        <Row tag="TTS" tagClass="text-success">
          <span className="text-muted">{line.tts}</span>
        </Row>
      )}
    </div>
  )
}

function Row({ tag, tagClass, children }: { tag: string; tagClass: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[30px_1fr] gap-2 text-[12.5px] leading-snug">
      <span className={cn('pt-px font-mono text-[10px] font-semibold tracking-[0.04em]', tagClass)}>{tag}</span>
      <p className="min-w-0 break-words">{children}</p>
    </div>
  )
}

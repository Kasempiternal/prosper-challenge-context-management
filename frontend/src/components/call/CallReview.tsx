import { usePipecatConversation } from '@pipecat-ai/client-react'
import { AnimatePresence, motion } from 'motion/react'
import { AlertTriangle, Check, RotateCcw, Sparkles, X } from 'lucide-react'
import { useCallback, useEffect, useMemo } from 'react'
import { cn } from '../../lib/cn'
import { checkVerdict } from '../../lib/review'
import { softSpring } from '../../lib/motion'
import { useCall } from '../../store/call'
import { useEditor } from '../../store/editor'
import type { CheckName, GradeOutcome, GradeResult, GradeTurn } from '../../types/grade'
import { Button } from '../ui/Button'
import { plainText } from './messageText'

const OUTCOME: Record<GradeOutcome, { label: string; pill: string }> = {
  booked: { label: 'Booked', pill: 'bg-success-soft text-success' },
  refused_correctly: { label: 'Refused correctly', pill: 'bg-info-soft text-info' },
  handed_off: { label: 'Handed off', pill: 'bg-warning-soft text-warning' },
  abandoned: { label: 'Abandoned', pill: 'bg-danger-soft text-danger' },
  unclear: { label: 'Unclear', pill: 'bg-raised text-ink-soft' },
}

/** `passWhenYes`: the check passes when JEV answers yes to its question. */
const CHECKS: { name: CheckName; passWhenYes: boolean; pass: string; fail: string; question: string }[] = [
  {
    name: 'booked_correctly',
    passWhenYes: true,
    pass: 'Booked what was asked',
    fail: 'Missed what was asked',
    question: 'Did the agent book (or correctly refuse) what the caller actually asked for, honoring their stated preferences?',
  },
  {
    name: 'unnecessary_questions',
    passWhenYes: false,
    pass: 'No unnecessary questions',
    fail: 'Asked an unnecessary question',
    question: "Did the agent ask any question whose answer it already had or that didn't change the outcome?",
  },
  {
    name: 'unsupported_claims',
    passWhenYes: false,
    pass: 'Every fact was supported',
    fail: 'Stated an unsupported fact',
    question: 'Did the agent state any fact (doctor, time, location, policy) not supported by the decisions/tool results shown?',
  },
]

const EFFORT_LABELS = ['very easy', 'easy', 'moderate', 'hard', 'very hard']

function gradeTurns(messages: ReturnType<typeof usePipecatConversation>['messages']): GradeTurn[] {
  return messages.flatMap((m) => {
    if (m.role !== 'user' && m.role !== 'assistant') return []
    const text = plainText(m)
    return text ? [{ role: m.role === 'user' ? 'user' : 'bot', text }] : []
  })
}

export function CallReview() {
  const status = useCall((s) => s.status)
  const review = useCall((s) => s.review)
  const { messages } = usePipecatConversation()
  const transcript = useMemo(() => gradeTurns(messages), [messages])
  const canGrade = status === 'ended' && transcript.length > 0

  const grade = useCallback(
    () => void useCall.getState().grade(transcript, useEditor.getState().doc?.agent),
    [transcript],
  )

  // Only an idle review auto-grades, so this fires once per ended call; re-grading is the button's job.
  useEffect(() => {
    if (canGrade && useCall.getState().review.status === 'idle') grade()
  }, [canGrade, grade])

  if (status !== 'ended' || (review.status === 'idle' && !canGrade)) return null

  return (
    <motion.section
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={softSpring}
      aria-labelledby="call-review-title"
      aria-busy={review.status === 'grading'}
      className="w-full rounded-[14px] border border-border-subtle bg-card px-3.5 py-3 shadow-card"
    >
      <header className="mb-2.5 flex items-center gap-2">
        <Sparkles className="size-3.5 text-accent" aria-hidden />
        <h3 id="call-review-title" className="text-[11px] font-semibold tracking-[0.06em] text-muted uppercase">
          Call review
        </h3>
        {review.status === 'done' && (
          <span
            title={`${review.result.inputTokens} input tokens · $${review.result.usd.toFixed(6)}`}
            className="font-mono text-[10.5px] text-faint tabular-nums"
          >
            JEV · {review.result.ms} ms
          </span>
        )}
        <Button
          variant="ghost"
          size="sm"
          className="-my-1 ml-auto h-7 px-2.5 text-[12px]"
          icon={<RotateCcw className="size-3" aria-hidden />}
          disabled={review.status === 'grading' || !canGrade}
          onClick={grade}
        >
          Re-grade
        </Button>
      </header>

      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={review.status}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
        >
          {review.status === 'done' ? (
            <Result result={review.result} />
          ) : review.status === 'error' ? (
            <p role="alert" className="flex items-start gap-1.5 text-[12.5px] leading-snug font-medium text-danger">
              <AlertTriangle className="mt-px size-3.5 shrink-0" aria-hidden />
              {review.message}
            </p>
          ) : (
            <Grading />
          )}
        </motion.div>
      </AnimatePresence>
    </motion.section>
  )
}

function Grading() {
  return (
    <div className="flex flex-col gap-2" role="status">
      <span className="text-[12.5px] text-muted">Grading with JEV…</span>
      <span className="skeleton h-5 w-24 rounded-full motion-reduce:animate-none" aria-hidden />
      {[0.8, 0.7, 0.75].map((w, i) => (
        <span key={i} className="skeleton h-3.5 rounded-full motion-reduce:animate-none" style={{ width: `${w * 100}%` }} aria-hidden />
      ))}
    </div>
  )
}

function Result({ result }: { result: GradeResult }) {
  const outcome = OUTCOME[result.outcome.choice]
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <span className={cn('rounded-full px-2.5 py-0.5 text-[11px] font-bold tracking-[0.06em] uppercase', outcome.pill)}>
          {outcome.label}
        </span>
        <span className="font-mono text-[11px] text-muted tabular-nums">p={result.outcome.confidence.toFixed(2)}</span>
      </div>

      <ul className="flex flex-col gap-1.5">
        {CHECKS.map((c) => {
          const yes = result.checks[c.name]
          const verdict = checkVerdict(yes, c.passWhenYes)
          const p = yes >= 0.5 ? yes : 1 - yes
          return (
            <li key={c.name} title={c.question} className="flex items-center gap-2 text-[12.5px]">
              <span
                className={cn(
                  'flex size-4 shrink-0 items-center justify-center rounded-full',
                  verdict === 'pass' && 'bg-success-soft text-success',
                  verdict === 'fail' && 'bg-danger-soft text-danger',
                  verdict === 'unclear' && 'bg-warning-soft text-warning',
                )}
              >
                {verdict === 'pass' ? (
                  <Check className="size-2.5" strokeWidth={3} aria-hidden />
                ) : verdict === 'fail' ? (
                  <X className="size-2.5" strokeWidth={3} aria-hidden />
                ) : (
                  <span className="text-[10px] leading-none font-bold" aria-hidden>?</span>
                )}
              </span>
              <span
                className={cn(
                  'min-w-0 flex-1 truncate',
                  verdict === 'pass' && 'text-ink',
                  verdict === 'fail' && 'font-medium text-danger',
                  verdict === 'unclear' && 'text-ink-soft',
                )}
              >
                <span className="sr-only">{verdict === 'pass' ? 'Pass: ' : verdict === 'fail' ? 'Fail: ' : 'Unclear: '}</span>
                {verdict === 'fail' ? c.fail : verdict === 'pass' ? c.pass : `Unclear: ${c.pass.charAt(0).toLowerCase()}${c.pass.slice(1)}`}
              </span>
              <span className="font-mono text-[11px] text-muted tabular-nums">{p.toFixed(2)}</span>
            </li>
          )
        })}
      </ul>

      <EffortMeter level={result.effort} />
    </div>
  )
}

function EffortMeter({ level }: { level: number }) {
  const step = Math.min(5, Math.max(1, Math.round(level)))
  const tone = step <= 2 ? 'bg-success' : step === 3 ? 'bg-warning' : 'bg-danger'
  return (
    <div className="flex items-center gap-2.5 border-t border-border-subtle pt-2.5">
      <span className="text-[11px] font-semibold tracking-[0.06em] text-muted uppercase">Caller effort</span>
      <span
        role="meter"
        aria-label="Caller effort"
        aria-valuemin={1}
        aria-valuemax={5}
        aria-valuenow={level}
        aria-valuetext={`${level.toFixed(1)} of 5, ${EFFORT_LABELS[step - 1]}`}
        className="flex gap-[3px]"
      >
        {[1, 2, 3, 4, 5].map((i) => (
          <span key={i} className={cn('h-2 w-4 rounded-full', i <= step ? tone : 'bg-border')} />
        ))}
      </span>
      <span className="ml-auto text-[12px] text-ink-soft">
        <span className="font-mono tabular-nums">{level.toFixed(1)}</span> · {EFFORT_LABELS[step - 1]}
      </span>
    </div>
  )
}

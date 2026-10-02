import { usePipecatConversation, type ConversationMessage } from '@pipecat-ai/client-react'
import { motion, useReducedMotion } from 'motion/react'
import { ArrowRight, MessageSquareText } from 'lucide-react'
import { useEffect, useRef, type ReactNode } from 'react'
import { cn } from '../../lib/cn'
import { softSpring } from '../../lib/motion'
import { isBotText, plainText } from './messageText'

function MessageText({ message }: { message: ConversationMessage }) {
  return (
    <>
      {message.parts.map((part, i) => {
        const sep = part.needsSeparator || i > 0 ? ' ' : ''
        if (isBotText(part.text)) {
          return (
            <span key={i}>
              {sep}
              {part.text.spoken}
              <span className="opacity-55">{part.text.unspoken}</span>
            </span>
          )
        }
        return (
          <span key={i}>
            {sep}
            {part.text as ReactNode}
          </span>
        )
      })}
    </>
  )
}


export function Transcript() {
  const { messages } = usePipecatConversation()
  const reduce = useReducedMotion()
  const end = useRef<HTMLDivElement>(null)
  const visible = messages.filter((m) => m.role !== 'system')
  const lastFinal = visible.findLast((m) => (m.role === 'user' || m.role === 'assistant') && m.final !== false)

  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end', behavior: reduce ? 'auto' : 'smooth' })
  }, [messages, reduce])

  return (
    <div className="scrollbar-thin flex min-h-0 flex-1 flex-col overflow-y-auto px-4 py-4">
      {/* Only finished turns are announced; streaming partials would make screen readers stutter. */}
      <p className="sr-only" aria-live="polite">
        {lastFinal ? `${lastFinal.role === 'user' ? 'You' : 'Agent'}: ${plainText(lastFinal)}` : ''}
      </p>
      {visible.length === 0 ? (
        <div className="m-auto flex max-w-[240px] flex-col items-center gap-2 text-center">
          <span className="flex size-9 items-center justify-center rounded-full bg-card text-muted shadow-card">
            <MessageSquareText className="size-4" />
          </span>
          <p className="text-[12.5px] leading-snug text-muted">The live transcript appears here once the call starts.</p>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {visible.map((m, i) =>
            m.role === 'function_call' ? (
              <motion.div
                key={i}
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={softSpring}
                className="my-1 flex items-center justify-center gap-1.5 text-[11.5px] text-muted"
              >
                <span className="h-px w-6 bg-border" />
                <ArrowRight className="size-3 text-accent" />
                <span className="font-mono">{m.functionCall?.function_name ?? 'transition'}</span>
                <span className="h-px w-6 bg-border" />
              </motion.div>
            ) : (
              <motion.div
                key={i}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={softSpring}
                className={cn(
                  'max-w-[85%] rounded-[16px] px-3.5 py-2 text-[13px] leading-relaxed',
                  m.role === 'user'
                    ? 'self-end rounded-br-[6px] bg-ink text-bg'
                    : 'self-start rounded-bl-[6px] border border-border-subtle bg-card text-ink shadow-card',
                  m.final === false && 'opacity-80',
                )}
              >
                <MessageText message={m} />
              </motion.div>
            ),
          )}
          <div ref={end} />
        </div>
      )}
    </div>
  )
}

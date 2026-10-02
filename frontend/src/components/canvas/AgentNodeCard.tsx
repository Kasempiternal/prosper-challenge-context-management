import { Handle, Position, type NodeProps } from '@xyflow/react'
import { motion } from 'motion/react'
import { AlertTriangle, ArrowRight, Check, PhoneOff, RotateCcw, Wrench } from 'lucide-react'
import { memo } from 'react'
import { cn } from '../../lib/cn'
import { softSpring } from '../../lib/motion'
import { NODE_WIDTH } from '../../lib/layout'
import type { AgentFlowNode } from './types'

function AgentNodeCardImpl({ data, selected }: NodeProps<AgentFlowNode>) {
  const { node, isStart, issueCount, call, inCall } = data
  const message = node.task_messages[0]?.content?.trim()
  const edges = node.edges ?? []
  const tools = node.tools ?? []
  const live = call === 'active'
  // During a call the canvas narrates the conversation; validation styling steps back.
  const flagged = issueCount > 0 && !inCall
  const issueLabel = `${issueCount} ${issueCount === 1 ? 'issue' : 'issues'}`

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.92 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={softSpring}
      style={{ width: NODE_WIDTH }}
      className={cn(
        'group relative rounded-[14px] border bg-card shadow-card',
        'transition-[box-shadow,translate,border-color,opacity] duration-200 ease-out-soft hover:-translate-y-px hover:shadow-lift',
        live
          ? 'animate-live-glow border-accent'
          : selected
            ? 'border-accent shadow-[0_0_0_1px_var(--color-accent)]'
            : flagged
              ? 'border-dashed border-danger'
              : 'border-border-subtle',
        inCall && !live && call !== 'visited' && 'opacity-80',
      )}
    >
      <Handle type="target" position={Position.Left} />

      <div className="flex items-center gap-2 px-4 pt-3.5 pb-1.5">
        <span className="min-w-0 flex-1 truncate font-mono text-[14px] font-semibold tracking-[-0.01em] text-ink">
          {node.name || <span className="text-faint italic">unnamed</span>}
        </span>
        {issueCount > 0 && (
          <span
            title={issueLabel}
            className={cn('flex items-center gap-1 text-[11px] font-semibold', flagged ? 'text-danger' : 'text-faint')}
          >
            <AlertTriangle className="size-3.5" aria-label={issueLabel} />
            {flagged && issueCount}
          </span>
        )}
        {live && (
          <span className="flex shrink-0 items-center gap-1 rounded-full bg-accent px-2 py-px text-[10.5px] font-bold tracking-[0.08em] text-accent-ink">
            <span className="size-1.5 animate-pulse rounded-full bg-accent-ink" aria-hidden />
            LIVE
          </span>
        )}
        {node.context_strategy === 'reset' && (
          <span title="Context resets on entry" className="flex shrink-0 items-center text-faint">
            <RotateCcw className="size-3.5" aria-label="Context resets on entry" />
          </span>
        )}
        {isStart && <Badge className="bg-info-soft text-info">Start</Badge>}
        {node.end && <Badge className="bg-raised text-ink-soft">End</Badge>}
        {call === 'visited' && (
          <span className="flex size-[18px] items-center justify-center rounded-full bg-success-soft text-success" title="Visited in this call">
            <Check className="size-3" strokeWidth={3} aria-label="Visited" />
          </span>
        )}
      </div>

      <p className={cn('line-clamp-2 min-h-[2.9em] px-4 text-[13px] leading-[1.45]', message ? 'text-muted' : 'text-faint italic')}>
        {message || 'No instructions yet'}
      </p>

      {tools.length > 0 && (
        <div className="mt-2.5 flex flex-wrap gap-1 px-3.5" aria-label="Tools">
          {tools.map((t) => (
            <span
              key={t}
              title={`Tool: ${t}`}
              className="inline-flex max-w-full items-center gap-1 rounded-[6px] border border-border bg-surface px-1.5 py-px font-mono text-[10.5px] text-muted"
            >
              <Wrench className="size-2.5 shrink-0" aria-hidden />
              <span className="truncate">{t}</span>
            </span>
          ))}
        </div>
      )}

      <div className="mt-3 flex min-h-10 flex-wrap items-center gap-1 border-t border-border-subtle px-3.5 py-2">
        {edges.map((e, i) => (
          <span
            key={`${e.function}-${i}`}
            className="inline-flex max-w-full items-center gap-1 truncate rounded-full bg-raised px-2 py-0.5 font-mono text-[11px] text-ink-soft"
          >
            <ArrowRight className="size-3 shrink-0 text-muted" aria-hidden />
            <span className="truncate">{e.function || '—'}</span>
          </span>
        ))}
        {edges.length === 0 &&
          (node.end ? (
            <span className="inline-flex items-center gap-1 text-[12px] text-muted">
              <PhoneOff className="size-3" aria-hidden /> Ends the call
            </span>
          ) : (
            <span className="text-[12px] text-faint">Drag from the right handle to connect</span>
          ))}
      </div>

      <Handle type="source" position={Position.Right} />
    </motion.div>
  )
}

function Badge({ className, children }: { className: string; children: string }) {
  return (
    <span className={cn('shrink-0 rounded-full px-2 py-px text-[11px] font-semibold tracking-[0.02em]', className)}>
      {children}
    </span>
  )
}

export const AgentNodeCard = memo(AgentNodeCardImpl)

import { AnimatePresence, motion } from 'motion/react'
import { PanelLeftClose, PanelLeftOpen, Plus, Trash2, Workflow } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { cn } from '../../lib/cn'
import { softSpring, spring } from '../../lib/motion'
import { relativeTime } from '../../lib/time'
import { useAgents } from '../../store/agents'
import { isCallActive, useCall } from '../../store/call'
import { useEditor } from '../../store/editor'
import type { AgentSummary } from '../../types/agent'
import { Button, IconButton } from '../ui/Button'
import { ConfirmDialog } from '../ui/Modal'
import { NewAgentModal } from './NewAgentModal'

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(false)
  const [creating, setCreating] = useState(false)
  const [deleting, setDeleting] = useState<AgentSummary | null>(null)
  const { list, status, activeId, requestOpen, remove } = useAgents()

  const confirmDelete = async () => {
    if (!deleting) return
    const target = deleting
    setDeleting(null)
    try {
      if (await remove(target.id)) toast.success(`Deleted “${target.name}”`)
    } catch (err) {
      toast.error('Delete failed', { description: err instanceof Error ? err.message : String(err) })
    }
  }

  return (
    <motion.aside
      animate={{ width: collapsed ? 64 : 248 }}
      transition={softSpring}
      className="relative z-20 flex h-full shrink-0 flex-col overflow-hidden border-r border-border bg-surface"
    >
      <div className="flex h-14 shrink-0 items-center gap-2.5 px-4">
        <Logo />
        <AnimatePresence initial={false}>
          {!collapsed && (
            <motion.span
              initial={{ opacity: 0, x: -6 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -6 }}
              transition={{ duration: 0.15 }}
              className="font-display text-[22px] leading-none tracking-[-0.01em] whitespace-nowrap"
            >
              Agent Studio
            </motion.span>
          )}
        </AnimatePresence>
      </div>

      <div className={cn('px-3 pb-2', collapsed && 'px-2.5')}>
        {collapsed ? (
          <IconButton label="New agent" onClick={() => setCreating(true)} className="mx-auto size-9 bg-card shadow-card">
            <Plus className="size-4" />
          </IconButton>
        ) : (
          <Button className="w-full" icon={<Plus className="size-4" />} onClick={() => setCreating(true)}>
            New agent
          </Button>
        )}
      </div>

      {!collapsed && (
        <div className="px-4 pt-3 pb-1.5 text-[11px] font-semibold tracking-[0.06em] text-muted uppercase">Agents</div>
      )}

      <nav aria-label="Agents" className="scrollbar-thin flex-1 overflow-y-auto px-2 pb-3">
        {status === 'loading' && list.length === 0 && <ListSkeleton collapsed={collapsed} />}
        {status === 'error' && !collapsed && (
          <p className="px-2 py-3 text-[12.5px] leading-snug text-muted">
            Can’t reach the backend on :7860. Start it with <code className="font-mono text-[11.5px]">python backend/bot.py</code>.
          </p>
        )}
        <ul className="flex flex-col gap-0.5">
          <AnimatePresence initial={false}>
            {list.map((agent) => (
              <AgentItem
                key={agent.id}
                agent={agent}
                active={agent.id === activeId}
                collapsed={collapsed}
                onOpen={() => requestOpen(agent.id)}
                onDelete={() => {
                  if (agent.id === activeId && isCallActive(useCall.getState().status)) {
                    toast('End the test call before deleting this agent')
                  } else setDeleting(agent)
                }}
              />
            ))}
          </AnimatePresence>
        </ul>
      </nav>

      <div className={cn('flex border-t border-border p-2', collapsed ? 'justify-center' : 'justify-end')}>
        <IconButton label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} onClick={() => setCollapsed((c) => !c)}>
          {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
        </IconButton>
      </div>

      <NewAgentModal open={creating} onClose={() => setCreating(false)} />
      <ConfirmDialog
        open={!!deleting}
        title="Delete agent?"
        description={
          <>
            “{deleting?.name}” and its flow will be permanently removed. This can’t be undone.
          </>
        }
        confirmLabel="Delete agent"
        destructive
        onConfirm={confirmDelete}
        onCancel={() => setDeleting(null)}
      />
    </motion.aside>
  )
}

function AgentItem({
  agent,
  active,
  collapsed,
  onOpen,
  onDelete,
}: {
  agent: AgentSummary
  active: boolean
  collapsed: boolean
  onOpen: () => void
  onDelete: () => void
}) {
  const dirty = useEditor((s) => active && s.dirty)
  return (
    <motion.li
      layout
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, height: 0 }}
      transition={spring}
      className="group relative"
    >
      {active && (
        <motion.span
          layoutId="agent-active"
          transition={spring}
          className="absolute inset-0 rounded-[10px] border border-border-subtle bg-card shadow-card"
        />
      )}
      <button
        type="button"
        onClick={onOpen}
        title={collapsed ? agent.name : undefined}
        aria-current={active ? 'page' : undefined}
        className={cn(
          'relative flex w-full items-center gap-2.5 rounded-[10px] px-2 py-2 text-left transition-colors duration-200',
          !active && 'hover:bg-hover',
        )}
      >
        <span
          className={cn(
            'flex size-8 shrink-0 items-center justify-center rounded-[9px] text-[12px] font-semibold transition-colors duration-200',
            active ? 'bg-ink text-bg' : 'bg-raised text-ink-soft',
          )}
        >
          {initials(agent.name)}
        </span>
        {!collapsed && (
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-1.5">
              <span className="truncate text-[13.5px] font-medium">{agent.name}</span>
              {dirty && <span className="size-1.5 shrink-0 rounded-full bg-accent" aria-label="Unsaved changes" />}
            </span>
            <span className="mt-0.5 flex items-center gap-1 text-[12px] text-muted">
              <Workflow className="size-3" aria-hidden />
              {agent.node_count} {agent.node_count === 1 ? 'node' : 'nodes'} · {relativeTime(agent.updated_at)}
            </span>
          </span>
        )}
      </button>
      {!collapsed && (
        <IconButton
          label={`Delete ${agent.name}`}
          onClick={onDelete}
          className="absolute top-1/2 right-1.5 size-7 -translate-y-1/2 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100 hover:text-danger"
        >
          <Trash2 className="size-3.5" />
        </IconButton>
      )}
    </motion.li>
  )
}

function ListSkeleton({ collapsed }: { collapsed: boolean }) {
  return (
    <div className="flex flex-col gap-1" aria-hidden>
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex items-center gap-2.5 px-2 py-2">
          <div className="skeleton size-8 rounded-[9px]" />
          {!collapsed && (
            <div className="flex-1 space-y-1.5">
              <div className="skeleton h-3 w-3/4 rounded" />
              <div className="skeleton h-2.5 w-1/2 rounded" />
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  return (words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? '?').slice(0, 2)).toUpperCase()
}

export function Logo() {
  return (
    <svg viewBox="0 0 32 32" className="size-8 shrink-0" aria-hidden>
      <rect width="32" height="32" rx="9" className="fill-ink" />
      <circle cx="11" cy="16" r="3.2" className="fill-accent" />
      <circle cx="21" cy="11" r="2.4" className="fill-surface" />
      <circle cx="21" cy="21" r="2.4" className="fill-surface" />
      <path d="M14 15l4.6-3M14 17l4.6 3" className="stroke-surface" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}

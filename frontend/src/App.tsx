import { ReactFlowProvider } from '@xyflow/react'
import { MotionConfig } from 'motion/react'
import { useCallback, useEffect, useState } from 'react'
import { Toaster } from 'sonner'
import { CallProvider } from './components/call/CallProvider'
import { Canvas } from './components/canvas/Canvas'
import { RightPanel } from './components/inspector/RightPanel'
import { KeysSheet } from './components/keys/KeysSheet'
import { Sidebar } from './components/sidebar/Sidebar'
import { TopBar } from './components/topbar/TopBar'
import { ConfirmDialog } from './components/ui/Modal'
import { useLiveValidation } from './hooks/useLiveValidation'
import { useShortcuts } from './hooks/useShortcuts'
import { useAgents } from './store/agents'
import { useEditor } from './store/editor'
import { useTheme } from './store/theme'

export default function App() {
  return (
    <MotionConfig reducedMotion="user">
      <CallProvider>
        <div className="flex h-full overflow-hidden">
          <Sidebar />
          <ReactFlowProvider>
            <Workspace />
          </ReactFlowProvider>
        </div>
        <SwitchGuard />
        <KeysSheet />
        <ThemedToaster />
      </CallProvider>
    </MotionConfig>
  )
}

function ThemedToaster() {
  const theme = useTheme((s) => s.resolved)
  return (
    <Toaster
      theme={theme}
      position="bottom-center"
      toastOptions={{
        classNames: {
          toast: '!rounded-[12px] !border-border-subtle !bg-card !text-ink !shadow-panel !font-sans !text-[13px]',
          description: '!text-muted',
          actionButton: '!bg-accent !text-accent-ink',
          cancelButton: '!bg-raised !text-ink-soft',
        },
      }}
    />
  )
}

function Workspace() {
  const hasDoc = useEditor((s) => !!s.doc)
  const status = useAgents((s) => s.status)
  const opening = useAgents((s) => s.opening)
  const [helpOpen, setHelpOpen] = useState(false)
  const toggleHelp = useCallback(() => setHelpOpen((o) => !o), [])

  useLiveValidation()
  useShortcuts(toggleHelp)

  useEffect(() => {
    void useAgents.getState().boot()
  }, [])

  useEffect(() => {
    const onUnload = (e: BeforeUnloadEvent) => {
      if (useEditor.getState().dirty) e.preventDefault()
    }
    window.addEventListener('beforeunload', onUnload)
    return () => window.removeEventListener('beforeunload', onUnload)
  }, [])

  if (!hasDoc) {
    return (
      <main className="flex min-w-0 flex-1 items-center justify-center">
        {status === 'loading' || opening ? <CanvasSkeleton /> : <EmptyState error={status === 'error'} />}
      </main>
    )
  }

  return (
    <main className="flex min-w-0 flex-1 flex-col">
      <TopBar helpOpen={helpOpen} setHelpOpen={setHelpOpen} />
      <div className="relative min-h-0 flex-1">
        <Canvas />
        <RightPanel />
      </div>
    </main>
  )
}

function CanvasSkeleton() {
  return (
    <div className="flex items-center gap-16" aria-label="Loading agent">
      {[0, 1, 2].map((i) => (
        <div key={i} className="w-[220px] rounded-[12px] border border-border-subtle bg-card p-4 shadow-card">
          <div className="skeleton h-3.5 w-1/2 rounded" />
          <div className="skeleton mt-3 h-2.5 w-full rounded" />
          <div className="skeleton mt-1.5 h-2.5 w-4/5 rounded" />
          <div className="skeleton mt-4 h-4 w-1/3 rounded-full" />
        </div>
      ))}
    </div>
  )
}

function EmptyState({ error }: { error: boolean }) {
  return (
    <div className="max-w-[360px] text-center">
      <h1 className="font-display text-[34px] leading-tight">{error ? 'Backend offline' : 'No agent selected'}</h1>
      <p className="mt-2 text-[13.5px] leading-relaxed text-muted">
        {error
          ? 'Start the API with “backend/.venv/Scripts/python backend/bot.py”, then reload.'
          : 'Create an agent from the sidebar to start designing its conversation flow.'}
      </p>
    </div>
  )
}

function SwitchGuard() {
  const pendingId = useAgents((s) => s.pendingId)
  const { open, cancelPending } = useAgents.getState()
  return (
    <ConfirmDialog
      open={!!pendingId}
      title="Discard unsaved changes?"
      description="This agent has edits that haven’t been saved. Switching will discard them."
      confirmLabel="Discard and switch"
      destructive
      onConfirm={() => pendingId && void open(pendingId)}
      onCancel={cancelPending}
    />
  )
}

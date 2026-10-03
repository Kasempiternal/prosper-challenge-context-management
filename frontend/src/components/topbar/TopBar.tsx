import { AnimatePresence, motion } from 'motion/react'
import { Activity, Check, Keyboard, LayoutGrid, Loader2, Phone, Redo2, Save, Settings2, Undo2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useAutoLayout } from '../../hooks/useAutoLayout'
import { SHORTCUTS } from '../../hooks/useShortcuts'
import { saveCurrent } from '../../lib/actions'
import { callBlockedHint } from '../../lib/issues'
import { fade, spring } from '../../lib/motion'
import { isCallActive, useCall } from '../../store/call'
import { cn } from '../../lib/cn'
import { useDevView } from '../../store/devView'
import { useEditor } from '../../store/editor'
import { KeysButton } from '../keys/KeysSheet'
import { Button, IconButton } from '../ui/Button'
import { Kbd, Popover } from '../ui/Popover'
import { AgentNameInput } from './AgentNameInput'
import { ThemeMenu } from './ThemeMenu'
import { ValidationChip } from './ValidationChip'

const MOD = /Mac|iPhone|iPad/.test(navigator.userAgent) ? '⌘' : 'Ctrl'

export function TopBar({ helpOpen, setHelpOpen }: { helpOpen: boolean; setHelpOpen: (open: boolean) => void }) {
  const canUndo = useEditor((s) => s.past.length > 0)
  const canRedo = useEditor((s) => s.future.length > 0)
  const dirty = useEditor((s) => s.dirty)
  const settingsOpen = useEditor((s) => s.rightPanel === 'inspector' && s.selection?.kind === 'settings')
  const callOpen = useEditor((s) => s.rightPanel === 'call')
  const { undo, redo, select, setRightPanel } = useEditor.getState()
  const callStatus = useCall((s) => s.status)
  const issueCount = useEditor((s) => (s.validation === 'valid' ? 0 : s.issues.length))
  const callHint =
    !isCallActive(callStatus) && issueCount > 0 ? callBlockedHint(issueCount) : callOpen ? 'Hide test call' : 'Talk to this draft'
  const layout = useAutoLayout()

  return (
    <header className="relative z-30 flex h-14 shrink-0 items-center gap-3 border-b border-border bg-bg/80 px-4 backdrop-blur">
      <div className="flex min-w-0 flex-1 items-center gap-2.5">
        <AgentNameInput />
        <AnimatePresence>
          {dirty && (
            <motion.span
              initial={{ scale: 0, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0, opacity: 0 }}
              transition={spring}
              title="Unsaved changes"
              aria-label="Unsaved changes"
              className="size-2 shrink-0 rounded-full bg-accent"
            />
          )}
        </AnimatePresence>
        <ValidationChip />
      </div>

      <div className="flex items-center gap-0.5">
        <IconButton label={`Undo (${MOD}+Z)`} onClick={undo} disabled={!canUndo}>
          <Undo2 className="size-4" />
        </IconButton>
        <IconButton label={`Redo (${MOD}+Shift+Z)`} onClick={redo} disabled={!canRedo}>
          <Redo2 className="size-4" />
        </IconButton>
        <IconButton label="Auto-layout" onClick={layout}>
          <LayoutGrid className="size-4" />
        </IconButton>
        <IconButton
          label="Agent settings"
          active={settingsOpen}
          onClick={() => select(settingsOpen ? null : { kind: 'settings' })}
        >
          <Settings2 className="size-4" />
        </IconButton>
        <Popover
          open={helpOpen}
          onClose={() => setHelpOpen(false)}
          align="end"
          className="w-[260px]"
          anchor={
            <IconButton label="Keyboard shortcuts (?)" active={helpOpen} onClick={() => setHelpOpen(!helpOpen)}>
              <Keyboard className="size-4" />
            </IconButton>
          }
        >
          <p className="px-2.5 pt-1.5 pb-2 text-[11px] font-semibold tracking-[0.06em] text-muted uppercase">Shortcuts</p>
          <ul>
            {SHORTCUTS.map((s) => (
              <li key={s.label} className="flex items-center justify-between rounded-lg px-2.5 py-1.5 text-[13px]">
                <span className="text-ink-soft">{s.label}</span>
                <span className="flex gap-1">
                  {s.keys.map((k) => (
                    <Kbd key={k}>{k === 'Ctrl' ? MOD : k}</Kbd>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        </Popover>
        <ThemeMenu />
        <div className="ml-1 flex items-center gap-1.5">
          <KeysButton />
          <DevViewToggle />
        </div>
      </div>

      <div className="mx-1 h-5 w-px bg-border" />

      <SaveButton />
      <Button
        variant="primary"
        icon={
          callStatus === 'live' ? (
            <span className="relative flex size-2">
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-accent-ink opacity-75" />
              <span className="relative inline-flex size-2 rounded-full bg-accent-ink" />
            </span>
          ) : (
            <Phone className="size-4" />
          )
        }
        onClick={() => setRightPanel(callOpen ? 'inspector' : 'call')}
        aria-pressed={callOpen}
        title={callHint}
      >
        {callStatus === 'live' ? 'On call' : 'Test call'}
      </Button>
    </header>
  )
}

function DevViewToggle() {
  const on = useDevView((s) => s.on)
  return (
    <button
      type="button"
      onClick={useDevView.getState().toggle}
      aria-pressed={on}
      title="Dev view: live pipeline telemetry (D)"
      className={cn(
        'flex h-8 items-center gap-1.5 rounded-full border px-3 text-[12.5px] font-medium transition-colors duration-200 ease-out-soft',
        on ? 'border-accent/40 bg-accent-soft text-accent' : 'border-border text-ink-soft hover:border-border-strong hover:text-ink',
      )}
    >
      <Activity className="size-3.5" aria-hidden />
      Dev view
    </button>
  )
}

function SaveButton() {
  const saving = useEditor((s) => s.saving)
  const dirty = useEditor((s) => s.dirty)
  const savedNonce = useEditor((s) => s.savedNonce)
  const [settledNonce, setSettledNonce] = useState(0)
  const justSaved = savedNonce !== settledNonce

  useEffect(() => {
    const t = window.setTimeout(() => setSettledNonce(savedNonce), 1600)
    return () => window.clearTimeout(t)
  }, [savedNonce])

  const state = saving ? 'saving' : justSaved ? 'saved' : 'idle'
  return (
    <Button
      onClick={() => void saveCurrent()}
      disabled={!dirty && !saving && !justSaved}
      className="min-w-[92px]"
      aria-label={`Save (${MOD}+S)`}
      title={`Save (${MOD}+S)`}
    >
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span
          key={state}
          initial={{ opacity: 0, scale: 0.6, filter: 'blur(2px)' }}
          animate={{ opacity: 1, scale: 1, filter: 'blur(0px)' }}
          exit={{ opacity: 0, scale: 0.6, filter: 'blur(2px)' }}
          transition={fade}
          className="flex items-center gap-2"
        >
          {state === 'saving' && <Loader2 className="size-4 animate-spin" />}
          {state === 'saved' && <Check className="size-4 text-success" strokeWidth={2.5} />}
          {state === 'idle' && <Save className="size-4" />}
          {state === 'saved' ? 'Saved' : 'Save'}
        </motion.span>
      </AnimatePresence>
    </Button>
  )
}

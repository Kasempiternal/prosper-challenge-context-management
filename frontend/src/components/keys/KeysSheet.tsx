import { KeyRound, Lock } from 'lucide-react'
import { useEffect } from 'react'
import { cn } from '../../lib/cn'
import { joinLabels, PROVIDER_IDS, type Provider } from '../../lib/keyProviders'
import { keyHealth, missingForCall, useKeys, type KeyHealth } from '../../store/keys'
import { PanelHeader } from '../inspector/Panel'
import { Button, IconButton } from '../ui/Button'
import { Sheet } from '../ui/Sheet'
import { KeyRow } from './KeyRow'

/** The API keys sheet: one row per provider. Opened through useKeys().openSheet. */
export function KeysSheet() {
  const sheet = useKeys((s) => s.sheet)
  const { closeSheet } = useKeys.getState()

  return (
    <Sheet
      open={sheet !== null}
      onClose={closeSheet}
      label="API keys"
      describedBy="keys-sheet-description"
      initialFocus={sheet?.focus ? `keys-${sheet.focus}-key` : null}
    >
      <PanelHeader icon={<KeyRound className="size-4" />} eyebrow="Studio" title="API keys" onClose={closeSheet} />
      <div className="h-px bg-border-subtle" />
      <div className="scrollbar-thin flex-1 overflow-y-auto px-5 pt-4 pb-5">
        <p id="keys-sheet-description" className="text-[13px] leading-relaxed text-muted">
          A test call needs OpenAI and ElevenLabs. JEV is optional: without it, the agent asks the caller whenever a request
          is ambiguous.
        </p>
        <div className="mt-4 flex flex-col gap-3">
          {PROVIDER_IDS.map((p) => (
            <KeyRow key={p} provider={p} id={`keys-${p}`} variant="sheet" />
          ))}
        </div>
      </div>
      <div className="border-t border-border-subtle bg-surface/60 px-5 py-3.5">
        <p className="flex items-start gap-2 text-[12px] leading-snug text-muted">
          <Lock className="mt-px size-3.5 shrink-0 text-faint" aria-hidden />
          Keys stay in this browser and go only to your local backend. Nothing is saved on the server.
        </p>
      </div>
    </Sheet>
  )
}

const DOT: Record<KeyHealth, string> = {
  ready: 'bg-success',
  'no-jev': 'bg-amber-500 dark:bg-warning',
  blocked: 'bg-danger',
  unknown: 'bg-border-strong',
}

function healthLabel(health: KeyHealth, missing: Provider[]): string {
  if (health === 'ready') return 'API keys: all set'
  if (health === 'no-jev') return 'API keys: no JEV key, so calls run without JEV'
  if (health === 'blocked') return `API keys: add ${joinLabels(missing)} to start a call`
  return 'API keys'
}

/**
 * Opens the keys sheet, with a dot for whether a call can start: green (every key), amber (no JEV
 * key), red (no OpenAI or ElevenLabs key). `compact`: an icon button for a panel header.
 */
export function KeysButton({ compact = false }: { compact?: boolean }) {
  const browser = useKeys((s) => s.browser)
  const server = useKeys((s) => s.server)
  const keys = { browser, server }
  const health = keyHealth(keys)
  const missing = missingForCall(keys)
  const label = healthLabel(health, missing)
  const open = () => useKeys.getState().openSheet(missing[0] ?? (health === 'no-jev' ? 'jev' : undefined))

  useEffect(() => {
    void useKeys.getState().loadServerStatus()
  }, [])

  if (compact) {
    return (
      <IconButton label={label} aria-haspopup="dialog" onClick={open} className="relative">
        <KeyRound className="size-4" />
        <span aria-hidden className={cn('absolute top-1 right-1 size-2 rounded-full ring-2 ring-card', DOT[health])} />
      </IconButton>
    )
  }
  return (
    <button
      type="button"
      onClick={open}
      aria-haspopup="dialog"
      aria-label={label}
      title={label}
      className="flex h-8 items-center gap-1.5 rounded-full border border-border px-3 text-[12.5px] font-medium text-ink-soft transition-colors duration-200 ease-out-soft hover:border-border-strong hover:text-ink"
    >
      <KeyRound className="size-3.5" aria-hidden />
      Keys
      <span aria-hidden className={cn('ml-0.5 size-2 rounded-full transition-colors duration-300', DOT[health])} />
    </button>
  )
}

/** Under the Call button when a call cannot start: which keys to add, and the way to add them. */
export function MissingKeysNotice({ providers }: { providers: Provider[] }) {
  const plural = providers.length > 1 ? 'keys' : 'key'
  return (
    <div
      role="status"
      className="flex w-full items-center gap-3 rounded-[12px] border border-warning/25 bg-warning-soft py-2.5 pr-2.5 pl-3.5"
    >
      <KeyRound className="size-3.5 shrink-0 text-warning" aria-hidden />
      <p className="min-w-0 flex-1 text-[12.5px] leading-snug text-ink-soft">
        Add your {joinLabels(providers)} {plural} to start a call
      </p>
      <Button size="sm" variant="secondary" className="h-7 px-3 text-[12.5px]" onClick={() => useKeys.getState().openSheet(providers[0])}>
        Open keys
      </Button>
    </div>
  )
}

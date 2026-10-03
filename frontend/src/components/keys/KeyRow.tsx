import { AnimatePresence, motion } from 'motion/react'
import { AlertCircle, ArrowUpRight, AudioLines, Check, Eye, EyeOff, KeyRound, Loader2, Lock, MessagesSquare, Scale } from 'lucide-react'
import { useEffect, useState, type ComponentType } from 'react'
import { cn } from '../../lib/cn'
import { PROVIDERS, shortLabel, type Provider } from '../../lib/keyProviders'
import { checkFor, keySource, maskKey, useKeys, type KeyCheck, type KeySource } from '../../store/keys'
import { Button } from '../ui/Button'
import { controlClass } from '../ui/control'

/** "inline": under the Disambiguator switch, in the panel's width. "sheet": a row of the API keys sheet, with its header. */
type Variant = 'inline' | 'sheet'

/** Why the key entry is open: nothing to fall back on, the server's key, or a saved key. */
type Entry = 'none' | 'server' | 'replace'

const ICONS: Record<Provider, ComponentType<{ className?: string }>> = {
  openai: MessagesSquare,
  elevenlabs: AudioLines,
  jev: Scale,
}

const SOURCE: Record<KeySource, string> = {
  browser: 'This browser',
  server: 'Server key',
  missing: 'Missing',
  unknown: 'Checking…',
}

const swap = { initial: { opacity: 0, y: 3 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0, y: -3 }, transition: { duration: 0.15 } }

const textAction =
  'h-7 shrink-0 rounded-full px-2.5 text-[12.5px] font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-45'

/**
 * One provider's key: where it comes from, and the entry, saved and server views over the keys
 * store. The same component under the Disambiguator ("inline") and in the keys sheet. The input's
 * id is `${id}-key`.
 */
export function KeyRow({ provider, id, variant, locked = false }: { provider: Provider; id: string; variant: Variant; locked?: boolean }) {
  const source = useKeys((s) => keySource(s, provider))
  const saved = useKeys((s) => s.browser[provider])
  const [editing, setEditing] = useState(false)

  useEffect(() => {
    void useKeys.getState().loadServerStatus()
  }, [])

  const entry: Entry | null = editing ? (saved ? 'replace' : 'server') : source === 'missing' ? 'none' : null
  const body = entry ? (
    <KeyEntry id={`${id}-key`} provider={provider} entry={entry} variant={variant} locked={locked} onDone={() => setEditing(false)} />
  ) : saved ? (
    <SavedKey id={id} provider={provider} saved={saved} variant={variant} locked={locked} onReplace={() => setEditing(true)} />
  ) : source === 'server' ? (
    <ServerKey provider={provider} locked={locked} onOwnKey={() => setEditing(true)} />
  ) : variant === 'sheet' ? (
    <p className="flex items-center gap-1.5 text-[12px] text-muted">
      <Loader2 className="size-3.5 animate-spin" aria-hidden />
      Asking the backend for its keys…
    </p>
  ) : null

  const content = (
    <AnimatePresence mode="wait" initial={false}>
      {body && (
        <motion.div key={entry ?? source} {...swap} className={variant === 'inline' ? 'pt-1' : undefined}>
          {body}
        </motion.div>
      )}
    </AnimatePresence>
  )
  if (variant === 'inline') return content

  const info = PROVIDERS[provider]
  const Icon = ICONS[provider]
  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-3.5 rounded-[14px] border border-border-subtle bg-card p-4 shadow-card">
      <div className="flex items-start gap-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-[10px] bg-raised text-ink-soft" aria-hidden>
          <Icon className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <h3 id={`${id}-title`} className="truncate text-[14px] leading-tight font-semibold tracking-[-0.01em] text-ink">
              {info.label}
            </h3>
            <SourceBadge source={source} required={info.required} />
          </div>
          <p className="mt-0.5 text-[12.5px] leading-snug text-muted">{info.purpose}</p>
        </div>
      </div>
      {content}
    </section>
  )
}

function SourceBadge({ source, required }: { source: KeySource; required: boolean }) {
  const missing = source === 'missing'
  return (
    <span
      className={cn(
        'inline-flex h-6 shrink-0 items-center gap-1.5 rounded-full border px-2.5 text-[11.5px] font-medium whitespace-nowrap',
        missing
          ? required
            ? 'border-danger/25 bg-danger-soft text-danger'
            : 'border-warning/30 bg-warning-soft text-warning'
          : 'border-border-subtle bg-surface text-ink-soft',
      )}
    >
      <span
        aria-hidden
        className={cn(
          'size-1.5 rounded-full',
          missing ? 'bg-current' : source === 'unknown' ? 'bg-border-strong' : 'bg-success',
        )}
      />
      {SOURCE[source]}
    </span>
  )
}

function ServerKey({ provider, locked, onOwnKey }: { provider: Provider; locked: boolean; onOwnKey: () => void }) {
  return (
    <p className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[12px] leading-snug text-muted">
      <KeyRound className="size-3.5 shrink-0 text-faint" aria-hidden />
      Using the server’s {shortLabel(provider)} key
      <span aria-hidden className="text-faint">
        ·
      </span>
      <button
        type="button"
        disabled={locked}
        onClick={onOwnKey}
        className="rounded-sm font-medium text-accent underline-offset-2 transition-colors duration-150 enabled:hover:text-accent-hover enabled:hover:underline disabled:cursor-not-allowed disabled:opacity-50"
      >
        Use my own key
      </button>
    </p>
  )
}

const TITLE: Record<Entry, (keyName: string) => string> = {
  none: (keyName) => `Add your ${keyName}`,
  server: (keyName) => `Use your own ${keyName}`,
  replace: (keyName) => `Replace your ${keyName}`,
}

interface EntryProps {
  id: string
  provider: Provider
  entry: Entry
  variant: Variant
  locked: boolean
  onDone: () => void
}

function KeyEntry({ id, provider, entry, variant, locked, onDone }: EntryProps) {
  const info = PROVIDERS[provider]
  const [draft, setDraft] = useState('')
  const [shown, setShown] = useState(false)
  const check = useKeys((s) => checkFor(s.checks[provider], draft))
  const empty = draft.trim() === ''
  const testing = check?.status === 'testing'
  const inline = variant === 'inline'
  const titled = inline || entry !== 'none'

  const save = () => {
    if (locked || empty) return
    useKeys.getState().save(provider, draft)
    onDone()
  }

  return (
    <div className={cn('flex flex-col gap-2.5', inline && 'rounded-[12px] border border-border-subtle bg-surface p-3')}>
      {titled ? (
        <div className="flex items-start gap-2.5">
          {inline && (
            <span className="flex size-7 shrink-0 items-center justify-center rounded-[8px] bg-accent-soft text-accent" aria-hidden>
              <KeyRound className="size-3.5" />
            </span>
          )}
          <label
            htmlFor={id}
            className={cn('min-w-0 flex-1 text-[13px] leading-snug font-medium text-ink', inline ? 'pt-[5px]' : 'pt-[3px]')}
          >
            {TITLE[entry](info.keyName)}
          </label>
          {entry !== 'none' && (
            <button
              type="button"
              onClick={onDone}
              className="-mr-1 shrink-0 rounded-full px-2 py-1 text-[12px] font-medium text-muted transition-colors duration-150 hover:bg-hover hover:text-ink"
            >
              Cancel
            </button>
          )}
        </div>
      ) : (
        <label htmlFor={id} className="sr-only">
          {info.keyName}
        </label>
      )}

      <div>
        <div className="relative">
          <input
            id={id}
            type={shown ? 'text' : 'password'}
            value={draft}
            disabled={locked}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                save()
              }
            }}
            placeholder={`Paste your ${info.keyName}`}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            data-1p-ignore
            data-lpignore="true"
            aria-describedby={`${id}-status`}
            className={cn(
              controlClass(check?.status === 'failed'),
              'h-9 pr-10 font-mono text-[12.5px] placeholder:font-sans placeholder:text-[13px] disabled:opacity-60',
            )}
          />
          <button
            type="button"
            aria-label={shown ? 'Hide key' : 'Show key'}
            aria-pressed={shown}
            aria-controls={id}
            title={shown ? 'Hide key' : 'Show key'}
            onClick={() => setShown(!shown)}
            className="absolute top-1/2 right-1 flex size-7 -translate-y-1/2 items-center justify-center rounded-full text-faint transition-colors duration-150 hover:bg-hover hover:text-ink"
          >
            {shown ? <EyeOff className="size-3.5" aria-hidden /> : <Eye className="size-3.5" aria-hidden />}
          </button>
        </div>
        {!inline && <KeyStatus id={`${id}-status`} provider={provider} check={check} className="mt-2 empty:mt-0" />}
      </div>

      <div className="flex items-center gap-2">
        {inline ? (
          <KeyStatus id={`${id}-status`} provider={provider} check={check} className="min-w-0 flex-1" />
        ) : (
          <GetKeyLink provider={provider} className="mr-auto text-[12px]" />
        )}
        <Button
          size="sm"
          variant="secondary"
          className="h-7 px-3 text-[12.5px]"
          disabled={locked || empty || testing}
          onClick={() => void useKeys.getState().test(provider, draft)}
        >
          Test
        </Button>
        <Button size="sm" variant="primary" className="h-7 px-3 text-[12.5px]" disabled={locked || empty} onClick={save}>
          Save
        </Button>
      </div>

      {inline && (
        <div className="flex items-center justify-between gap-3 border-t border-border-subtle pt-2.5 text-[11.5px] leading-snug">
          <span className="flex min-w-0 items-center gap-1.5 text-faint">
            <Lock className="size-3 shrink-0" aria-hidden />
            <span className="truncate">Stays in this browser</span>
          </span>
          <GetKeyLink provider={provider} short />
        </div>
      )}
    </div>
  )
}

/** "Get a key at platform.openai.com", or "Get a key" where the panel is narrow. */
function GetKeyLink({ provider, short = false, className }: { provider: Provider; short?: boolean; className?: string }) {
  const info = PROVIDERS[provider]
  return (
    <a
      href={info.url}
      target="_blank"
      rel="noreferrer noopener"
      title={short ? `Get a key at ${info.host}` : undefined}
      className={cn(
        'group inline-flex shrink-0 items-center gap-0.5 rounded-sm font-medium text-muted underline-offset-2 transition-colors duration-150 hover:text-accent hover:underline',
        className,
      )}
    >
      {short ? 'Get a key' : `Get a key at ${info.host}`}
      <ArrowUpRight className="size-3 transition-transform duration-150 group-hover:translate-x-px group-hover:-translate-y-px" aria-hidden />
    </a>
  )
}

interface SavedProps {
  id: string
  provider: Provider
  saved: string
  variant: Variant
  locked: boolean
  onReplace: () => void
}

function SavedKey({ id, provider, saved, variant, locked, onReplace }: SavedProps) {
  const check = useKeys((s) => checkFor(s.checks[provider], saved))
  const serverToo = useKeys((s) => s.server?.[provider] === true)
  const masked = (
    <span className="truncate font-mono text-[13px] leading-snug tracking-[0.02em] text-ink">
      <span aria-hidden>{maskKey(saved)}</span>
      <span className="sr-only">{saved.length > 8 ? `Key ending in ${saved.slice(-4)}` : 'Key saved'}</span>
    </span>
  )
  const actions = (
    <div className={cn('flex flex-wrap items-center gap-0.5', variant === 'inline' && 'mt-1.5 -ml-2.5')}>
      <button
        type="button"
        disabled={locked || check?.status === 'testing'}
        onClick={() => void useKeys.getState().test(provider, saved)}
        className={cn(textAction, 'text-ink-soft enabled:hover:bg-hover enabled:hover:text-ink')}
      >
        Test
      </button>
      <button type="button" disabled={locked} onClick={onReplace} className={cn(textAction, 'text-ink-soft enabled:hover:bg-hover enabled:hover:text-ink')}>
        Replace
      </button>
      <button
        type="button"
        disabled={locked}
        onClick={() => useKeys.getState().remove(provider)}
        className={cn(textAction, 'text-danger enabled:hover:bg-danger-soft')}
      >
        Remove
      </button>
    </div>
  )
  const status = <KeyStatus id={`${id}-status`} provider={provider} check={check} className="mt-1 empty:mt-0" />

  if (variant === 'sheet') {
    return (
      <div className="flex flex-col">
        <div className="flex items-center gap-2">
          <div className="flex h-9 min-w-0 flex-1 items-center rounded-[10px] border border-border-subtle bg-surface px-3">{masked}</div>
          <div className="-mr-1.5">{actions}</div>
        </div>
        {status}
        {serverToo && <p className="mt-1.5 text-[11.5px] leading-snug text-faint">Used instead of the server’s key.</p>}
      </div>
    )
  }

  return (
    <div className="flex items-start gap-2.5 rounded-[12px] border border-border-subtle bg-surface py-2.5 pr-3 pl-3">
      <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-[8px] bg-accent-soft text-accent" aria-hidden>
        <KeyRound className="size-3.5" />
      </span>
      <div className="flex min-w-0 flex-1 flex-col">
        <p className="text-[11.5px] leading-tight text-muted">Your {shortLabel(provider)} key, in this browser</p>
        {masked}
        {actions}
        {status}
      </div>
    </div>
  )
}

function failedText(error: string, provider: Provider): string {
  if (error === 'invalid key') return 'Key rejected'
  if (error === 'network') return `Couldn’t reach ${shortLabel(provider)}`
  if (error === 'busy') return 'A test of this key is already running'
  if (error === 'backend') return 'Couldn’t reach the backend'
  return `${shortLabel(provider)} answered unexpectedly`
}

function KeyStatus({ id, provider, check, className }: { id: string; provider: Provider; check: KeyCheck | null; className?: string }) {
  return (
    <span id={id} role="status" aria-live="polite" className={cn('flex items-center text-[12px] leading-tight', className)}>
      <AnimatePresence mode="wait" initial={false}>
        {check && (
          <motion.span key={check.status} {...swap} className="flex min-w-0 items-center gap-1.5">
            {check.status === 'testing' ? (
              <>
                <Loader2 className="size-3.5 shrink-0 animate-spin text-muted" aria-hidden />
                <span className="truncate text-muted">Testing…</span>
              </>
            ) : check.status === 'verified' ? (
              <>
                <span className="flex size-4 shrink-0 items-center justify-center rounded-full bg-success-soft text-success" aria-hidden>
                  <Check className="size-2.5" strokeWidth={3} />
                </span>
                <span className="truncate font-medium text-success" title={check.limited ? 'The key works, but lacks a permission this check reads with.' : undefined}>
                  {check.limited ? 'Key valid (limited permissions)' : 'Key verified'} <span className="font-normal text-muted">·</span>{' '}
                  <span className="font-mono font-normal tabular-nums">{check.ms} ms</span>
                </span>
              </>
            ) : (
              <>
                <AlertCircle className="size-3.5 shrink-0 text-danger" aria-hidden />
                <span className="truncate font-medium text-danger" title={check.error}>
                  {failedText(check.error, provider)}
                </span>
              </>
            )}
          </motion.span>
        )}
      </AnimatePresence>
    </span>
  )
}

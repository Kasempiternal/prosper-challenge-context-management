import { create } from 'zustand'
import { api } from '../lib/api'
import { usesJev } from '../lib/chooser'
import { PROVIDER_IDS, PROVIDERS, type Provider } from '../lib/keyProviders'
import type { AgentConfig } from '../types/agent'

/** Where a provider's key pasted in this browser lives. Readable by any script on this origin, so a local-demo convenience only. */
export const keyStorage = (p: Provider) => `agent-studio:${p}-key`

/** The last key test, tied to the key it tested: a result shows only next to that same key. */
export type KeyCheck =
  | { status: 'testing'; key: string }
  | { status: 'verified'; key: string; ms: number; limited: boolean }
  | { status: 'failed'; key: string; error: string }

export type PerProvider<T> = Record<Provider, T>

/** Where a call would get a provider's key from; "unknown" until /api/keys/status answers. */
export type KeySource = 'browser' | 'server' | 'missing' | 'unknown'

/** The keys sheet, and the row to put the cursor in when it opens. */
export type SheetState = { focus: Provider | null } | null

interface KeysState {
  /** Keys saved in this browser; null when none. */
  browser: PerProvider<string | null>
  /** Whether the backend has its own key; null until /api/keys/status answers. */
  server: PerProvider<boolean> | null
  checks: PerProvider<KeyCheck | null>
  sheet: SheetState

  save: (p: Provider, key: string) => void
  remove: (p: Provider) => void
  test: (p: Provider, key: string) => Promise<void>
  /** Asks the backend once per page load; a failed request counts as no server keys. */
  loadServerStatus: () => Promise<void>
  openSheet: (focus?: Provider) => void
  closeSheet: () => void
}

const perProvider = <T>(value: (p: Provider) => T) =>
  Object.fromEntries(PROVIDER_IDS.map((p) => [p, value(p)])) as PerProvider<T>

const NO_SERVER_KEYS = perProvider(() => false)

let statusRequest: Promise<void> | null = null

export const useKeys = create<KeysState>()((set, get) => ({
  browser: perProvider((p) => localStorage.getItem(keyStorage(p))?.trim() || null),
  server: null,
  checks: perProvider(() => null),
  sheet: null,

  save: (p, raw) => {
    const key = raw.trim()
    if (!key) return
    localStorage.setItem(keyStorage(p), key)
    set({ browser: { ...get().browser, [p]: key } })
  },
  remove: (p) => {
    localStorage.removeItem(keyStorage(p))
    set({ browser: { ...get().browser, [p]: null } })
  },
  test: async (p, raw) => {
    const key = raw.trim()
    if (!key) return
    set({ checks: { ...get().checks, [p]: { status: 'testing', key } } })
    const result = await api.testKey(p, key)
    if (get().checks[p]?.key !== key) return
    const check: KeyCheck = result.ok
      ? { status: 'verified', key, ms: result.ms, limited: result.limited }
      : { status: 'failed', key, error: result.error }
    set({ checks: { ...get().checks, [p]: check } })
  },
  loadServerStatus: () =>
    (statusRequest ??= api.keyStatus().then(
      (server) => set({ server }),
      () => set({ server: NO_SERVER_KEYS }),
    )),
  openSheet: (focus) => set({ sheet: { focus: focus ?? null } }),
  closeSheet: () => set({ sheet: null }),
}))

type Keys = Pick<KeysState, 'browser' | 'server'>

export function keySource(keys: Keys, p: Provider): KeySource {
  if (keys.browser[p]) return 'browser'
  if (keys.server === null) return 'unknown'
  return keys.server[p] ? 'server' : 'missing'
}

/** The required keys (OpenAI, ElevenLabs) that neither this browser nor the server has. */
export function missingForCall(keys: Keys): Provider[] {
  return PROVIDER_IDS.filter((p) => PROVIDERS[p].required && keySource(keys, p) === 'missing')
}

/**
 * The top bar's dot: "ready" (every key available), "no-jev" (a call runs, without JEV),
 * "blocked" (a call cannot start), "unknown" (the server has not answered yet).
 */
export type KeyHealth = 'ready' | 'no-jev' | 'blocked' | 'unknown'

export function keyHealth(keys: Keys): KeyHealth {
  if (missingForCall(keys).length) return 'blocked'
  const sources = PROVIDER_IDS.map((p) => keySource(keys, p))
  if (sources.includes('unknown')) return 'unknown'
  return keySource(keys, 'jev') === 'missing' ? 'no-jev' : 'ready'
}

/**
 * What pressing Call does: start, show which required keys to add (the OpenAI disambiguator uses
 * the conversation's OpenAI key, so it is gated here too), or ask before a JEV call runs on rules only.
 */
export type CallGate = { kind: 'ready' } | { kind: 'missing'; providers: Provider[] } | { kind: 'keyless-jev' }

export function callGate(keys: Keys, agent: Pick<AgentConfig, 'catalog' | 'resolver'>): CallGate {
  const missing = missingForCall(keys)
  if (missing.length) return { kind: 'missing', providers: missing }
  if (usesJev(agent) && keySource(keys, 'jev') === 'missing') return { kind: 'keyless-jev' }
  return { kind: 'ready' }
}

/** The /start body's api_keys: the browser's keys by env var name, JEV's only when the call consults JEV. */
export function callKeys(browser: PerProvider<string | null>, agent: Pick<AgentConfig, 'catalog' | 'resolver'>): Record<string, string> {
  const keys: Record<string, string> = {}
  for (const p of PROVIDER_IDS) {
    const key = browser[p]
    if (key && (p !== 'jev' || usesJev(agent))) keys[PROVIDERS[p].env] = key
  }
  return keys
}

/** "••••abcd": the last four characters only, and none of a key too short to hide the rest. */
export function maskKey(key: string): string {
  return '••••' + (key.length > 8 ? key.slice(-4) : '')
}

/** The check to show next to `key`, if it is about that key. */
export function checkFor(check: KeyCheck | null, key: string): KeyCheck | null {
  return check && check.key === key.trim() ? check : null
}

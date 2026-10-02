import { create } from 'zustand'

export type CallStatus = 'idle' | 'connecting' | 'live' | 'ended'

export interface TakenEdge {
  from: string
  to: string
  function: string
  nonce: number
}

export interface CallError {
  message: string
  hint?: string
}

/** One update_request outcome, parsed from backend `resolver_decision` (agent_tools/scheduling_tools.py). */
export interface Decision {
  /** offer | ask | refuse | confirm today; kept open so a new backend status still renders. */
  status: string
  say: string
  offers: string[]
  /** What the resolver asked about; options are empty when it asked an open question. */
  ask: { field: string; options: string[] } | null
  reason: string | null
  /** Present only when the JEV disambiguator was actually called this turn. */
  jev: { p: number | null; ms: number } | null
  /** Tokens in the tool result the LLM saw. */
  tokens: number | null
}

export interface TimedDecision extends Decision {
  at: number
}

/** Server messages defined in the phase-1 contract (RTVIServerMessageFrame payloads), plus Phase 2's resolver_decision. */
export type FlowEvent =
  | { type: 'node_entered'; node: string; state?: Record<string, unknown> }
  | { type: 'edge_taken'; function: string; from: string; to: string; args?: Record<string, unknown> }
  | { type: 'call_ended'; reason: string }
  | { type: 'resolver_decision'; decision: Decision }

export const REJECTED: CallError = {
  message: 'The backend rejected this agent — fix the issues and retry.',
  hint: 'The bot ended the session before it was ready. Check the backend log for the validation error.',
}

interface CallState {
  status: CallStatus
  /** Incremented per start; async steps of an older attempt see a different value and bail out. */
  attempt: number
  error: CallError | null
  startedAt: number | null
  activeNode: string | null
  visited: string[]
  takenEdge: TakenEdge | null
  collected: Record<string, unknown>
  endReason: string | null
  decisions: TimedDecision[]

  begin: () => number
  /** BotReady. Ignored unless the current attempt is still connecting (a hang-up may have won the race). */
  connected: () => void
  end: (error?: CallError) => void
  reset: () => void
  ingest: (event: FlowEvent) => void
}

const fresh = {
  error: null,
  startedAt: null,
  activeNode: null,
  visited: [],
  takenEdge: null,
  collected: {},
  endReason: null,
  decisions: [],
}

export const useCall = create<CallState>()((set, get) => ({
  status: 'idle',
  attempt: 0,
  ...fresh,

  begin: () => {
    const attempt = get().attempt + 1
    set({ status: 'connecting', attempt, ...fresh })
    return attempt
  },
  connected: () => {
    if (get().status === 'connecting') set({ status: 'live', startedAt: Date.now() })
  },
  end: (error) => {
    if (!isCallActive(get().status)) return
    set({ status: 'ended', error: error ?? null, activeNode: null })
  },
  reset: () => set({ status: 'idle', ...fresh }),

  ingest: (event) => {
    switch (event.type) {
      case 'node_entered': {
        const { visited, collected } = get()
        set({
          activeNode: event.node,
          visited: visited.includes(event.node) ? visited : [...visited, event.node],
          collected: event.state ? { ...collected, ...event.state } : collected,
        })
        break
      }
      case 'edge_taken':
        set({
          takenEdge: { from: event.from, to: event.to, function: event.function, nonce: Date.now() },
          collected: event.args ? { ...get().collected, ...event.args } : get().collected,
        })
        break
      case 'call_ended':
        set({ endReason: event.reason })
        break
      case 'resolver_decision':
        set({ decisions: [...get().decisions, { ...event.decision, at: Date.now() }] })
        break
    }
  },
}))

export function isCallActive(status: CallStatus): boolean {
  return status === 'connecting' || status === 'live'
}

export function parseFlowEvent(raw: unknown): FlowEvent | null {
  const type = (raw as { type?: unknown } | null)?.type
  if (type === 'node_entered' || type === 'edge_taken' || type === 'call_ended') return raw as FlowEvent
  if (type === 'resolver_decision') {
    const decision = parseDecision(raw as Record<string, unknown>)
    return decision && { type, decision }
  }
  return null
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

function parseDecision(raw: Record<string, unknown>): Decision | null {
  if (typeof raw.status !== 'string') return null
  // The backend sends `candidates` as the bare field name for an open question, or {field, options}.
  const c = raw.candidates
  const ask =
    typeof c === 'string'
      ? { field: c, options: [] }
      : isRecord(c) && typeof c.field === 'string'
        ? { field: c.field, options: strings(c.options) }
        : null
  const jev = isRecord(raw.jev) && raw.jev.used === true ? { p: num(raw.jev.p), ms: num(raw.jev.ms) ?? 0 } : null
  return {
    status: raw.status,
    say: typeof raw.say === 'string' ? raw.say : '',
    offers: strings(raw.offers),
    ask,
    reason: typeof raw.reason === 'string' ? raw.reason : null,
    jev,
    tokens: isRecord(raw.tokens) ? num(raw.tokens.result) : null,
  }
}

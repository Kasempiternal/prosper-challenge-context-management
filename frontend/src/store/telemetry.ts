import { create } from 'zustand'
import type { Stage, TelemetryEvent } from '../lib/telemetry'

export const STAGES: readonly Stage[] = ['mic', 'stt', 'llm', 'tools', 'tts', 'speaker']
export const MAX_TURNS = 8
const MAX_LINES = 80
const MAX_BUBBLES = 6
const MAX_CALLS = 40

export interface StageState {
  active: boolean
  /** Last measured latency; for the speaker it is the last voice-to-voice total. */
  ms: number | null
  source: 'ttfb' | 'processing' | 'event' | null
}

export type SegmentKind = 'stt' | 'llm' | 'tool' | 'resolver' | 'jev' | 'tts'

/** One span of a turn, in ms relative to the turn start (user stopped speaking). */
export interface Segment {
  kind: SegmentKind
  label: string
  start: number
  ms: number
}

export interface Turn {
  id: number
  startAt: number
  node: string | null
  segments: Segment[]
  /** User stopped speaking → bot started speaking; null until the bot speaks. */
  totalMs: number | null
  /** A new user utterance began: later events belong to the next turn. */
  sealed: boolean
}

export type Line =
  | { id: number; role: 'user'; final: string; interim: string; open: boolean }
  | { id: number; role: 'bot'; llm: string; tts: string; open: boolean }
  | { id: number; role: 'edge'; fn: string; to: string }

type UserLine = Extract<Line, { role: 'user' }>
type BotLine = Extract<Line, { role: 'bot' }>
type NewLine = { [R in Line['role']]: Omit<Extract<Line, { role: R }>, 'id'> }[Line['role']]

export interface Bubble {
  id: number
  node: string | null
  label: string
  detail: string
  tone: 'tool' | 'jev' | 'warn'
  at: number
}

export interface Usage {
  promptTokens: number
  completionTokens: number
  ttsChars: number
  /** Null until the STT service reports usage; callers fall back to the call duration. */
  sttSeconds: number | null
  jevTokens: number
  jevCalls: number
  /** Prompt tokens of each LLM call, oldest first. */
  promptPerCall: number[]
}

export interface Telemetry {
  seq: number
  node: string | null
  liveFrom: number | null
  endedAt: number | null
  lastFinalAt: number | null
  stages: Record<Stage, StageState>
  toolsInFlight: Record<string, { at: number; name: string | null }>
  turns: Turn[]
  lines: Line[]
  bubbles: Bubble[]
  usage: Usage
}

const idleStage: StageState = { active: false, ms: null, source: null }

export const initialTelemetry: Telemetry = {
  seq: 0,
  node: null,
  liveFrom: null,
  endedAt: null,
  lastFinalAt: null,
  stages: { mic: idleStage, stt: idleStage, llm: idleStage, tools: idleStage, tts: idleStage, speaker: idleStage },
  toolsInFlight: {},
  turns: [],
  lines: [],
  bubbles: [],
  usage: {
    promptTokens: 0,
    completionTokens: 0,
    ttsChars: 0,
    sttSeconds: null,
    jevTokens: 0,
    jevCalls: 0,
    promptPerCall: [],
  },
}

const keepLast = <T>(items: T[], n: number) => (items.length > n ? items.slice(items.length - n) : items)

function setStage(s: Telemetry, stage: Stage, patch: Partial<StageState>): Telemetry {
  return { ...s, stages: { ...s.stages, [stage]: { ...s.stages[stage], ...patch } } }
}

function openTurn(s: Telemetry): Turn | null {
  const turn = s.turns.at(-1)
  return turn && !turn.sealed ? turn : null
}

function patchTurn(s: Telemetry, fn: (turn: Turn) => Turn): Telemetry {
  const turn = openTurn(s)
  return turn ? { ...s, turns: [...s.turns.slice(0, -1), fn(turn)] } : s
}

function addSegment(s: Telemetry, at: number, kind: SegmentKind, label: string, ms: number): Telemetry {
  return patchTurn(s, (turn) => ({
    ...turn,
    segments: [...turn.segments, { kind, label, ms, start: Math.max(0, at - turn.startAt - ms) }],
  }))
}

function addBubble(s: Telemetry, at: number, bubble: Omit<Bubble, 'id' | 'node' | 'at'>): Telemetry {
  const seq = s.seq + 1
  return { ...s, seq, bubbles: keepLast([...s.bubbles, { ...bubble, id: seq, node: s.node, at }], MAX_BUBBLES) }
}

function pushLine(s: Telemetry, line: NewLine): Telemetry {
  const seq = s.seq + 1
  return { ...s, seq, lines: keepLast([...s.lines, { ...line, id: seq } as Line], MAX_LINES) }
}

function closeLines(s: Telemetry, role: 'user' | 'bot'): Telemetry {
  const last = s.lines.at(-1)
  if (!last || last.role !== role || !last.open) return s
  return { ...s, lines: [...s.lines.slice(0, -1), { ...last, open: false }] }
}

function withUserLine(s: Telemetry, fn: (line: UserLine) => UserLine): Telemetry {
  const last = s.lines.at(-1)
  if (last?.role === 'user' && last.open) return { ...s, lines: [...s.lines.slice(0, -1), fn(last)] }
  const next = pushLine(s, { role: 'user', final: '', interim: '', open: true })
  return withUserLine(next, fn)
}

function withBotLine(s: Telemetry, fn: (line: BotLine) => BotLine): Telemetry {
  const last = s.lines.at(-1)
  if (last?.role === 'bot' && last.open) return { ...s, lines: [...s.lines.slice(0, -1), fn(last)] }
  const next = pushLine(closeLines(s, 'user'), { role: 'bot', llm: '', tts: '', open: true })
  return withBotLine(next, fn)
}

const join = (a: string, b: string) => (a && b ? `${a} ${b}` : a || b)

function stopAll(s: Telemetry): Telemetry {
  const stages = Object.fromEntries(STAGES.map((k) => [k, { ...s.stages[k], active: false }])) as Telemetry['stages']
  return { ...s, stages, toolsInFlight: {} }
}

export function reduce(s: Telemetry, e: TelemetryEvent): Telemetry {
  switch (e.kind) {
    case 'reset':
      return initialTelemetry
    case 'call_live':
      return { ...s, liveFrom: e.at, endedAt: null }
    case 'call_ended':
      return s.endedAt === null ? { ...stopAll(s), endedAt: e.at } : s
    case 'user_started': {
      const sealed = patchTurn(s, (turn) => ({ ...turn, sealed: true }))
      const next = setStage(setStage(closeLines(sealed, 'bot'), 'mic', { active: true }), 'stt', { active: true })
      return { ...next, lastFinalAt: null }
    }
    case 'user_stopped': {
      const last = s.turns.at(-1)
      const kept = last && last.totalMs === null ? s.turns.slice(0, -1) : s.turns
      const seq = s.seq + 1
      const segments: Segment[] = s.lastFinalAt !== null ? [{ kind: 'stt', label: 'STT final', start: 0, ms: 0 }] : []
      const turn: Turn = { id: seq, startAt: e.at, node: s.node, segments, totalMs: null, sealed: false }
      return setStage({ ...s, seq, turns: keepLast([...kept, turn], MAX_TURNS) }, 'mic', { active: false })
    }
    case 'transcript': {
      if (!e.final) return setStage(withUserLine(s, (l) => ({ ...l, interim: e.text })), 'stt', { active: true })
      const next = setStage(withUserLine(s, (l) => ({ ...l, final: join(l.final, e.text), interim: '' })), 'stt', {
        active: false,
      })
      const turn = openTurn(next)
      if (!turn || turn.totalMs !== null || turn.segments.some((g) => g.kind !== 'stt')) return { ...next, lastFinalAt: e.at }
      return patchTurn({ ...next, lastFinalAt: e.at }, (t) => ({
        ...t,
        segments: [{ kind: 'stt', label: 'STT final', start: 0, ms: e.at - t.startAt }],
      }))
    }
    case 'llm_started':
      return setStage(setStage(s, 'llm', { active: true }), 'stt', { active: false })
    case 'llm_text':
      return withBotLine(s, (l) => ({ ...l, llm: l.llm + e.text }))
    case 'llm_stopped':
      return setStage(s, 'llm', { active: false })
    case 'tool_called':
      return setStage(s, 'tools', { active: true })
    case 'tool_running':
      return setStage({ ...s, toolsInFlight: { ...s.toolsInFlight, [e.id]: { at: e.at, name: e.name } } }, 'tools', {
        active: true,
      })
    case 'tool_stopped': {
      const { [e.id]: started, ...rest } = s.toolsInFlight
      const idle = Object.keys(rest).length === 0
      const next = setStage({ ...s, toolsInFlight: rest }, 'tools', { active: !idle })
      if (!started || e.cancelled) return next
      const ms = e.at - started.at
      const named = setStage(next, 'tools', { ms, source: 'event' })
      return addSegment(named, e.at, 'tool', e.name ?? started.name ?? 'tool call', ms)
    }
    case 'resolver': {
      const timed = e.ms === null ? s : addSegment(setStage(s, 'tools', { ms: e.ms, source: 'event' }), e.at, 'resolver', 'resolver', e.ms)
      return addBubble(timed, e.at, {
        label: 'update_request',
        detail: e.ms === null ? e.status : `${e.status} · ${e.ms} ms`,
        tone: 'tool',
      })
    }
    case 'jev': {
      const usage = { ...s.usage, jevTokens: s.usage.jevTokens + e.inputTokens, jevCalls: s.usage.jevCalls + 1 }
      if (e.purpose === 'warmup') return { ...s, usage }
      const timed = addSegment(setStage({ ...s, usage }, 'tools', { ms: e.ms, source: 'event' }), e.at, 'jev', `JEV ${e.purpose}`, e.ms)
      const detail = e.ok ? `${e.p === null ? '' : `p=${e.p.toFixed(2)} · `}${e.ms} ms` : `failed · ${e.ms} ms`
      return addBubble(timed, e.at, {
        label: `JEV ${e.purpose}`,
        detail,
        tone: e.ok ? 'jev' : 'warn',
      })
    }
    case 'tts_started':
      return setStage(s, 'tts', { active: true })
    case 'tts_text':
      return withBotLine(s, (l) => ({ ...l, tts: join(l.tts, e.text) }))
    case 'tts_stopped':
      return setStage(s, 'tts', { active: false })
    case 'bot_started': {
      const turn = openTurn(s)
      const next = setStage(s, 'speaker', { active: true })
      if (!turn || turn.totalMs !== null) return next
      const totalMs = e.at - turn.startAt
      return setStage(patchTurn(next, (t) => ({ ...t, totalMs })), 'speaker', { ms: totalMs, source: 'event' })
    }
    case 'bot_stopped':
      return setStage(s, 'speaker', { active: false })
    case 'latency': {
      const current = s.stages[e.stage]
      const next =
        e.metric === 'ttfb' || current.source !== 'ttfb' ? setStage(s, e.stage, { ms: e.ms, source: e.metric }) : s
      if (e.metric !== 'ttfb' || e.stage === 'stt') return next
      return addSegment(next, e.at, e.stage, e.stage === 'llm' ? 'LLM TTFB' : 'TTS TTFB', e.ms)
    }
    case 'llm_usage':
      return {
        ...s,
        usage: {
          ...s.usage,
          promptTokens: s.usage.promptTokens + e.prompt,
          completionTokens: s.usage.completionTokens + e.completion,
          promptPerCall: keepLast([...s.usage.promptPerCall, e.prompt], MAX_CALLS),
        },
      }
    case 'tts_usage':
      return { ...s, usage: { ...s.usage, ttsChars: s.usage.ttsChars + e.chars } }
    case 'stt_usage':
      return { ...s, usage: { ...s.usage, sttSeconds: (s.usage.sttSeconds ?? 0) + e.seconds } }
    case 'node_entered':
      return { ...s, node: e.node }
    case 'edge_taken':
      return pushLine(closeLines(closeLines(s, 'user'), 'bot'), { role: 'edge', fn: e.fn, to: e.to })
  }
}

interface TelemetryStore extends Telemetry {
  dispatch: (events: TelemetryEvent[]) => void
}

export const useTelemetry = create<TelemetryStore>()((set, get) => ({
  ...initialTelemetry,
  dispatch: (events) => {
    if (events.length === 0) return
    const { dispatch: _dispatch, ...state } = get()
    set(events.reduce(reduce, state))
  },
}))

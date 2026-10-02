import { RTVIEvent, type RTVIEventHandler } from '@pipecat-ai/client-js'
import { isChooser, type Chooser } from './chooser'

export type Stage = 'mic' | 'stt' | 'llm' | 'tools' | 'tts' | 'speaker'
export type MetricStage = 'stt' | 'llm' | 'tts'

export type TelemetrySignal =
  | { kind: 'user_started' }
  | { kind: 'user_stopped' }
  | { kind: 'transcript'; text: string; final: boolean }
  | { kind: 'llm_started' }
  | { kind: 'llm_text'; text: string }
  | { kind: 'llm_stopped' }
  | { kind: 'tool_called'; name: string | null }
  | { kind: 'tool_running'; id: string; name: string | null }
  | { kind: 'tool_stopped'; id: string; name: string | null; cancelled: boolean }
  | { kind: 'tts_started' }
  | { kind: 'tts_text'; text: string }
  | { kind: 'tts_stopped' }
  | { kind: 'bot_started' }
  | { kind: 'bot_stopped' }
  | { kind: 'latency'; stage: MetricStage; metric: 'ttfb' | 'processing'; ms: number }
  | { kind: 'llm_usage'; prompt: number; completion: number }
  | { kind: 'tts_usage'; chars: number }
  | { kind: 'stt_usage'; seconds: number }
  | { kind: 'resolver'; status: string; ms: number | null }
  | { kind: 'model'; provider: Chooser; purpose: string; ms: number; inputTokens: number; usd: number; ok: boolean; p: number | null }
  | { kind: 'resolver_mode'; requested: Chooser; active: Chooser }
  | { kind: 'node_entered'; node: string }
  | { kind: 'edge_taken'; fn: string; from: string; to: string }
  | { kind: 'call_live' }
  | { kind: 'call_ended' }
  | { kind: 'reset' }

export type TelemetryEvent = TelemetrySignal & { at: number }

type Adapter = {
  [E in RTVIEvent]?: (...args: Parameters<NonNullable<RTVIEventHandler<E>>>) => TelemetrySignal[]
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const str = (v: unknown): string | null => (typeof v === 'string' ? v : null)
const list = (v: unknown): Record<string, unknown>[] => (Array.isArray(v) ? v.filter(isRecord) : [])

const STAGE_MARKERS: ReadonlyArray<[MetricStage, string]> = [
  ['stt', 'STT'],
  ['llm', 'LLM'],
  ['tts', 'TTS'],
]

export function stageOf(processor: string): MetricStage | null {
  return STAGE_MARKERS.find(([, marker]) => processor.includes(marker))?.[0] ?? null
}

function latencies(entries: unknown, metric: 'ttfb' | 'processing'): TelemetrySignal[] {
  return list(entries).flatMap((m): TelemetrySignal[] => {
    const stage = stageOf(str(m.processor) ?? '')
    const seconds = num(m.value)
    return stage && seconds !== null ? [{ kind: 'latency', stage, metric, ms: Math.round(seconds * 1000) }] : []
  })
}

export function metricsSignals(data: unknown): TelemetrySignal[] {
  if (!isRecord(data)) return []
  return [
    ...latencies(data.ttfb, 'ttfb'),
    ...latencies(data.processing, 'processing'),
    ...list(data.tokens).flatMap((t): TelemetrySignal[] => {
      const prompt = num(t.prompt_tokens)
      return prompt === null ? [] : [{ kind: 'llm_usage', prompt, completion: num(t.completion_tokens) ?? 0 }]
    }),
    ...list(data.characters).flatMap((c): TelemetrySignal[] => {
      const chars = num(c.value)
      return chars === null ? [] : [{ kind: 'tts_usage', chars }]
    }),
    ...list(data.stt_usage).flatMap((s): TelemetrySignal[] => {
      const seconds = isRecord(s.value) ? num(s.value.audio_seconds) : null
      return seconds === null ? [] : [{ kind: 'stt_usage', seconds }]
    }),
  ]
}

export function serverSignals(data: unknown): TelemetrySignal[] {
  if (!isRecord(data)) return []
  switch (data.type) {
    case 'node_entered': {
      const node = str(data.node)
      return node === null ? [] : [{ kind: 'node_entered', node }]
    }
    case 'edge_taken':
      return [{ kind: 'edge_taken', fn: str(data.function) ?? '', from: str(data.from) ?? '', to: str(data.to) ?? '' }]
    case 'call_ended':
      return [{ kind: 'call_ended' }]
    case 'resolver_decision': {
      const status = str(data.status)
      return status === null ? [] : [{ kind: 'resolver', status, ms: num(data.ms) }]
    }
    case 'resolver_mode':
      return isChooser(data.requested) && isChooser(data.active)
        ? [{ kind: 'resolver_mode', requested: data.requested, active: data.active }]
        : []
    case 'model_call': {
      const ms = num(data.ms)
      if (ms === null || !isChooser(data.provider)) return []
      return [
        {
          kind: 'model',
          provider: data.provider,
          purpose: str(data.purpose) ?? 'call',
          ms,
          inputTokens: num(data.input_tokens) ?? 0,
          usd: num(data.usd) ?? 0,
          ok: data.ok !== false,
          p: num(data.p),
        },
      ]
    }
    default:
      return []
  }
}

export const RTVI_ADAPTER: Adapter = {
  [RTVIEvent.UserStartedSpeaking]: () => [{ kind: 'user_started' }],
  [RTVIEvent.UserStoppedSpeaking]: () => [{ kind: 'user_stopped' }],
  [RTVIEvent.UserTranscript]: (data) => [{ kind: 'transcript', text: data.text, final: data.final }],
  [RTVIEvent.BotLlmStarted]: () => [{ kind: 'llm_started' }],
  [RTVIEvent.BotLlmText]: (data) => [{ kind: 'llm_text', text: data.text }],
  [RTVIEvent.BotLlmStopped]: () => [{ kind: 'llm_stopped' }],
  [RTVIEvent.LLMFunctionCallStarted]: (data) => [{ kind: 'tool_called', name: data.function_name ?? null }],
  [RTVIEvent.LLMFunctionCallInProgress]: (data) => [
    { kind: 'tool_running', id: data.tool_call_id, name: data.function_name ?? null },
  ],
  [RTVIEvent.LLMFunctionCallStopped]: (data) => [
    { kind: 'tool_stopped', id: data.tool_call_id, name: data.function_name ?? null, cancelled: data.cancelled },
  ],
  [RTVIEvent.BotTtsStarted]: () => [{ kind: 'tts_started' }],
  [RTVIEvent.BotTtsText]: (data) => [{ kind: 'tts_text', text: data.text }],
  [RTVIEvent.BotTtsStopped]: () => [{ kind: 'tts_stopped' }],
  [RTVIEvent.BotStartedSpeaking]: () => [{ kind: 'bot_started' }],
  [RTVIEvent.BotStoppedSpeaking]: () => [{ kind: 'bot_stopped' }],
  [RTVIEvent.Metrics]: (data) => metricsSignals(data),
  [RTVIEvent.ServerMessage]: (data) => serverSignals(data),
}

const untyped = RTVI_ADAPTER as unknown as Partial<Record<RTVIEvent, (data: unknown) => TelemetrySignal[]>>

export const ADAPTED_EVENTS = Object.keys(RTVI_ADAPTER) as RTVIEvent[]

export function toSignals(event: RTVIEvent, data: unknown): TelemetrySignal[] {
  return untyped[event]?.(data) ?? []
}

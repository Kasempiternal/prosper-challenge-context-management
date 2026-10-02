import { RTVIEvent } from '@pipecat-ai/client-js'
import { toSignals, type TelemetryEvent } from './telemetry'

/**
 * A greeting and two user turns as the client receives them, in ms from call start. Payload shapes
 * follow pipecat's RTVIObserver (metrics in seconds, `tokens` without a processor, function-call
 * events without names at the default report level) and the backend's server messages.
 */
export const RECORDED_CALL: ReadonlyArray<readonly [number, RTVIEvent, unknown?]> = [
  [100, RTVIEvent.ServerMessage, { type: 'node_entered', node: 'greeting', state: {} }],
  [110, RTVIEvent.ServerMessage, { type: 'resolver_mode', requested: 'jev', active: 'jev' }],
  [120, RTVIEvent.ServerMessage, { type: 'model_call', provider: 'jev', purpose: 'warmup', ms: 820, input_tokens: 300, usd: 0.000012, ok: true, p: null }],
  [200, RTVIEvent.BotLlmStarted],
  [650, RTVIEvent.Metrics, { ttfb: [{ processor: 'SerialToolCallsLLMService#0', model: 'gpt-4o', value: 0.45 }] }],
  [700, RTVIEvent.BotLlmText, { text: 'Hi! ' }],
  [720, RTVIEvent.BotLlmText, { text: 'How can I help?' }],
  [760, RTVIEvent.BotLlmStopped],
  [770, RTVIEvent.Metrics, { tokens: [{ prompt_tokens: 812, completion_tokens: 9, total_tokens: 821 }] }],
  [800, RTVIEvent.BotTtsStarted],
  [980, RTVIEvent.Metrics, { ttfb: [{ processor: 'ElevenLabsTTSService#0', value: 0.18 }] }],
  [1000, RTVIEvent.BotStartedSpeaking],
  [1010, RTVIEvent.BotTtsText, { text: 'Hi!' }],
  [1600, RTVIEvent.BotTtsText, { text: 'How can I help?' }],
  [2600, RTVIEvent.BotStoppedSpeaking],
  [2650, RTVIEvent.BotTtsStopped],
  [2660, RTVIEvent.Metrics, { characters: [{ processor: 'ElevenLabsTTSService#0', value: 21 }] }],

  [4000, RTVIEvent.UserStartedSpeaking],
  [4300, RTVIEvent.UserTranscript, { text: "I'd like to", final: false, timestamp: '', user_id: '' }],
  [5200, RTVIEvent.UserTranscript, { text: "I'd like to book a heart checkup.", final: true, timestamp: '', user_id: '' }],
  [5600, RTVIEvent.UserStoppedSpeaking],
  [5610, RTVIEvent.BotLlmStarted],
  [6030, RTVIEvent.Metrics, { ttfb: [{ processor: 'SerialToolCallsLLMService#0', model: 'gpt-4o', value: 0.41 }] }],
  [6040, RTVIEvent.LLMFunctionCallStarted, {}],
  [6045, RTVIEvent.LLMFunctionCallInProgress, { tool_call_id: 'call_1' }],
  [6050, RTVIEvent.ServerMessage, { type: 'edge_taken', function: 'start', from: 'greeting', to: 'schedule', args: {} }],
  [6052, RTVIEvent.ServerMessage, { type: 'node_entered', node: 'schedule', state: {} }],
  [6060, RTVIEvent.LLMFunctionCallStopped, { tool_call_id: 'call_1', cancelled: false }],
  [6070, RTVIEvent.BotLlmStopped],
  [6075, RTVIEvent.Metrics, { tokens: [{ prompt_tokens: 930, completion_tokens: 14, total_tokens: 944 }] }],
  [6100, RTVIEvent.BotLlmStarted],
  [6480, RTVIEvent.Metrics, { ttfb: [{ processor: 'SerialToolCallsLLMService#0', model: 'gpt-4o', value: 0.37 }] }],
  [6500, RTVIEvent.BotLlmText, { text: 'Sure — which doctor ' }],
  [6520, RTVIEvent.BotLlmText, { text: 'would you like to see?' }],
  [6560, RTVIEvent.BotLlmStopped],
  [6565, RTVIEvent.Metrics, { tokens: [{ prompt_tokens: 951, completion_tokens: 22, total_tokens: 973 }] }],
  [6600, RTVIEvent.BotTtsStarted],
  [6800, RTVIEvent.Metrics, { ttfb: [{ processor: 'ElevenLabsTTSService#0', value: 0.19 }] }],
  [6820, RTVIEvent.BotStartedSpeaking],
  [6830, RTVIEvent.BotTtsText, { text: 'Sure — which doctor would you like to see?' }],
  [8000, RTVIEvent.BotStoppedSpeaking],
  [8010, RTVIEvent.BotTtsStopped],
  [8020, RTVIEvent.Metrics, {
    characters: [{ processor: 'ElevenLabsTTSService#0', value: 48 }],
    stt_usage: [{ processor: 'ElevenLabsRealtimeSTTService#0', value: { audio_seconds: 5.6 } }],
  }],

  [10000, RTVIEvent.UserStartedSpeaking],
  [11000, RTVIEvent.UserTranscript, { text: 'Dr. Chen please, Tuesday morning.', final: true, timestamp: '', user_id: '' }],
  [11050, RTVIEvent.Metrics, { ttfb: [{ processor: 'ElevenLabsRealtimeSTTService#0', value: 0.21 }] }],
  [11400, RTVIEvent.UserStoppedSpeaking],
  [11420, RTVIEvent.BotLlmStarted],
  [11800, RTVIEvent.Metrics, { ttfb: [{ processor: 'SerialToolCallsLLMService#0', model: 'gpt-4o', value: 0.38 }] }],
  [11810, RTVIEvent.LLMFunctionCallStarted, {}],
  [11812, RTVIEvent.LLMFunctionCallInProgress, { tool_call_id: 'call_2' }],
  [12355, RTVIEvent.ServerMessage, { type: 'model_call', provider: 'jev', purpose: 'provider', ms: 540, input_tokens: 1800, usd: 0.000072, ok: true, p: 0.92 }],
  [12362, RTVIEvent.ServerMessage, {
    type: 'resolver_decision', status: 'offer', ms: 552, say: 'Dr. Emily Chen has Tuesday at 9. Does that work?',
    offers: ['1 Tue 09:00 Downtown Dr. Emily Chen'], model: { used: true, provider: 'jev', p: 0.92, ms: 540 }, tokens: { result: 77 },
  }],
  [12365, RTVIEvent.LLMFunctionCallStopped, { tool_call_id: 'call_2', cancelled: false }],
  [12370, RTVIEvent.BotLlmStopped],
  [12375, RTVIEvent.Metrics, {
    tokens: [{ prompt_tokens: 1012, completion_tokens: 3, total_tokens: 1015 }],
    processing: [{ processor: 'SerialToolCallsLLMService#0', value: 0.95 }],
  }],
  [12400, RTVIEvent.BotLlmStarted],
  [12760, RTVIEvent.Metrics, { ttfb: [{ processor: 'SerialToolCallsLLMService#0', model: 'gpt-4o', value: 0.36 }] }],
  [12780, RTVIEvent.BotLlmText, { text: 'Dr. Emily Chen has Tuesday at 9. ' }],
  [12790, RTVIEvent.BotLlmText, { text: 'Does that work?' }],
  [12800, RTVIEvent.BotLlmStopped],
  [12805, RTVIEvent.Metrics, { tokens: [{ prompt_tokens: 1104, completion_tokens: 25, total_tokens: 1129 }] }],
  [12820, RTVIEvent.BotTtsStarted],
  [13010, RTVIEvent.Metrics, { ttfb: [{ processor: 'ElevenLabsTTSService#0', value: 0.18 }] }],
  [13030, RTVIEvent.BotStartedSpeaking],
  [13040, RTVIEvent.BotTtsText, { text: 'Dr. Emily Chen has Tuesday at 9.' }],
  [13900, RTVIEvent.Metrics, {
    characters: [{ processor: 'ElevenLabsTTSService#0', value: 60 }],
    stt_usage: [{ processor: 'ElevenLabsRealtimeSTTService#0', value: { audio_seconds: 6.0 } }],
  }],
]

/** Runs recorded RTVI events through the adapter, stamped from `t0`, stopping before `until` ms. */
export function replay(t0: number, until = Infinity): TelemetryEvent[] {
  return [
    { kind: 'call_live', at: t0 },
    ...RECORDED_CALL.filter(([t]) => t < until).flatMap(([t, event, payload]) =>
      toSignals(event, payload).map((signal) => ({ ...signal, at: t0 + t })),
    ),
  ]
}

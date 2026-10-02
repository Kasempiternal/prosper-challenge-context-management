import { RTVIEvent } from '@pipecat-ai/client-js'
import { describe, expect, it } from 'vitest'
import { DEFAULT_PRICES, costLines } from '../lib/pricing'
import { metricsSignals, stageOf, toSignals } from '../lib/telemetry'
import { replay } from '../lib/telemetry.fixture'
import { initialTelemetry, reduce } from './telemetry'

const T0 = 1_000_000
const run = (until?: number) => replay(T0, until).reduce(reduce, initialTelemetry)

describe('RTVI adapter', () => {
  it('classifies pipecat processor names case-sensitively', () => {
    expect(stageOf('ElevenLabsRealtimeSTTService#0')).toBe('stt')
    expect(stageOf('ElevenLabsTTSService#0')).toBe('tts')
    expect(stageOf('SerialToolCallsLLMService#0')).toBe('llm')
    expect(stageOf('SileroVADAnalyzer#0')).toBeNull()
  })

  it('normalizes one metrics message into latency and usage signals', () => {
    expect(
      metricsSignals({
        ttfb: [{ processor: 'SerialToolCallsLLMService#0', model: 'gpt-4o', value: 0.4123 }, { processor: 'Other#0', value: 1 }],
        tokens: [{ prompt_tokens: 900, completion_tokens: 12, total_tokens: 912 }],
        characters: [{ processor: 'ElevenLabsTTSService#0', value: 40 }],
        stt_usage: [{ processor: 'ElevenLabsRealtimeSTTService#0', value: { audio_seconds: 2.5 } }],
        ttfat: [{ processor: 'SerialToolCallsLLMService#0', ttfat: 0.5, ttfb: 0.4, thinking_time: 0.1 }],
      }),
    ).toEqual([
      { kind: 'latency', stage: 'llm', metric: 'ttfb', ms: 412 },
      { kind: 'llm_usage', prompt: 900, completion: 12 },
      { kind: 'tts_usage', chars: 40 },
      { kind: 'stt_usage', seconds: 2.5 },
    ])
  })

  it('ignores unknown server messages and tolerates a resolver_decision without ms', () => {
    expect(toSignals(RTVIEvent.ServerMessage, { type: 'something_new' })).toEqual([])
    expect(toSignals(RTVIEvent.ServerMessage, { type: 'resolver_decision', status: 'ask' })).toEqual([
      { kind: 'resolver', status: 'ask', ms: null },
    ])
  })
})

describe('telemetry reducer on a recorded call', () => {
  it('builds one waterfall per user turn, ending at bot-started-speaking', () => {
    const { turns } = run()
    expect(turns.map(({ id: _id, startAt, ...t }) => ({ ...t, startAt: startAt - T0 }))).toEqual([
      {
        startAt: 5600,
        node: 'greeting',
        totalMs: 1220,
        sealed: true,
        segments: [
          { kind: 'stt', label: 'STT final', start: 0, ms: 0 },
          { kind: 'llm', label: 'LLM TTFB', start: 20, ms: 410 },
          { kind: 'tool', label: 'tool call', start: 445, ms: 15 },
          { kind: 'llm', label: 'LLM TTFB', start: 510, ms: 370 },
          { kind: 'tts', label: 'TTS TTFB', start: 1010, ms: 190 },
        ],
      },
      {
        startAt: 11400,
        node: 'schedule',
        totalMs: 1630,
        sealed: false,
        segments: [
          { kind: 'stt', label: 'STT final', start: 0, ms: 0 },
          { kind: 'llm', label: 'LLM TTFB', start: 20, ms: 380 },
          { kind: 'jev', label: 'JEV provider', start: 415, ms: 540 },
          { kind: 'resolver', label: 'resolver', start: 410, ms: 552 },
          { kind: 'tool', label: 'tool call', start: 412, ms: 553 },
          { kind: 'llm', label: 'LLM TTFB', start: 1000, ms: 360 },
          { kind: 'tts', label: 'TTS TTFB', start: 1430, ms: 180 },
        ],
      },
    ])
  })

  it('measures STT from end of turn when the final transcript arrives late', () => {
    const late = [
      { kind: 'user_started', at: 0 },
      { kind: 'transcript', text: 'hello', final: false, at: 100 },
      { kind: 'user_stopped', at: 500 },
      { kind: 'transcript', text: 'hello there', final: true, at: 740 },
    ] as const
    const s = late.reduce(reduce, initialTelemetry)
    expect(s.turns[0].segments).toEqual([{ kind: 'stt', label: 'STT final', start: 0, ms: 240 }])
  })

  it('tracks each stage’s activity and last latency, preferring ttfb over processing', () => {
    const mid = run(12000)
    expect(mid.stages.llm).toEqual({ active: true, ms: 380, source: 'ttfb' })
    expect(mid.stages.tools.active).toBe(true)
    expect(mid.stages.mic.active).toBe(false)

    const end = run()
    expect(end.stages).toEqual({
      mic: { active: false, ms: null, source: null },
      stt: { active: false, ms: 210, source: 'ttfb' },
      llm: { active: false, ms: 360, source: 'ttfb' },
      tools: { active: false, ms: 553, source: 'event' },
      tts: { active: true, ms: 180, source: 'ttfb' },
      speaker: { active: true, ms: 1630, source: 'event' },
    })
  })

  it('keeps a dev transcript: finals replace interims, bot text streams, edges divide', () => {
    expect(run(5000).lines.at(-1)).toMatchObject({ role: 'user', final: '', interim: "I'd like to", open: true })
    expect(run(6510).lines.at(-1)).toMatchObject({ role: 'bot', llm: 'Sure — which doctor ', tts: '', open: true })
    expect(run().lines.map(({ id: _id, ...l }) => l)).toEqual([
      { role: 'bot', llm: 'Hi! How can I help?', tts: 'Hi! How can I help?', open: false },
      { role: 'user', final: "I'd like to book a heart checkup.", interim: '', open: false },
      { role: 'edge', fn: 'start', to: 'schedule' },
      { role: 'bot', llm: 'Sure — which doctor would you like to see?', tts: 'Sure — which doctor would you like to see?', open: false },
      { role: 'user', final: 'Dr. Chen please, Tuesday morning.', interim: '', open: false },
      { role: 'bot', llm: 'Dr. Emily Chen has Tuesday at 9. Does that work?', tts: 'Dr. Emily Chen has Tuesday at 9.', open: true },
    ])
  })

  it('raises tool bubbles on the node that was active, skipping the JEV warm-up', () => {
    expect(run().bubbles.map(({ id: _id, at, ...b }) => ({ ...b, at: at - T0 }))).toEqual([
      { node: 'schedule', label: 'JEV provider', detail: 'p=0.92 · 540 ms', tone: 'jev', at: 12355 },
      { node: 'schedule', label: 'update_request', detail: 'offer · 552 ms', tone: 'tool', at: 12362 },
    ])
  })

  it('accumulates usage and prices it', () => {
    const { usage } = run()
    expect(usage).toEqual({
      promptTokens: 4809,
      completionTokens: 73,
      ttsChars: 129,
      sttSeconds: 11.6,
      jevTokens: 2100,
      jevCalls: 2,
      promptPerCall: [812, 930, 951, 1012, 1104],
    })
    const lines = costLines(usage, usage.sttSeconds ?? 0, DEFAULT_PRICES)
    expect(lines.map((l) => [l.key, Number(l.usd.toFixed(7))])).toEqual([
      ['openai', 0.0127525],
      ['tts', 0.0387],
      ['stt', 0.0012889],
      ['jev', 0.000084],
    ])
  })

  it('stops every stage when the call ends and resets on a new call', () => {
    const ended = reduce(run(), { kind: 'call_ended', at: T0 + 14000 })
    expect(Object.values(ended.stages).some((st) => st.active)).toBe(false)
    expect(ended.endedAt).toBe(T0 + 14000)
    expect(reduce(ended, { kind: 'reset', at: 0 })).toBe(initialTelemetry)
  })
})

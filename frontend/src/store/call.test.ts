import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AgentConfig } from '../types/agent'
import { parseFlowEvent, startRequestData, useCall } from './call'
import { useKeys } from './keys'

const ingestRaw = (raw: unknown) => {
  const event = parseFlowEvent(raw)
  if (event) useCall.getState().ingest(event)
  return event
}

// Payload shapes copied from backend/agent_tools/scheduling_tools.py `_decision_event`.
const OFFER = {
  type: 'resolver_decision',
  status: 'offer',
  say: 'Dr. Emily Chen has Tuesday at 9 or Wednesday at 2 at Downtown. Which works?',
  summary: 'service: cardiology consult; next: caller picks a time.',
  notes: [],
  valid_rows: 6,
  offers: ['1 Tue 09:00 Downtown Dr. Emily Chen', '2 Wed 14:00 Downtown Dr. Emily Chen'],
  model: { used: true, provider: 'openai', p: 0.92, ms: 540 },
  tokens: { result: 77 },
}
const ASK = {
  type: 'resolver_decision',
  status: 'ask',
  say: 'Do you mean Dr. David Chen or Dr. Emily Chen?',
  candidates: { field: 'provider', options: ['Dr. David Chen', 'Dr. Emily Chen'] },
  model: { used: false },
  tokens: { result: 54 },
}

describe('resolver_decision', () => {
  beforeEach(() => useCall.getState().reset())

  it("shows when the handler kept the caller's words over the model's", () => {
    useCall.getState().begin()
    ingestRaw({ ...ASK, kept_words: { model: ['Washington, DC'], caller: 'Washington.' } })
    ingestRaw(ASK)
    const [kept, plain] = useCall.getState().decisions
    expect(kept.kept).toEqual({ model: ['Washington, DC'], caller: 'Washington.' })
    expect(plain.kept).toBeUndefined()
  })

  it('appends parsed decisions in arrival order', () => {
    useCall.getState().begin()
    ingestRaw(OFFER)
    ingestRaw(ASK)
    ingestRaw({ type: 'resolver_decision', status: 'ask', say: 'Are you a new patient?', candidates: 'is_new', model: { used: false } })

    const decisions = useCall.getState().decisions.map(({ at: _at, ...d }) => d)
    expect(decisions).toEqual([
      {
        status: 'offer',
        say: OFFER.say,
        offers: OFFER.offers,
        ask: null,
        reason: null,
        model: { provider: 'openai', p: 0.92, ms: 540 },
        tokens: 77,
      },
      {
        status: 'ask',
        say: ASK.say,
        offers: [],
        ask: { field: 'provider', options: ['Dr. David Chen', 'Dr. Emily Chen'] },
        reason: null,
        model: null,
        tokens: 54,
      },
      {
        status: 'ask',
        say: 'Are you a new patient?',
        offers: [],
        ask: { field: 'is_new', options: [] },
        reason: null,
        model: null,
        tokens: null,
      },
    ])
  })

  it('keeps the refusal reason and a model call with no probability or known provider', () => {
    ingestRaw({ type: 'resolver_decision', status: 'refuse', say: 'No.', reason: 'needs_referral', model: { used: true, provider: 'gpt5', p: null, ms: 1200 } })
    const [d] = useCall.getState().decisions
    expect(d.reason).toBe('needs_referral')
    expect(d.model).toEqual({ provider: null, p: null, ms: 1200 })
  })

  it('a new call starts with no decisions', () => {
    ingestRaw(OFFER)
    expect(useCall.getState().decisions).toHaveLength(1)
    useCall.getState().begin()
    expect(useCall.getState().decisions).toEqual([])
  })

  it('ignores unknown and malformed events', () => {
    expect(ingestRaw({ type: 'tool_progress', pct: 50 })).toBeNull()
    expect(ingestRaw({ type: 'resolver_decision', say: 'no status' })).toBeNull()
    expect(ingestRaw(null)).toBeNull()
    expect(useCall.getState().decisions).toEqual([])
  })
})

// Response shape from backend/grader.py, values from a live JEV run.
const GRADE_OK = {
  ok: true,
  scores: {
    booked_correctly: { p: 0.88 },
    unnecessary_questions: { p: 0.39 },
    unsupported_claims: { p: 0.61 },
    caller_effort: { level: 1.05, probabilities: { '1': 0.95, '2': 0.05, '3': 0, '4': 0, '5': 0 }, confidence: 0.95 },
    outcome: { choice: 'booked', confidence: 1, probabilities: { booked: 1 } },
  },
  usage: { input_tokens: 847, output_tokens: 137, usd: 0.00003388 },
  ms: 637,
}
const TURNS = [
  { role: 'user' as const, text: 'Cardiology with Dr. Chen, soonest.' },
  { role: 'bot' as const, text: 'Dr. Emily Chen has tomorrow at 8 at Downtown.' },
]

function endedCall() {
  const call = useCall.getState()
  call.begin()
  call.connected()
  ingestRaw(OFFER)
  call.ingest({ type: 'node_entered', node: 'schedule', state: { visit: 'Cardiology Consultation' } })
  call.end()
}

function mockFetch(status: number, body: unknown) {
  let release: () => void = () => {}
  const gate = new Promise<void>((r) => (release = r))
  const fetch = vi.fn(async () => {
    await gate
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', fetch)
  return { fetch, release }
}

describe('grade', () => {
  beforeEach(() => useCall.getState().reset())
  afterEach(() => vi.unstubAllGlobals())

  it('idle -> grading -> done, posting the transcript, decisions and collected data', async () => {
    endedCall()
    const { fetch, release } = mockFetch(200, GRADE_OK)
    expect(useCall.getState().review).toEqual({ status: 'idle' })

    const done = useCall.getState().grade(TURNS, { name: 'Clinic' })
    expect(useCall.getState().review).toEqual({ status: 'grading' })
    release()
    await done

    expect(useCall.getState().review).toEqual({
      status: 'done',
      result: {
        checks: { booked_correctly: 0.88, unnecessary_questions: 0.39, unsupported_claims: 0.61 },
        effort: 1.05,
        outcome: { choice: 'booked', confidence: 1 },
        inputTokens: 847,
        usd: 0.00003388,
        ms: 637,
      },
    })
    expect(fetch).toHaveBeenCalledTimes(1)
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/grade')
    expect(JSON.parse(init.body as string)).toEqual({
      agent: { name: 'Clinic' },
      transcript: TURNS,
      decisions: [{ status: 'offer', say: OFFER.say, offers: OFFER.offers, reason: null }],
      collected: { visit: 'Cardiology Consultation' },
    })
  })

  it('grading -> error with the backend reason', async () => {
    endedCall()
    const { release } = mockFetch(503, { ok: false, reason: 'JEV not configured' })
    const done = useCall.getState().grade(TURNS)
    release()
    await done
    expect(useCall.getState().review).toEqual({ status: 'error', message: 'JEV not configured' })
  })

  it('a malformed grade is an error, not a crash', async () => {
    endedCall()
    const { release } = mockFetch(200, { ok: true, scores: { outcome: { choice: 'teleported' } } })
    const done = useCall.getState().grade(TURNS)
    release()
    await done
    expect(useCall.getState().review).toEqual({ status: 'error', message: 'JEV returned a malformed grade' })
  })

  it('does nothing while the call is live or with an empty transcript', async () => {
    const { fetch } = mockFetch(200, GRADE_OK)
    useCall.getState().begin()
    await useCall.getState().grade(TURNS)
    useCall.getState().end()
    await useCall.getState().grade([])
    expect(fetch).not.toHaveBeenCalled()
    expect(useCall.getState().review).toEqual({ status: 'idle' })
  })

  it('a new call discards an in-flight grade', async () => {
    endedCall()
    const { release } = mockFetch(200, GRADE_OK)
    const done = useCall.getState().grade(TURNS)
    useCall.getState().begin()
    release()
    await done
    expect(useCall.getState().review).toEqual({ status: 'idle' })
  })
})

const NO_KEYS = { openai: null, elevenlabs: null, jev: null }
const BROWSER = { openai: 'sk-test-openai-0000abcd', elevenlabs: 'sk_test_el_0000wxyz', jev: 'sk-test-jev-0000qrst' }

describe('grade and the browser keys', () => {
  beforeEach(() => useCall.getState().reset())
  afterEach(() => {
    vi.unstubAllGlobals()
    useKeys.setState({ browser: NO_KEYS })
  })

  it.each([
    ['all three', BROWSER, { 'Content-Type': 'application/json', 'X-CMD-API-Key': BROWSER.jev }],
    ['no JEV key', { ...BROWSER, jev: null }, { 'Content-Type': 'application/json' }],
    ['none', NO_KEYS, { 'Content-Type': 'application/json' }],
  ])('browser keys: %s -> only the JEV header', async (_case, browser, headers) => {
    useKeys.setState({ browser })
    endedCall()
    const { fetch, release } = mockFetch(200, GRADE_OK)
    const done = useCall.getState().grade(TURNS)
    release()
    await done
    const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(init.headers).toEqual(headers)
    expect(init.body).not.toContain('sk-test')
    expect(init.body).not.toContain('sk_test')
  })
})

describe('startRequestData', () => {
  const agent = (over: Partial<AgentConfig> = {}): AgentConfig => ({
    id: 'clinic',
    name: 'Clinic',
    persona: '',
    voice_id: 'v',
    model: 'gpt-4o',
    initial_node: 'start',
    nodes: [{ name: 'start', task_messages: [], edges: [] }],
    catalog: 'data/catalog.json',
    resolver: { chooser: 'jev' },
    ...over,
  })

  it('carries every browser key as api_keys when the call consults JEV', () => {
    const a = agent()
    const data = startRequestData(a, BROWSER)
    expect(data).toEqual({
      transport: 'webrtc',
      body: {
        agent: a,
        api_keys: { OPENAI_API_KEY: BROWSER.openai, ELEVENLABS_API_KEY: BROWSER.elevenlabs, CMD_API_KEY: BROWSER.jev },
      },
    })
    expect(data.body.agent).not.toBe(a)
  })

  it('an absent chooser is JEV', () => {
    expect(startRequestData(agent({ resolver: {} }), { ...NO_KEYS, jev: 'k1234' }).body.api_keys).toEqual({ CMD_API_KEY: 'k1234' })
  })

  it.each([
    ['OpenAI picked', agent({ resolver: { chooser: 'openai' } })],
    ['Off picked', agent({ resolver: { chooser: 'none' } })],
    ['no catalog, so no resolver', agent({ catalog: undefined })],
  ])('leaves the JEV key out with %s', (_case, a) => {
    expect(startRequestData(a, BROWSER).body.api_keys).toEqual({ OPENAI_API_KEY: BROWSER.openai, ELEVENLABS_API_KEY: BROWSER.elevenlabs })
  })

  it('only the keys this browser holds; no api_keys at all without one', () => {
    expect(startRequestData(agent(), { ...NO_KEYS, elevenlabs: BROWSER.elevenlabs }).body.api_keys).toEqual({
      ELEVENLABS_API_KEY: BROWSER.elevenlabs,
    })
    const data = startRequestData(agent(), NO_KEYS)
    expect(Object.keys(data.body)).toEqual(['agent'])
    expect(JSON.stringify(data)).not.toMatch(/sk[-_]test/)
  })

  it('never sends the old cmd_api_key field', () => {
    expect(JSON.stringify(startRequestData(agent(), BROWSER))).not.toContain('cmd_api_key')
  })
})

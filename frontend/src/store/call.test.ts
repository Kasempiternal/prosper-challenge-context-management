import { beforeEach, describe, expect, it } from 'vitest'
import { parseFlowEvent, useCall } from './call'

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
  jev: { used: true, p: 0.92, ms: 540 },
  tokens: { result: 77 },
}
const ASK = {
  type: 'resolver_decision',
  status: 'ask',
  say: 'Do you mean Dr. David Chen or Dr. Emily Chen?',
  candidates: { field: 'provider', options: ['Dr. David Chen', 'Dr. Emily Chen'] },
  jev: { used: false },
  tokens: { result: 54 },
}

describe('resolver_decision', () => {
  beforeEach(() => useCall.getState().reset())

  it('appends parsed decisions in arrival order', () => {
    useCall.getState().begin()
    ingestRaw(OFFER)
    ingestRaw(ASK)
    ingestRaw({ type: 'resolver_decision', status: 'ask', say: 'Are you a new patient?', candidates: 'is_new', jev: { used: false } })

    const decisions = useCall.getState().decisions.map(({ at: _at, ...d }) => d)
    expect(decisions).toEqual([
      {
        status: 'offer',
        say: OFFER.say,
        offers: OFFER.offers,
        ask: null,
        reason: null,
        jev: { p: 0.92, ms: 540 },
        tokens: 77,
      },
      {
        status: 'ask',
        say: ASK.say,
        offers: [],
        ask: { field: 'provider', options: ['Dr. David Chen', 'Dr. Emily Chen'] },
        reason: null,
        jev: null,
        tokens: 54,
      },
      {
        status: 'ask',
        say: 'Are you a new patient?',
        offers: [],
        ask: { field: 'is_new', options: [] },
        reason: null,
        jev: null,
        tokens: null,
      },
    ])
  })

  it('keeps the refusal reason and a JEV call with no probability', () => {
    ingestRaw({ type: 'resolver_decision', status: 'refuse', say: 'No.', reason: 'needs_referral', jev: { used: true, p: null, ms: 1200 } })
    const [d] = useCall.getState().decisions
    expect(d.reason).toBe('needs_referral')
    expect(d.jev).toEqual({ p: null, ms: 1200 })
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

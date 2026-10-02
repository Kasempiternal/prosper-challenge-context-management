import { describe, expect, it } from 'vitest'
import { effectiveChooser, effectiveTimeoutMs, modeLabel } from './chooser'

describe('chooser defaults mirror the backend schema', () => {
  it.each([
    [undefined, 'jev'],
    [{}, 'jev'],
    [{ jev: { enabled: true } }, 'jev'],
    [{ jev: { enabled: false } }, 'none'],
    [{ chooser: 'openai' as const, jev: { enabled: false } }, 'openai'],
    [{ chooser: 'embed' as const }, 'embed'],
  ])('%j -> %s', (resolver, expected) => {
    expect(effectiveChooser(resolver)).toBe(expected)
  })

  it('takes the generic timeout, then the JEV one, then 2500', () => {
    expect(effectiveTimeoutMs({ timeout_ms: 900, jev: { timeout_ms: 1200 } })).toBe(900)
    expect(effectiveTimeoutMs({ jev: { timeout_ms: 1200 } })).toBe(1200)
    expect(effectiveTimeoutMs(undefined)).toBe(2500)
  })
})

describe('modeLabel', () => {
  it('names what runs, and says so when the backend fell back', () => {
    expect(modeLabel({ requested: 'openai', active: 'openai' })).toBe('OpenAI')
    expect(modeLabel({ requested: 'none', active: 'none' })).toBe('no model')
    expect(modeLabel({ requested: 'jev', active: 'none' })).toBe('no model (JEV unavailable)')
  })
})

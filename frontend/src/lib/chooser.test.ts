import { describe, expect, it } from 'vitest'
import { effectiveChooser, modeLabel } from './chooser'

describe('chooser default mirrors the backend schema', () => {
  it.each([
    [undefined, 'jev'],
    [{}, 'jev'],
    [{ chooser: 'embed' as const }, 'embed'],
    [{ chooser: 'none' as const }, 'none'],
  ])('%j -> %s', (resolver, expected) => {
    expect(effectiveChooser(resolver)).toBe(expected)
  })
})

describe('modeLabel', () => {
  it('names what runs, and says so when the backend fell back', () => {
    expect(modeLabel({ requested: 'openai', active: 'openai' })).toBe('OpenAI')
    expect(modeLabel({ requested: 'none', active: 'none' })).toBe('no model')
    expect(modeLabel({ requested: 'jev', active: 'none' })).toBe('no model (JEV unavailable)')
  })
})

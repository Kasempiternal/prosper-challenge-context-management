import { describe, expect, it } from 'vitest'
import { DEMO_BEATS, DEMO_GROUPS } from './demoScript'

describe('demo script', () => {
  it('has the nine README beats first, then the real-caller calls, numbered in order', () => {
    expect(DEMO_BEATS.map((b) => b.id)).toEqual(DEMO_BEATS.map((_, i) => i + 1))
    expect(DEMO_BEATS.filter((b) => b.group === 'demo').map((b) => b.id)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9])
    expect(DEMO_BEATS.filter((b) => b.group === 'real').length).toBe(6)
  })

  it('gives every beat a known group, an agent that ships, and a full script to the end of the call', () => {
    const groups = DEMO_GROUPS.map((g) => g.key)
    for (const beat of DEMO_BEATS) {
      expect(groups).toContain(beat.group)
      expect(['clinic-scheduler', 'national-scheduler']).toContain(beat.agent)
      expect(beat.steps.length).toBeGreaterThanOrEqual(4)
      expect(beat.failIf?.trim()).toBeTruthy()
      for (const step of beat.steps) {
        expect(step.say.trim()).not.toBe('')
        expect(step.expect.trim()).not.toBe('')
      }
    }
  })
})

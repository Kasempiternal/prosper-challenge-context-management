import { describe, expect, it } from 'vitest'
import { DEMO_BEATS } from './demoScript'

describe('demo script', () => {
  it('has the nine beats of the README, in order', () => {
    expect(DEMO_BEATS.map((b) => b.id)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9])
  })

  it('gives every beat an agent that ships, and lines to say with an expectation', () => {
    for (const beat of DEMO_BEATS) {
      expect(['clinic-scheduler', 'national-scheduler']).toContain(beat.agent)
      expect(beat.steps.length).toBeGreaterThan(0)
      for (const step of beat.steps) {
        expect(step.say.trim()).not.toBe('')
        expect(step.expect.trim()).not.toBe('')
      }
    }
  })
})

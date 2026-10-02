import { describe, expect, it } from 'vitest'
import { checkVerdict } from './review'

describe('checkVerdict', () => {
  it('passes a confident yes on a pass-when-yes check', () => {
    expect(checkVerdict(0.96, true)).toBe('pass')
  })
  it('calls a coin-flip on a pass-when-no check unclear, not a failure', () => {
    expect(checkVerdict(0.56, false)).toBe('unclear')
  })
  it('fails a confident yes on a pass-when-no check', () => {
    expect(checkVerdict(0.9, false)).toBe('fail')
  })
  it('passes a confident no on a pass-when-no check', () => {
    expect(checkVerdict(0.2, false)).toBe('pass')
  })
})

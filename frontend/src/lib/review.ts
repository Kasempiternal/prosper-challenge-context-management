export type Verdict = 'pass' | 'fail' | 'unclear'

// A near coin-flip from the grader is not a verdict; show it as unclear.
export const UNCLEAR_BAND: [number, number] = [0.35, 0.65]

export function checkVerdict(yes: number, passWhenYes: boolean): Verdict {
  const passP = passWhenYes ? yes : 1 - yes
  if (passP >= UNCLEAR_BAND[1]) return 'pass'
  if (passP <= UNCLEAR_BAND[0]) return 'fail'
  return 'unclear'
}

/** POST /api/grade — see backend/grader.py. */

export type GradeOutcome = 'booked' | 'refused_correctly' | 'handed_off' | 'abandoned' | 'unclear'

export interface GradeTurn {
  role: 'user' | 'bot'
  text: string
}

export interface GradeRequest {
  agent?: unknown
  transcript: GradeTurn[]
  decisions: { status: string; say: string; offers: string[]; reason: string | null }[]
  collected: Record<string, unknown>
}

export type CheckName = 'booked_correctly' | 'unnecessary_questions' | 'unsupported_claims'

export interface GradeResult {
  /** Probability JEV answers "yes" to each check question. */
  checks: Record<CheckName, number>
  /** Expected effort on the 1 (very easy) .. 5 (very hard) scale. */
  effort: number
  outcome: { choice: GradeOutcome; confidence: number }
  inputTokens: number
  usd: number
  ms: number
}

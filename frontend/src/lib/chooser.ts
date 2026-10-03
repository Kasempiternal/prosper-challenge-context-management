import type { ResolverConfig } from '../types/agent'

/** The model behind the resolver's ambiguous cases (backend agent_builder/schema.py CHOOSERS). */
export type Chooser = NonNullable<ResolverConfig['chooser']>

export const CHOOSERS: ReadonlyArray<{ value: Chooser; label: string; hint: string }> = [
  { value: 'jev', label: 'JEV', hint: 'Command Code JEV settles close matches.' },
  { value: 'openai', label: 'OpenAI', hint: 'gpt-4o-mini picks from the shortlist by token odds.' },
  { value: 'embed', label: 'Embeddings', hint: 'Local bge-small similarity. No network, no cost.' },
  { value: 'none', label: 'Off', hint: 'No model: anything ambiguous becomes a question.' },
]

export const isChooser = (v: unknown): v is Chooser => CHOOSERS.some((c) => c.value === v)

export const chooserLabel = (c: Chooser) => CHOOSERS.find((x) => x.value === c)?.label ?? c

/** What a call runs: "OpenAI", "no model", or "no model (OpenAI unavailable)" when the backend fell back. */
export function modeLabel(mode: { requested: Chooser; active: Chooser }): string {
  const active = mode.active === 'none' ? 'no model' : chooserLabel(mode.active)
  return mode.active === mode.requested || mode.requested === 'none'
    ? active
    : `${active} (${chooserLabel(mode.requested)} unavailable)`
}

/** The per-turn budget of a networked chooser when resolver.timeout_ms is absent (backend TURN_BUDGET_MS). */
export const DEFAULT_TIMEOUT_MS = 2500

/** Mirrors ResolverConfig.from_dict: an absent chooser is JEV. */
export function effectiveChooser(resolver: ResolverConfig | undefined): Chooser {
  return isChooser(resolver?.chooser) ? resolver.chooser : 'jev'
}

/** A call of this agent consults JEV: it schedules from a catalog and its chooser is JEV. */
export function usesJev(agent: { catalog?: unknown; resolver?: ResolverConfig }): boolean {
  return typeof agent.catalog === 'string' && effectiveChooser(agent.resolver) === 'jev'
}

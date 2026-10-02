import type { AgentConfig, AgentNode, AgentSummary, CatalogSummary, ValidationIssue, Voice } from '../types/agent'
import type { CheckName, GradeOutcome, GradeRequest, GradeResult } from '../types/grade'

export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function parseNode(raw: unknown, index: number): AgentNode {
  if (!isRecord(raw) || typeof raw.name !== 'string') {
    throw new ApiError(0, `Agent node #${index + 1} is malformed`)
  }
  return {
    ...raw,
    name: raw.name,
    task_messages: Array.isArray(raw.task_messages) ? raw.task_messages : [],
    edges: Array.isArray(raw.edges) ? raw.edges : [],
  }
}

export function parseAgent(raw: unknown): AgentConfig {
  if (!isRecord(raw) || typeof raw.id !== 'string' || !Array.isArray(raw.nodes)) {
    throw new ApiError(0, 'Server returned a malformed agent')
  }
  return {
    ...raw,
    id: raw.id,
    name: typeof raw.name === 'string' ? raw.name : raw.id,
    initial_node: typeof raw.initial_node === 'string' ? raw.initial_node : '',
    nodes: raw.nodes.map(parseNode),
    persona: typeof raw.persona === 'string' ? raw.persona : '',
    voice_id: typeof raw.voice_id === 'string' ? raw.voice_id : '',
    model: typeof raw.model === 'string' ? raw.model : '',
  }
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(path, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
  })
  if (!res.ok && res.status !== 422) {
    const detail = await res.text().catch(() => '')
    throw new ApiError(res.status, detail || `${res.status} ${res.statusText}`)
  }
  return res
}

const json = (value: unknown) => JSON.stringify(value)

const OUTCOMES: GradeOutcome[] = ['booked', 'refused_correctly', 'handed_off', 'abandoned', 'unclear']
const CHECKS: CheckName[] = ['booked_correctly', 'unnecessary_questions', 'unsupported_claims']
const malformedGrade = () => new ApiError(0, 'JEV returned a malformed grade')
const prob = (v: unknown): number => {
  if (typeof v !== 'number' || !(v >= 0 && v <= 1)) throw malformedGrade()
  return v
}

export function parseGrade(raw: unknown): GradeResult {
  if (!isRecord(raw) || !isRecord(raw.scores)) throw malformedGrade()
  const scores = raw.scores
  const sub = (k: string) => (isRecord(scores[k]) ? scores[k] : {})
  const outcome = sub('outcome')
  const choice = OUTCOMES.find((o) => o === outcome.choice)
  const effort = sub('caller_effort').level
  if (!choice || typeof effort !== 'number' || !(effort >= 1 && effort <= 5)) throw malformedGrade()
  const usage = isRecord(raw.usage) ? raw.usage : {}
  return {
    checks: Object.fromEntries(CHECKS.map((c) => [c, prob(sub(c).p)])) as Record<CheckName, number>,
    effort,
    outcome: { choice, confidence: prob(outcome.confidence) },
    inputTokens: typeof usage.input_tokens === 'number' ? usage.input_tokens : 0,
    usd: typeof usage.usd === 'number' ? usage.usd : 0,
    ms: typeof raw.ms === 'number' ? raw.ms : 0,
  }
}

const COUNT_KEYS = ['locations', 'providers', 'appointment_types', 'metros'] as const
const isCount = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v) && v >= 0

/** Entries that are not well-formed summaries are dropped rather than failing the whole list. */
export function parseCatalogs(raw: unknown): CatalogSummary[] {
  if (!Array.isArray(raw)) throw new ApiError(0, 'Server returned a malformed catalog list')
  return raw.flatMap((c): CatalogSummary[] => {
    if (!isRecord(c) || typeof c.path !== 'string' || !COUNT_KEYS.every((k) => isCount(c[k]))) return []
    return [
      {
        path: c.path,
        label: typeof c.label === 'string' ? c.label : c.path,
        locations: c.locations as number,
        providers: c.providers as number,
        appointment_types: c.appointment_types as number,
        metros: c.metros as number,
        naive_tokens: isCount(c.naive_tokens) ? c.naive_tokens : null,
      },
    ]
  })
}

export type SaveResult = { ok: true } | { ok: false; errors: ValidationIssue[] }

export const api = {
  async listAgents(): Promise<AgentSummary[]> {
    return (await request('/api/agents')).json()
  },
  async getAgent(id: string): Promise<AgentConfig> {
    return parseAgent(await (await request(`/api/agents/${encodeURIComponent(id)}`)).json())
  },
  async createAgent(name: string): Promise<AgentConfig> {
    const res = await request('/api/agents', { method: 'POST', body: json({ name }) })
    return parseAgent(await res.json())
  },
  async saveAgent(agent: AgentConfig): Promise<SaveResult> {
    const res = await request(`/api/agents/${encodeURIComponent(agent.id)}`, {
      method: 'PUT',
      body: json(agent),
    })
    const body = (await res.json()) as { ok: boolean; errors?: ValidationIssue[] }
    return body.ok ? { ok: true } : { ok: false, errors: body.errors ?? [] }
  },
  async deleteAgent(id: string): Promise<void> {
    await request(`/api/agents/${encodeURIComponent(id)}`, { method: 'DELETE' })
  },
  async validate(agent: AgentConfig, signal?: AbortSignal): Promise<ValidationIssue[]> {
    const res = await fetch('/api/agents/validate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: json(agent),
      signal,
    })
    if (!res.ok) throw new ApiError(res.status, `Validation unavailable (${res.status})`)
    const body = (await res.json()) as { ok: boolean; errors?: ValidationIssue[] }
    return body.errors ?? []
  },
  async listVoices(): Promise<Voice[]> {
    return (await request('/api/voices')).json()
  },
  async listModels(): Promise<string[]> {
    return (await request('/api/models')).json()
  },
  async listCatalogs(): Promise<CatalogSummary[]> {
    return parseCatalogs(await (await request('/api/catalogs')).json())
  },
  /** Errors carry the backend's `reason` (e.g. "JEV not configured") as the message. */
  async grade(body: GradeRequest): Promise<GradeResult> {
    const res = await fetch('/api/grade', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: json(body) })
    const payload: unknown = await res.json().catch(() => null)
    if (!res.ok) {
      const reason = isRecord(payload) && typeof payload.reason === 'string' ? payload.reason : `Grading failed (${res.status})`
      throw new ApiError(res.status, reason)
    }
    return parseGrade(payload)
  },
}

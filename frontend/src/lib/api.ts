import type { AgentConfig, AgentNode, AgentSummary, ValidationIssue, Voice } from '../types/agent'

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
}

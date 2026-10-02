// Mirrors backend/agent_builder/schema.py plus the UI-owned `id` and node `ui`.
// Index signatures are deliberate: Phase 2 adds keys (node `tools`, edge `guard`, ...)
// that this editor must carry through every edit untouched.

export interface TaskMessage {
  role: string
  content: string
  [key: string]: unknown
}

export const FIELD_TYPES = ['string', 'number', 'integer', 'boolean'] as const
export type FieldType = (typeof FIELD_TYPES)[number]

export interface PropertySchema {
  type?: string
  description?: string
  enum?: Array<string | number>
  [key: string]: unknown
}

export interface AgentEdge {
  function: string
  description: string
  target: string
  properties?: Record<string, PropertySchema>
  required?: string[]
  [key: string]: unknown
}

export type ContextStrategy = 'append' | 'reset'

export interface NodePosition {
  x: number
  y: number
}

export interface AgentNode {
  name: string
  task_messages: TaskMessage[]
  role_message?: string | null
  edges?: AgentEdge[]
  pre_actions?: unknown[]
  post_actions?: unknown[]
  end?: boolean
  context_strategy?: ContextStrategy
  /** Scheduling tool names from backend/agent_tools/registry.py. */
  tools?: string[]
  ui?: NodePosition
  [key: string]: unknown
}

export interface AgentConfig {
  id: string
  name: string
  initial_node: string
  nodes: AgentNode[]
  persona: string
  voice_id: string
  model: string
  /** Catalog JSON path relative to backend/; set on scheduling agents. */
  catalog?: string
  resolver?: ResolverConfig
  [key: string]: unknown
}

/** Absent keys mean the backend default (speak_direct true, jev.enabled true, jev.timeout_ms 2500). */
export interface ResolverConfig {
  speak_direct?: boolean
  jev?: { enabled?: boolean; timeout_ms?: number; [key: string]: unknown }
  [key: string]: unknown
}

export interface AgentSummary {
  id: string
  name: string
  node_count: number
  updated_at: string
}

/** GET /api/catalogs entry; counts come from the catalog's sidecar or the catalog itself. */
export interface CatalogSummary {
  /** Relative to backend/, forward slashes; the value of AgentConfig.catalog. */
  path: string
  label: string
  locations: number
  providers: number
  appointment_types: number
  metros: number
  /** Tokens of the whole catalog pasted into a prompt; null when the catalog has no sidecar. */
  naive_tokens: number | null
}

export interface ValidationIssue {
  path: string
  message: string
}

export interface Voice {
  id: string
  name: string
  description: string
}

/** Editor document: `keys[i]` is a stable client-side identity for `agent.nodes[i]`, so renames don't remount canvas nodes. */
export interface Doc {
  agent: AgentConfig
  keys: string[]
}

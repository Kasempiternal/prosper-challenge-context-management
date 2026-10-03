import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AgentSummary } from '../types/agent'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }) }))
vi.mock('../lib/api', () => ({ api: { listAgents: vi.fn(), getAgent: vi.fn() } }))

const { api } = await import('../lib/api')
const { useAgents } = await import('./agents')

const summary = (id: string, catalog: string | null): AgentSummary => ({
  id,
  name: id,
  node_count: 5,
  catalog,
  updated_at: '2026-10-04T00:00:00+00:00',
})

// A fresh clone checks files out alphabetically, so the plain example has the newest file time.
const FRESH_CLONE = [summary('prosper-scheduler', null), summary('national-scheduler', 'data/national/catalog.json')]

describe('boot', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.mocked(api.getAgent).mockReset().mockRejectedValue(new Error('not needed'))
    vi.mocked(api.listAgents).mockReset().mockResolvedValue(FRESH_CLONE)
  })

  it('opens a scheduling agent on a first visit', async () => {
    await useAgents.getState().boot()
    expect(api.getAgent).toHaveBeenCalledWith('national-scheduler')
  })

  it('opens the remembered agent on a later visit', async () => {
    localStorage.setItem('agent-studio:last-agent', 'prosper-scheduler')
    await useAgents.getState().boot()
    expect(api.getAgent).toHaveBeenCalledWith('prosper-scheduler')
  })
})

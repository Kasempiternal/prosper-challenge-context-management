import { create } from 'zustand'
import { toast } from 'sonner'
import { api } from '../lib/api'
import type { AgentSummary } from '../types/agent'
import { isCallActive, useCall } from './call'
import { useEditor } from './editor'

const LAST_AGENT = 'agent-studio:last-agent'

interface AgentsState {
  list: AgentSummary[]
  status: 'loading' | 'ready' | 'error'
  activeId: string | null
  opening: boolean
  /** Agent the user tried to switch to while the current one had unsaved edits. */
  pendingId: string | null

  refresh: () => Promise<AgentSummary[]>
  /** Initial load: list agents and open the last-used (or most recent) one. */
  boot: () => Promise<void>
  requestOpen: (id: string) => void
  open: (id: string) => Promise<void>
  cancelPending: () => void
  create: (name: string) => Promise<void>
  /** Resolves false when the delete was refused (the agent is on a test call). */
  remove: (id: string) => Promise<boolean>
}

export const useAgents = create<AgentsState>()((set, get) => ({
  list: [],
  status: 'loading',
  activeId: null,
  opening: false,
  pendingId: null,

  refresh: async () => {
    try {
      const list = await api.listAgents()
      set({ list, status: 'ready' })
      return list
    } catch {
      set({ status: 'error' })
      return []
    }
  },

  boot: async () => {
    set({ status: 'loading' })
    const list = await get().refresh()
    const last = localStorage.getItem(LAST_AGENT)
    // A first visit opens a scheduling agent: a fresh clone's file times would otherwise put the
    // plain example first.
    const target = list.find((a) => a.id === last) ?? list.find((a) => a.catalog) ?? list[0]
    if (target) await get().open(target.id)
  },

  requestOpen: (id) => {
    if (id === get().activeId) return
    if (isCallActive(useCall.getState().status)) {
      toast('End the test call before switching agents')
      return
    }
    if (useEditor.getState().dirty) set({ pendingId: id })
    else void get().open(id)
  },

  open: async (id) => {
    const previous = get().activeId
    set({ pendingId: null, opening: true, activeId: id })
    try {
      const agent = await api.getAgent(id)
      if (get().activeId !== id) return
      useCall.getState().reset()
      useEditor.getState().load(agent)
      localStorage.setItem(LAST_AGENT, id)
    } catch (err) {
      if (get().activeId === id) set({ activeId: previous })
      toast.error('Could not open agent', { description: err instanceof Error ? err.message : String(err) })
    } finally {
      set({ opening: false })
    }
  },

  cancelPending: () => set({ pendingId: null }),

  create: async (name) => {
    const agent = await api.createAgent(name)
    await get().refresh()
    get().requestOpen(agent.id)
  },

  remove: async (id) => {
    if (id === get().activeId && isCallActive(useCall.getState().status)) {
      toast('End the test call before deleting this agent')
      return false
    }
    await api.deleteAgent(id)
    const list = await get().refresh()
    if (get().activeId !== id) return true
    set({ activeId: null })
    useEditor.getState().close()
    if (list[0]) await get().open(list[0].id)
    return true
  },
}))

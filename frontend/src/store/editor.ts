import { create } from 'zustand'
import { api } from '../lib/api'
import { resolveIssues, type Issue } from '../lib/issues'
import { autoLayout, needsLayout } from '../lib/layout'
import { makeDoc, moveNodes } from '../lib/ops'
import type { AgentConfig, Doc } from '../types/agent'
import { isCallActive, useCall } from './call'

export type Selection =
  | { kind: 'node'; key: string }
  | { kind: 'edge'; key: string; index: number }
  | { kind: 'settings' }
  | null

export type ValidationStatus = 'idle' | 'pending' | 'valid' | 'invalid' | 'offline'
export type RightPanel = 'inspector' | 'call'

const HISTORY_CAP = 100
const COALESCE_MS = 1200

interface ApplyOptions {
  /** Consecutive applies with the same key inside COALESCE_MS collapse into one undo step (typing, nudging). */
  coalesce?: string
  select?: Selection
}

interface EditorState {
  doc: Doc | null
  savedJson: string
  dirty: boolean
  past: Doc[]
  future: Doc[]
  lastApply: { coalesce?: string; at: number } | null
  selection: Selection
  /** Bumped to ask the canvas to pan to a node; nonce makes repeated requests for the same key fire. */
  focus: { key: string; nonce: number } | null
  issues: Issue[]
  validation: ValidationStatus
  saving: boolean
  savedNonce: number
  shakeNonce: number
  rightPanel: RightPanel
  /** The validation issues popover in the top bar (also opened from the test-call panel). */
  issuesOpen: boolean
  /** A JSON tab holds edits that haven't been applied to the doc yet. */
  jsonPending: boolean

  load: (agent: AgentConfig) => void
  close: () => void
  apply: (fn: (doc: Doc) => Doc, opts?: ApplyOptions) => void
  undo: () => void
  redo: () => void
  select: (selection: Selection, opts?: { focus?: boolean }) => void
  setRightPanel: (panel: RightPanel) => void
  setValidation: (status: ValidationStatus, issues?: Issue[]) => void
  setIssuesOpen: (open: boolean) => void
  setJsonPending: (pending: boolean) => void
  save: () => Promise<boolean>
}

const serialize = (doc: Doc | null) => (doc ? JSON.stringify(doc.agent) : '')

function selectionExists(doc: Doc, sel: Selection): Selection {
  if (!sel || sel.kind === 'settings') return sel
  const i = doc.keys.indexOf(sel.key)
  if (i === -1) return null
  if (sel.kind === 'edge' && !doc.agent.nodes[i].edges?.[sel.index]) return null
  return sel
}

/** Whether the floating right panel is showing (mirrors RightPanel's view choice). */
export const selectPanelOpen = (s: Pick<EditorState, 'rightPanel' | 'selection'>) =>
  s.rightPanel === 'call' || s.selection !== null

export const useEditor = create<EditorState>()((set, get) => ({
  doc: null,
  savedJson: '',
  dirty: false,
  past: [],
  future: [],
  lastApply: null,
  selection: null,
  focus: null,
  issues: [],
  validation: 'idle',
  saving: false,
  savedNonce: 0,
  shakeNonce: 0,
  rightPanel: 'inspector',
  issuesOpen: false,
  jsonPending: false,

  load: (agent) => {
    let doc = makeDoc(agent)
    // Agents authored outside the studio have no positions; lay them out once and treat that as the saved baseline.
    if (needsLayout(doc)) doc = moveNodes(doc, autoLayout(doc))
    set({
      doc,
      savedJson: serialize(doc),
      dirty: false,
      past: [],
      future: [],
      lastApply: null,
      selection: null,
      focus: null,
      issues: [],
      validation: 'pending',
      rightPanel: 'inspector',
      issuesOpen: false,
    })
  },

  close: () => set({ doc: null, savedJson: '', dirty: false, past: [], future: [], selection: null, issues: [] }),

  apply: (fn, opts = {}) => {
    const { doc, past, lastApply, savedJson, selection } = get()
    if (!doc) return
    const next = fn(doc)
    if (next === doc) return
    const now = Date.now()
    const coalesced =
      opts.coalesce !== undefined && lastApply?.coalesce === opts.coalesce && now - lastApply.at < COALESCE_MS
    set({
      doc: next,
      past: coalesced ? past : [...past, doc].slice(-HISTORY_CAP),
      future: [],
      lastApply: { coalesce: opts.coalesce, at: now },
      dirty: serialize(next) !== savedJson,
      validation: 'pending',
      selection: selectionExists(next, opts.select !== undefined ? opts.select : selection),
    })
  },

  undo: () => {
    const { doc, past, future, savedJson, selection } = get()
    const prev = past[past.length - 1]
    if (!doc || !prev) return
    set({
      doc: prev,
      past: past.slice(0, -1),
      future: [doc, ...future],
      lastApply: null,
      dirty: serialize(prev) !== savedJson,
      validation: 'pending',
      selection: selectionExists(prev, selection),
    })
  },

  redo: () => {
    const { doc, past, future, savedJson, selection } = get()
    const next = future[0]
    if (!doc || !next) return
    set({
      doc: next,
      past: [...past, doc],
      future: future.slice(1),
      lastApply: null,
      dirty: serialize(next) !== savedJson,
      validation: 'pending',
      selection: selectionExists(next, selection),
    })
  },

  select: (selection, opts) => {
    // During a call the transcript stays docked; the user reopens the inspector deliberately.
    const inCall = isCallActive(useCall.getState().status)
    set({ selection, rightPanel: selection && !inCall ? 'inspector' : get().rightPanel })
    if (opts?.focus && selection && selection.kind !== 'settings') {
      set({ focus: { key: selection.key, nonce: (get().focus?.nonce ?? 0) + 1 } })
    }
  },

  setRightPanel: (rightPanel) => set({ rightPanel }),

  setValidation: (validation, issues) => set(issues ? { validation, issues } : { validation }),
  setIssuesOpen: (issuesOpen) => set({ issuesOpen }),
  setJsonPending: (jsonPending) => set({ jsonPending }),

  save: async () => {
    const { doc, saving } = get()
    if (!doc || saving) return false
    set({ saving: true })
    try {
      const result = await api.saveAgent(doc.agent)
      if (!result.ok) {
        set({
          issues: resolveIssues(doc, result.errors),
          validation: 'invalid',
          shakeNonce: get().shakeNonce + 1,
        })
        return false
      }
      const current = get().doc
      const savedJson = serialize(doc)
      set({ savedJson, dirty: serialize(current) !== savedJson, savedNonce: get().savedNonce + 1 })
      return true
    } finally {
      set({ saving: false })
    }
  },
}))

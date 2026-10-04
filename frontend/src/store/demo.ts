import { create } from 'zustand'

const OPEN_KEY = 'agent-studio:demo-open'
const DONE_KEY = 'agent-studio:demo-done'

function loadDone(): number[] {
  try {
    const raw = JSON.parse(localStorage.getItem(DONE_KEY) ?? '[]')
    return Array.isArray(raw) ? raw.filter((n): n is number => typeof n === 'number') : []
  } catch {
    return []
  }
}

interface DemoState {
  open: boolean
  /** Beat ids ticked off in this browser. */
  done: number[]
  toggleOpen: () => void
  close: () => void
  toggleDone: (id: number) => void
  reset: () => void
}

export const useDemo = create<DemoState>()((set, get) => ({
  open: localStorage.getItem(OPEN_KEY) === '1',
  done: loadDone(),
  toggleOpen: () => {
    const open = !get().open
    localStorage.setItem(OPEN_KEY, open ? '1' : '0')
    set({ open })
  },
  close: () => {
    localStorage.setItem(OPEN_KEY, '0')
    set({ open: false })
  },
  toggleDone: (id) => {
    const done = get().done.includes(id) ? get().done.filter((n) => n !== id) : [...get().done, id]
    localStorage.setItem(DONE_KEY, JSON.stringify(done))
    set({ done })
  },
  reset: () => {
    localStorage.setItem(DONE_KEY, '[]')
    set({ done: [] })
  },
}))

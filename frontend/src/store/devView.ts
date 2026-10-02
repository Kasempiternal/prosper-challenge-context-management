import { create } from 'zustand'

const STORAGE_KEY = 'agent-studio:dev-view'

interface DevViewState {
  on: boolean
  toggle: () => void
}

export const useDevView = create<DevViewState>()((set, get) => ({
  on: localStorage.getItem(STORAGE_KEY) === '1',
  toggle: () => {
    const on = !get().on
    localStorage.setItem(STORAGE_KEY, on ? '1' : '0')
    set({ on })
  },
}))

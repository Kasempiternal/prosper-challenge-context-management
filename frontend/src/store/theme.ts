import { create } from 'zustand'

export type ThemePreference = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

// Keep in sync with the pre-paint script in index.html.
const STORAGE_KEY = 'agent-studio:theme'
const META_COLOR: Record<ResolvedTheme, string> = { light: '#f9f4ec', dark: '#12100e' }
const SWITCH_MS = 250

const media = window.matchMedia('(prefers-color-scheme: dark)')

function readPreference(): ThemePreference {
  const stored = localStorage.getItem(STORAGE_KEY)
  return stored === 'light' || stored === 'dark' ? stored : 'system'
}

function resolve(preference: ThemePreference): ResolvedTheme {
  if (preference === 'system') return media.matches ? 'dark' : 'light'
  return preference
}

let transitionTimer: number | undefined

function paint(theme: ResolvedTheme, animate: boolean) {
  const root = document.documentElement
  if (root.classList.contains('dark') === (theme === 'dark')) return
  if (animate) {
    root.classList.add('theme-transition')
    window.clearTimeout(transitionTimer)
    transitionTimer = window.setTimeout(() => root.classList.remove('theme-transition'), SWITCH_MS)
  }
  root.classList.toggle('dark', theme === 'dark')
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', META_COLOR[theme])
}

interface ThemeState {
  preference: ThemePreference
  resolved: ResolvedTheme
  setPreference: (preference: ThemePreference) => void
}

export const useTheme = create<ThemeState>()((set) => {
  const preference = readPreference()
  const resolved = resolve(preference)
  paint(resolved, false)

  media.addEventListener('change', () => {
    const { preference } = useTheme.getState()
    if (preference !== 'system') return
    const next = resolve('system')
    paint(next, true)
    set({ resolved: next })
  })

  return {
    preference,
    resolved,
    setPreference: (next) => {
      if (next === 'system') localStorage.removeItem(STORAGE_KEY)
      else localStorage.setItem(STORAGE_KEY, next)
      const theme = resolve(next)
      paint(theme, true)
      set({ preference: next, resolved: theme })
    },
  }
})

import { useEffect } from 'react'
import { deleteSelection, saveCurrent } from '../lib/actions'
import { useDevView } from '../store/devView'
import { useEditor } from '../store/editor'

export const SHORTCUTS: ReadonlyArray<{ keys: string[]; label: string }> = [
  { keys: ['Ctrl', 'S'], label: 'Save' },
  { keys: ['Ctrl', 'Z'], label: 'Undo' },
  { keys: ['Ctrl', 'Shift', 'Z'], label: 'Redo' },
  { keys: ['Del'], label: 'Delete selection' },
  { keys: ['Esc'], label: 'Clear selection' },
  { keys: ['Double-click'], label: 'Add node on canvas' },
  { keys: ['D'], label: 'Toggle dev view' },
  { keys: ['?'], label: 'Show shortcuts' },
]

function isEditable(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  return !!el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName))
}

export function useShortcuts(onHelp: () => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey
      const key = e.key.toLowerCase()
      if (mod && key === 's') {
        e.preventDefault()
        void saveCurrent()
        return
      }
      // Inside text fields, leave Ctrl+Z / Delete to the browser's native editing.
      if (isEditable(e.target)) return
      const editor = useEditor.getState()
      if (mod && (key === 'y' || (key === 'z' && e.shiftKey))) {
        e.preventDefault()
        editor.redo()
      } else if (mod && key === 'z') {
        e.preventDefault()
        editor.undo()
      } else if (key === 'delete' || key === 'backspace') {
        e.preventDefault()
        deleteSelection()
      } else if (key === 'escape') {
        editor.select(null)
      } else if (key === 'd' && !mod && !e.altKey) {
        useDevView.getState().toggle()
      } else if (e.key === '?') {
        onHelp()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onHelp])
}

import { useEffect } from 'react'
import { api } from '../lib/api'
import { resolveIssues } from '../lib/issues'
import { useEditor } from '../store/editor'

const DEBOUNCE_MS = 400

/** Re-validates the draft against the backend 400ms after the last edit; stale responses are dropped. */
export function useLiveValidation() {
  const doc = useEditor((s) => s.doc)

  useEffect(() => {
    if (!doc) return
    const controller = new AbortController()
    const timer = window.setTimeout(async () => {
      try {
        const errors = await api.validate(doc.agent, controller.signal)
        useEditor.getState().setValidation(errors.length ? 'invalid' : 'valid', resolveIssues(doc, errors))
      } catch {
        if (!controller.signal.aborted) useEditor.getState().setValidation('offline')
      }
    }, DEBOUNCE_MS)
    return () => {
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [doc])
}

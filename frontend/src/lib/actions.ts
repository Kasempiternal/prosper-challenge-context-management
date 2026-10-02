import { toast } from 'sonner'
import { useAgents } from '../store/agents'
import { useEditor } from '../store/editor'
import { deleteEdge, deleteNode, nodeByKey } from './ops'

export async function saveCurrent(): Promise<void> {
  const editor = useEditor.getState()
  if (!editor.doc || editor.saving) return
  if (editor.jsonPending) {
    toast('Apply or discard JSON changes first', { description: 'The Advanced (JSON) tab has edits that aren’t in the draft yet.' })
    return
  }
  try {
    const ok = await editor.save()
    if (ok) {
      void useAgents.getState().refresh()
      return
    }
    const count = useEditor.getState().issues.length
    toast.error(`Fix ${count} ${count === 1 ? 'issue' : 'issues'} before saving`, {
      description: 'Click the validation chip to jump to each one.',
    })
  } catch (err) {
    toast.error('Save failed', { description: err instanceof Error ? err.message : String(err) })
  }
}

export function deleteSelection(): void {
  const { selection, doc, apply } = useEditor.getState()
  if (!doc || !selection || selection.kind === 'settings') return
  if (selection.kind === 'node') {
    const name = nodeByKey(doc, selection.key)?.name
    apply((d) => deleteNode(d, selection.key), { select: null })
    toast(`Deleted node “${name}”`, { action: { label: 'Undo', onClick: () => useEditor.getState().undo() } })
  } else {
    apply((d) => deleteEdge(d, selection.key, selection.index), { select: null })
  }
}

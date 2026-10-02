import { useState } from 'react'
import { toast } from 'sonner'
import { useAgents } from '../../store/agents'
import { Button } from '../ui/Button'
import { Field, Input } from '../ui/Field'
import { Modal } from '../ui/Modal'

export function NewAgentModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const create = useAgents((s) => s.create)

  const submit = async () => {
    if (!name.trim() || busy) return
    setBusy(true)
    try {
      await create(name.trim())
      setName('')
      onClose()
    } catch (err) {
      toast.error('Could not create agent', { description: err instanceof Error ? err.message : String(err) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New agent"
      description="Starts with a single greeting node. You can shape the flow on the canvas."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={submit} disabled={!name.trim() || busy}>
            {busy ? 'Creating…' : 'Create agent'}
          </Button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <Field label="Name" htmlFor="new-agent-name">
          <Input
            id="new-agent-name"
            value={name}
            placeholder="e.g. Refill line"
            autoComplete="off"
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
      </form>
    </Modal>
  )
}

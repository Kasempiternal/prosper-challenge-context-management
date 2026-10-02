import { useEditor } from '../../store/editor'

export function AgentNameInput() {
  const name = useEditor((s) => s.doc?.agent.name ?? '')
  const apply = useEditor((s) => s.apply)

  return (
    <label className="group relative grid min-w-0 max-w-[340px] items-center">
      <span className="sr-only">Agent name</span>
      {/* Invisible twin sizes the grid cell to the text so the input hugs its content. */}
      <span aria-hidden className="invisible col-start-1 row-start-1 truncate px-2 text-[15px] font-semibold whitespace-pre">
        {name || 'Untitled agent'}
      </span>
      <input
        value={name}
        placeholder="Untitled agent"
        spellCheck={false}
        size={1}
        onChange={(e) => {
          const value = e.target.value
          apply((d) => ({ ...d, agent: { ...d.agent, name: value } }), { coalesce: 'agent-name' })
        }}
        onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
        className="col-start-1 row-start-1 h-8 w-full min-w-[60px] rounded-[8px] bg-transparent px-2 text-[15px] font-semibold tracking-[-0.01em] outline-none transition-colors duration-200 hover:bg-hover focus:bg-card focus:shadow-[0_0_0_1.5px_var(--color-accent)]"
      />
    </label>
  )
}

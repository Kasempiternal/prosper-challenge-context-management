import { AnimatePresence, motion } from 'motion/react'
import { Copy, Flag, Play, Trash2 } from 'lucide-react'
import { useEffect, useRef, type ReactNode } from 'react'
import { deleteSelection } from '../../lib/actions'
import { softSpring } from '../../lib/motion'
import { duplicateNode, nodeByKey, setStart, toggleEnd } from '../../lib/ops'
import { useEditor } from '../../store/editor'

export interface MenuState {
  key: string
  x: number
  y: number
}

export function NodeContextMenu({ menu, onClose }: { menu: MenuState | null; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const node = useEditor((s) => (menu && s.doc ? nodeByKey(s.doc, menu.key) : undefined))
  const isStart = useEditor((s) => !!node && s.doc?.agent.initial_node === node.name)

  useEffect(() => {
    if (!menu) return
    const onDown = (e: PointerEvent) => !ref.current?.contains(e.target as Node) && onClose()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('pointerdown', onDown)
    window.addEventListener('keydown', onKey)
    ref.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus()
    return () => {
      window.removeEventListener('pointerdown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [menu, onClose])

  const run = (fn: () => void) => () => {
    fn()
    onClose()
  }
  const apply = useEditor.getState().apply

  return (
    <AnimatePresence>
      {menu && node && (
        <motion.div
          ref={ref}
          role="menu"
          aria-label={`Actions for ${node.name}`}
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.97 }}
          transition={softSpring}
          style={{ left: menu.x, top: menu.y, transformOrigin: 'top left' }}
          className="absolute z-30 w-[200px] rounded-[12px] border border-border-subtle bg-card p-1 shadow-panel"
        >
          <Item icon={<Play className="size-3.5" />} disabled={isStart} onClick={run(() => apply((d) => setStart(d, menu.key)))}>
            Set as start
          </Item>
          <Item icon={<Flag className="size-3.5" />} onClick={run(() => apply((d) => toggleEnd(d, menu.key)))}>
            {node.end ? 'Unmark as end' : 'Mark as end'}
          </Item>
          <Item
            icon={<Copy className="size-3.5" />}
            onClick={run(() => {
              const current = useEditor.getState().doc
              if (!current) return
              const { doc, key } = duplicateNode(current, menu.key)
              apply(() => doc, { select: { kind: 'node', key } })
            })}
          >
            Duplicate
          </Item>
          <div className="my-1 h-px bg-border-subtle" />
          <Item icon={<Trash2 className="size-3.5" />} danger onClick={run(deleteSelection)}>
            Delete
          </Item>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

function Item({
  icon,
  children,
  onClick,
  disabled,
  danger,
}: {
  icon: ReactNode
  children: ReactNode
  onClick: () => void
  disabled?: boolean
  danger?: boolean
}) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      onClick={onClick}
      className={`flex w-full items-center gap-2.5 rounded-[8px] px-2.5 py-1.5 text-left text-[13px] transition-colors duration-150 outline-none disabled:opacity-40 ${
        danger ? 'text-danger hover:bg-danger-soft focus-visible:bg-danger-soft' : 'text-ink hover:bg-raised focus-visible:bg-raised'
      }`}
    >
      <span className={danger ? 'text-danger' : 'text-muted'}>{icon}</span>
      {children}
    </button>
  )
}

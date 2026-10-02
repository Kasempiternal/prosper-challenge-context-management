import { AnimatePresence, motion } from 'motion/react'
import { softSpring } from '../../lib/motion'
import { useEditor } from '../../store/editor'
import { TestCallPanel } from '../call/TestCallPanel'
import { AgentSettings } from './AgentSettings'
import { EdgeInspector } from './EdgeInspector'
import { NodeInspector } from './NodeInspector'

export function RightPanel() {
  const panel = useEditor((s) => s.rightPanel)
  const selection = useEditor((s) => s.selection)

  const view =
    panel === 'call'
      ? { id: 'call', el: <TestCallPanel /> }
      : selection?.kind === 'node'
        ? { id: `node:${selection.key}`, el: <NodeInspector nodeKey={selection.key} /> }
        : selection?.kind === 'edge'
          ? { id: `edge:${selection.key}:${selection.index}`, el: <EdgeInspector nodeKey={selection.key} index={selection.index} /> }
          : selection?.kind === 'settings'
            ? { id: 'settings', el: <AgentSettings /> }
            : null

  return (
    <AnimatePresence mode="wait">
      {view && (
        <motion.aside
          key={panel === 'call' ? 'call' : 'inspector'}
          initial={{ opacity: 0, x: 24 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: 24 }}
          transition={softSpring}
          className="absolute top-3 right-3 bottom-3 z-20 flex w-[384px] flex-col overflow-hidden rounded-[20px] border border-border-subtle bg-card shadow-panel"
        >
          <motion.div
            key={view.id}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.18 }}
            className="flex min-h-0 flex-1 flex-col"
          >
            {view.el}
          </motion.div>
        </motion.aside>
      )}
    </AnimatePresence>
  )
}

import { useReactFlow } from '@xyflow/react'
import { useReducedMotion } from 'motion/react'
import { useCallback } from 'react'
import { autoLayout, fitViewOptions } from '../lib/layout'
import { moveNodes } from '../lib/ops'
import { selectPanelOpen, useEditor } from '../store/editor'

export function useAutoLayout() {
  const rf = useReactFlow()
  const reduceMotion = useReducedMotion()
  return useCallback(() => {
    const { doc, apply } = useEditor.getState()
    if (!doc) return
    const heights: Record<string, number> = {}
    for (const n of rf.getNodes()) if (n.measured?.height) heights[n.id] = n.measured.height
    apply((d) => moveNodes(d, autoLayout(d, heights)))
    const fit = fitViewOptions(selectPanelOpen(useEditor.getState()))
    requestAnimationFrame(() => void rf.fitView({ ...fit, duration: reduceMotion ? 0 : 450 }))
  }, [rf, reduceMotion])
}

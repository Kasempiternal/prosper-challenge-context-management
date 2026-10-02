import dagre from '@dagrejs/dagre'
import type { Doc, NodePosition } from '../types/agent'

export const NODE_WIDTH = 280
const FALLBACK_HEIGHT = 150

/** Legible zoom range for whole-graph views; the top padding keeps the floating "Add node" button off the graph. */
export const FIT_VIEW = { padding: { top: '88px', bottom: '80px', x: '80px' }, minZoom: 0.6, maxZoom: 1 } as const

/** Canvas width covered by the floating right panel: 384px wide plus a 12px gap on each side. */
export const PANEL_INSET = 384 + 24

/** Whole-graph view for the narrower canvas area left of the open right panel. */
export const PANEL_FIT_VIEW = {
  padding: { top: '88px', bottom: '80px', left: '40px', right: `${PANEL_INSET + 32}px` },
  minZoom: 0.5,
  maxZoom: 1,
} as const

export const fitViewOptions = (panelOpen: boolean) => (panelOpen ? PANEL_FIT_VIEW : FIT_VIEW)

/** Left-to-right layered layout. `heights` are measured card heights keyed by node key. */
export function autoLayout(doc: Doc, heights: Record<string, number> = {}): Record<string, NodePosition> {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 64, ranksep: 160, marginx: 0, marginy: 0 })
  g.setDefaultEdgeLabel(() => ({}))

  const keyOf = new Map(doc.agent.nodes.map((n, i) => [n.name, doc.keys[i]]))
  doc.agent.nodes.forEach((_, i) => {
    const key = doc.keys[i]
    g.setNode(key, { width: NODE_WIDTH, height: heights[key] ?? FALLBACK_HEIGHT })
  })
  doc.agent.nodes.forEach((n, i) => {
    for (const e of n.edges ?? []) {
      const target = keyOf.get(e.target)
      if (target && target !== doc.keys[i]) g.setEdge(doc.keys[i], target)
    }
  })
  dagre.layout(g)

  const out: Record<string, NodePosition> = {}
  for (const key of doc.keys) {
    const { x, y, width, height } = g.node(key)
    out[key] = { x: x - width / 2, y: y - height / 2 }
  }
  return out
}

export function needsLayout(doc: Doc): boolean {
  return doc.agent.nodes.some((n) => !n.ui || typeof n.ui.x !== 'number' || typeof n.ui.y !== 'number')
}

import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  useReactFlow,
  type Connection,
  type NodeChange,
  type OnNodeDrag,
} from '@xyflow/react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { Plus } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent } from 'react'
import { issuesForNode } from '../../lib/issues'
import { FIT_VIEW, NODE_WIDTH, PANEL_FIT_VIEW, PANEL_INSET, fitViewOptions } from '../../lib/layout'
import { spring } from '../../lib/motion'
import { addEdge, addNode, edgeId, keyByName, moveNodes, parseEdgeId } from '../../lib/ops'
import { isCallActive, useCall } from '../../store/call'
import { useDevView } from '../../store/devView'
import { useTheme } from '../../store/theme'
import { selectPanelOpen, useEditor } from '../../store/editor'
import { PipelineStrip } from '../dev/PipelineStrip'
import { AgentNodeCard } from './AgentNodeCard'
import { FunctionEdge } from './FunctionEdge'
import { NodeContextMenu, type MenuState } from './NodeContextMenu'
import type { AgentFlowNode, CallMark, FunctionFlowEdge } from './types'

type Transient = Partial<Pick<AgentFlowNode, 'position' | 'measured' | 'dragging'>>

const nodeTypes = { agent: AgentNodeCard }
const edgeTypes = { function: FunctionEdge }
// Margins of the canvas area a revealed node must sit inside; the top one clears the "Add node" button.
const SAFE = { top: 72, right: 32, bottom: 40, left: 40 }
const MARKER_IDLE = { type: MarkerType.ArrowClosed, width: 16, height: 16, color: 'var(--color-edge)' }
const MARKER_HOT = { type: MarkerType.ArrowClosed, width: 16, height: 16, color: 'var(--color-accent)' }

export function Canvas() {
  const rf = useReactFlow<AgentFlowNode, FunctionFlowEdge>()
  const doc = useEditor((s) => s.doc)
  const selection = useEditor((s) => s.selection)
  const issues = useEditor((s) => s.issues)
  const focus = useEditor((s) => s.focus)
  const activeNode = useCall((s) => s.activeNode)
  const visited = useCall((s) => s.visited)
  const takenEdge = useCall((s) => s.takenEdge)
  const inCall = useCall((s) => isCallActive(s.status))
  const panelOpen = useEditor(selectPanelOpen)
  const colorMode = useTheme((s) => s.resolved)
  const devView = useDevView((s) => s.on)
  const reduceMotion = useReducedMotion()
  const [transient, setTransient] = useState<Record<string, Transient>>({})
  const [menu, setMenu] = useState<MenuState | null>(null)
  const wrapper = useRef<HTMLDivElement>(null)

  // The doc owns node content and resting positions; React Flow's in-flight drag positions
  // and measured sizes live in `transient` until a drag commits.
  const nodes = useMemo<AgentFlowNode[]>(() => {
    if (!doc) return []
    return doc.agent.nodes.map((node, i) => {
      const id = doc.keys[i]
      const t = transient[id]
      const call: CallMark = activeNode === node.name ? 'active' : visited.includes(node.name) ? 'visited' : null
      const isStart = doc.agent.initial_node === node.name
      const issueCount = issuesForNode(issues, id).length
      return {
        id,
        type: 'agent',
        ariaLabel: nodeAriaLabel(node.name, isStart, !!node.end, issueCount),
        position: t?.position ?? node.ui ?? { x: 0, y: 0 },
        measured: t?.measured,
        dragging: t?.dragging,
        selected: selection?.kind === 'node' && selection.key === id,
        data: {
          node,
          isStart,
          issueCount,
          call,
          inCall,
        },
      }
    })
  }, [doc, transient, selection, issues, activeNode, visited, inCall])

  const edges = useMemo<FunctionFlowEdge[]>(() => {
    if (!doc) return []
    const invalidEdges = new Set(
      issues.flatMap((i) => (i.target.kind === 'edge' ? [edgeId(i.target.key, i.target.index)] : [])),
    )
    const pairs = new Map<string, number>()
    const lanes = doc.agent.nodes.map((node, i) =>
      (node.edges ?? []).map((edge) => {
        const target = keyByName(doc, edge.target)
        if (!target) return 0
        const pair = pairKey(doc.keys[i], target)
        const index = pairs.get(pair) ?? 0
        pairs.set(pair, index + 1)
        return index
      }),
    )
    return doc.agent.nodes.flatMap((node, i) =>
      (node.edges ?? []).flatMap((edge, index): FunctionFlowEdge[] => {
        const target = keyByName(doc, edge.target)
        if (!target) return []
        const id = edgeId(doc.keys[i], index)
        const selected = selection?.kind === 'edge' && edgeId(selection.key, selection.index) === id
        const taken =
          takenEdge && takenEdge.from === node.name && takenEdge.function === edge.function ? takenEdge.nonce : null
        return [
          {
            id,
            type: 'function',
            source: doc.keys[i],
            target,
            selected,
            ariaLabel: `${edge.function || 'unnamed'}: ${node.name || 'unnamed'} → ${edge.target || 'unnamed'}`,
            markerEnd: selected || taken ? MARKER_HOT : MARKER_IDLE,
            data: {
              fn: edge.function,
              invalid: invalidEdges.has(id),
              inCall,
              takenNonce: taken,
              lane: { index: lanes[i][index], count: pairs.get(pairKey(doc.keys[i], target))! },
            },
          },
        ]
      }),
    )
  }, [doc, selection, issues, takenEdge, inCall])

  // Pans so `ids` sit centered in the canvas area the right panel leaves visible, but only when
  // they are not already fully inside it. `focusZoom` forces a centered pan at that zoom.
  const reveal = useCallback(
    (ids: string[], focusZoom?: number) => {
      const box = wrapper.current?.getBoundingClientRect()
      if (!box || ids.length === 0) return
      const b = rf.getNodesBounds(ids)
      const inset = selectPanelOpen(useEditor.getState()) ? PANEL_INSET : 0
      const area = {
        x: SAFE.left,
        y: SAFE.top,
        w: box.width - inset - SAFE.left - SAFE.right,
        h: box.height - SAFE.top - SAFE.bottom,
      }
      const vp = rf.getViewport()
      const left = b.x * vp.zoom + vp.x
      const top = b.y * vp.zoom + vp.y
      const inside =
        left >= area.x &&
        top >= area.y &&
        left + b.width * vp.zoom <= area.x + area.w &&
        top + b.height * vp.zoom <= area.y + area.h
      if (inside && focusZoom === undefined) return
      const fits = Math.min(area.w / b.width, area.h / b.height)
      const zoom = focusZoom ?? Math.max(Math.min(vp.zoom, fits), Math.min(vp.zoom, PANEL_FIT_VIEW.minZoom))
      void rf.setViewport(
        {
          x: area.x + area.w / 2 - (b.x + b.width / 2) * zoom,
          y: area.y + area.h / 2 - (b.y + b.height / 2) * zoom,
          zoom,
        },
        { duration: reduceMotion ? 0 : 450 },
      )
    },
    [rf, reduceMotion],
  )

  const selectionKey =
    selection?.kind === 'node'
      ? `node:${selection.key}`
      : selection?.kind === 'edge'
        ? edgeId(selection.key, selection.index)
        : null

  // What the panel is about: the selection, else the live node, else the whole graph.
  const revealTargets = useCallback((): string[] => {
    const { selection: sel, doc: current } = useEditor.getState()
    if (sel?.kind === 'node') return [sel.key]
    if (sel?.kind === 'edge') {
      const e = rf.getEdge(edgeId(sel.key, sel.index))
      return e ? [e.source, e.target] : [sel.key]
    }
    const live = useCall.getState().activeNode
    const liveKey = live && current ? keyByName(current, live) : undefined
    return liveKey ? [liveKey] : rf.getNodes().map((n) => n.id)
  }, [rf])

  useEffect(() => {
    if (panelOpen) reveal(revealTargets())
  }, [selectionKey, panelOpen, reveal, revealTargets])

  useEffect(() => {
    const current = useEditor.getState().doc
    const key = activeNode && current ? keyByName(current, activeNode) : undefined
    if (key) reveal([key])
  }, [activeNode, reveal])

  useEffect(() => {
    if (focus) reveal([focus.key], Math.max(rf.getZoom(), 0.85))
  }, [focus, reveal, rf])

  const agentId = doc?.agent.id
  useEffect(() => {
    if (agentId) requestAnimationFrame(() => void rf.fitView(FIT_VIEW))
  }, [agentId, rf])

  const onNodesChange = useCallback((changes: NodeChange<AgentFlowNode>[]) => {
    setTransient((prev) => {
      const next = { ...prev }
      for (const c of changes) {
        if (c.type === 'dimensions' && c.dimensions) next[c.id] = { ...next[c.id], measured: c.dimensions }
        if (c.type === 'position' && c.position) next[c.id] = { ...next[c.id], position: c.position, dragging: c.dragging }
      }
      return next
    })
  }, [])

  const onNodeDragStop: OnNodeDrag<AgentFlowNode> = useCallback((_, __, dragged) => {
    const positions = Object.fromEntries(dragged.map((n) => [n.id, n.position]))
    useEditor.getState().apply((d) => moveNodes(d, positions))
    setTransient((prev) => {
      const next = { ...prev }
      for (const n of dragged) next[n.id] = { measured: next[n.id]?.measured }
      return next
    })
  }, [])

  const onConnect = useCallback((c: Connection) => {
    const { apply, doc: current } = useEditor.getState()
    if (!current) return
    const { doc: next, index } = addEdge(current, c.source, c.target)
    if (index === -1) return
    apply(() => next, { select: { kind: 'edge', key: c.source, index } })
  }, [])

  const createNodeAt = useCallback(
    (clientX: number, clientY: number) => {
      const p = rf.screenToFlowPosition({ x: clientX, y: clientY })
      const { apply, doc: current } = useEditor.getState()
      if (!current) return
      const { doc: next, key } = addNode(current, { x: p.x - NODE_WIDTH / 2, y: p.y - 40 })
      apply(() => next, { select: { kind: 'node', key } })
    },
    [rf],
  )

  const onDoubleClick = (e: MouseEvent) => {
    if ((e.target as HTMLElement).classList.contains('react-flow__pane')) createNodeAt(e.clientX, e.clientY)
  }

  const addAtCenter = () => {
    const box = wrapper.current?.getBoundingClientRect()
    if (box) createNodeAt(box.left + box.width / 2, box.top + box.height / 2)
  }

  if (!doc) return null

  return (
    <div
      ref={wrapper}
      className="relative h-full w-full"
      style={{ '--panel-inset': panelOpen ? `${PANEL_INSET}px` : '0px' } as CSSProperties}
      onDoubleClick={onDoubleClick}
    >
      <ReactFlow<AgentFlowNode, FunctionFlowEdge>
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onNodeDragStop={onNodeDragStop}
        onConnect={onConnect}
        isValidConnection={(c) => c.source !== c.target}
        onNodeClick={(_, n) => useEditor.getState().select({ kind: 'node', key: n.id })}
        onEdgeClick={(_, e) => useEditor.getState().select({ kind: 'edge', ...parseEdgeId(e.id) })}
        onPaneClick={() => {
          setMenu(null)
          useEditor.getState().select(null)
        }}
        onNodeContextMenu={(e, n) => {
          e.preventDefault()
          const box = wrapper.current!.getBoundingClientRect()
          useEditor.getState().select({ kind: 'node', key: n.id })
          setMenu({ key: n.id, x: e.clientX - box.left, y: e.clientY - box.top })
        }}
        onMoveStart={() => setMenu(null)}
        deleteKeyCode={null}
        zoomOnDoubleClick={false}
        colorMode={colorMode}
        minZoom={0.25}
        maxZoom={1.75}
        connectionLineStyle={{ strokeWidth: 1.5 }}
        defaultEdgeOptions={{ type: 'function' }}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.4} color="var(--color-dots)" />
        <MiniMap
          position="bottom-left"
          pannable
          zoomable
          nodeBorderRadius={6}
          nodeColor={(n) => ((n.data as { call?: CallMark }).call === 'active' ? 'var(--color-accent)' : 'var(--color-minimap-node)')}
          className="!m-4"
          style={{ width: 168, height: 108 }}
        />
        <Controls
          position="bottom-left"
          showInteractive={false}
          fitViewOptions={fitViewOptions(panelOpen)}
          className="!m-4 !mb-[136px]"
        />
      </ReactFlow>

      <motion.div
        className="absolute top-4 left-4 z-10"
        initial={{ opacity: 0, y: -6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={spring}
      >
        <motion.button
          type="button"
          whileTap={{ scale: 0.96 }}
          whileHover={{ y: -1 }}
          transition={spring}
          onClick={addAtCenter}
          className="flex h-9 items-center gap-1.5 rounded-full border border-border bg-card pr-4 pl-3 text-[13px] font-medium text-ink shadow-card transition-shadow duration-200 hover:shadow-lift"
        >
          <Plus className="size-4 text-accent" strokeWidth={2.4} />
          Add node
        </motion.button>
      </motion.div>

      <AnimatePresence>{devView && <PipelineStrip />}</AnimatePresence>

      <NodeContextMenu menu={menu} onClose={() => setMenu(null)} />
    </div>
  )
}

const pairKey = (a: string, b: string) => (a < b ? `${a}|${b}` : `${b}|${a}`)

function nodeAriaLabel(name: string, isStart: boolean, isEnd: boolean, issueCount: number) {
  const parts = [`Node ${name || 'unnamed'}`]
  if (isStart) parts.push('start')
  if (isEnd) parts.push('end')
  if (issueCount > 0) parts.push(`${issueCount} ${issueCount === 1 ? 'issue' : 'issues'}`)
  return parts.join(', ')
}

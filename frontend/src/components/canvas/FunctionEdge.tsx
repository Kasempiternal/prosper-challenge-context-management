import { EdgeLabelRenderer, useInternalNode, type EdgeProps, type InternalNode } from '@xyflow/react'
import { motion, useReducedMotion } from 'motion/react'
import { memo } from 'react'
import { cn } from '../../lib/cn'
import { routeEdge, type Box } from '../../lib/edgeRoute'
import { EASE_OUT } from '../../lib/motion'
import { useEditor } from '../../store/editor'
import { parseEdgeId } from '../../lib/ops'
import type { FunctionFlowEdge } from './types'

function FunctionEdgeImpl({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  source,
  target,
  selected,
  markerEnd,
  data,
}: EdgeProps<FunctionFlowEdge>) {
  const reduce = useReducedMotion()
  const sourceNode = useInternalNode(source)
  const targetNode = useInternalNode(target)
  const { path, labelX, labelY } = routeEdge(
    { x: sourceX, y: sourceY },
    { x: targetX, y: targetY },
    boxOf(sourceNode, sourceX, sourceY),
    boxOf(targetNode, targetX, targetY),
    data?.lane ?? { index: 0, count: 1 },
  )
  const taken = data?.takenNonce != null

  return (
    <>
      <motion.path
        d={path}
        fill="none"
        className="react-flow__edge-path"
        markerEnd={markerEnd}
        initial={reduce ? false : { pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 0.45, ease: EASE_OUT }}
        style={taken ? { stroke: 'var(--color-accent)' } : undefined}
      />
      <path d={path} fill="none" stroke="transparent" strokeWidth={20} className="react-flow__edge-interaction" />
      {taken && (
        <g key={data?.takenNonce}>
          <path
            d={path}
            fill="none"
            stroke="var(--color-accent)"
            strokeWidth={2}
            strokeDasharray="5 11"
            strokeLinecap="round"
            className="animate-dash"
          />
          {!reduce && (
            <circle r={4} fill="var(--color-accent)">
              <animateMotion dur="1.1s" repeatCount="3" path={path} fill="freeze" />
            </circle>
          )}
        </g>
      )}
      <EdgeLabelRenderer>
        <button
          type="button"
          onClick={() => useEditor.getState().select({ kind: 'edge', ...parseEdgeId(id) })}
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          className={cn(
            'nodrag nopan pointer-events-auto absolute max-w-[180px] truncate rounded-full border px-2.5 py-[3px] font-mono text-[11px] font-medium',
            'transition-[color,background-color,border-color,box-shadow] duration-200 ease-out-soft hover:shadow-card',
            selected || taken
              ? 'border-accent bg-accent text-accent-ink'
              : data?.invalid && !data.inCall
                ? 'border-dashed border-danger bg-danger-soft text-danger'
                : 'border-border bg-card text-ink-soft hover:border-border-strong hover:text-ink',
          )}
        >
          {data?.fn || 'unnamed'}
        </button>
      </EdgeLabelRenderer>
    </>
  )
}

function boxOf(node: InternalNode | undefined, handleX: number, handleY: number): Box {
  if (!node?.measured.width || !node.measured.height) return { x: handleX, y: handleY, width: 0, height: 0 }
  const { x, y } = node.internals.positionAbsolute
  return { x, y, width: node.measured.width, height: node.measured.height }
}

export const FunctionEdge = memo(FunctionEdgeImpl)

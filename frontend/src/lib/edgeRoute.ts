import { Position, getBezierPath } from '@xyflow/react'

export type Box = { x: number; y: number; width: number; height: number }

/** Position of an edge among all edges joining the same unordered node pair. */
export type Lane = { index: number; count: number }

export type EdgeRoute = { path: string; labelX: number; labelY: number }

const LANE_SPREAD = 28
const ARC_CLEARANCE = 44
const ARC_LANE_STEP = 30
const EXIT = 24
const EXIT_LANE_STEP = 10
const CORNER = 14

/**
 * Forward edges (target handle right of the source handle) are beziers, fanned apart when they
 * share a node pair. Backward edges are rounded orthogonal routes: through the vertical gap
 * between the cards when one exists, otherwise in an arc over both cards.
 */
export function routeEdge(
  s: { x: number; y: number },
  t: { x: number; y: number },
  sourceBox: Box,
  targetBox: Box,
  lane: Lane,
): EdgeRoute {
  const spread = lane.index - (lane.count - 1) / 2

  if (t.x >= s.x) {
    if (spread === 0) {
      const [path, labelX, labelY] = getBezierPath({
        sourceX: s.x,
        sourceY: s.y,
        targetX: t.x,
        targetY: t.y,
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
      })
      return { path, labelX, labelY }
    }
    // A cubic's midpoint sits at 3/4 of its control-point offset, so 4/3 puts the label exactly `bend` off the chord.
    const bend = spread * LANE_SPREAD
    const pull = Math.max((t.x - s.x) / 2, 40)
    const cy = (bend * 4) / 3
    return {
      path: `M${s.x},${s.y} C${s.x + pull},${s.y + cy} ${t.x - pull},${t.y + cy} ${t.x},${t.y}`,
      labelX: (s.x + t.x) / 2,
      labelY: (s.y + t.y) / 2 + bend,
    }
  }

  const exit = EXIT + lane.index * EXIT_LANE_STEP
  const sBottom = sourceBox.y + sourceBox.height
  const tBottom = targetBox.y + targetBox.height
  const y =
    targetBox.y > sBottom
      ? (sBottom + targetBox.y) / 2 + spread * LANE_SPREAD
      : sourceBox.y > tBottom
        ? (tBottom + sourceBox.y) / 2 + spread * LANE_SPREAD
        : Math.min(sourceBox.y, targetBox.y) - ARC_CLEARANCE - lane.index * ARC_LANE_STEP
  const points: [number, number][] = [
    [s.x, s.y],
    [s.x + exit, s.y],
    [s.x + exit, y],
    [t.x - exit, y],
    [t.x - exit, t.y],
    [t.x, t.y],
  ]
  return { path: roundedPolyline(points, CORNER), labelX: (s.x + t.x) / 2, labelY: y }
}

function roundedPolyline(points: [number, number][], radius: number): string {
  let d = `M${points[0][0]},${points[0][1]}`
  for (let i = 1; i < points.length - 1; i++) {
    const [px, py] = points[i - 1]
    const [x, y] = points[i]
    const [nx, ny] = points[i + 1]
    const r = Math.min(radius, Math.hypot(x - px, y - py) / 2, Math.hypot(nx - x, ny - y) / 2)
    const inX = x - Math.sign(x - px) * r
    const inY = y - Math.sign(y - py) * r
    const outX = x + Math.sign(nx - x) * r
    const outY = y + Math.sign(ny - y) * r
    d += ` L${inX},${inY} Q${x},${y} ${outX},${outY}`
  }
  const [lx, ly] = points[points.length - 1]
  return `${d} L${lx},${ly}`
}

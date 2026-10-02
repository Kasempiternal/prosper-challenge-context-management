import { describe, expect, it } from 'vitest'
import { routeEdge, type Box } from './edgeRoute'

const card = (x: number, y: number): Box => ({ x, y, width: 280, height: 180 })
const solo = { index: 0, count: 1 }

describe('routeEdge', () => {
  it('keeps a lone forward edge as a bezier with its label at the chord midpoint', () => {
    const r = routeEdge({ x: 280, y: 60 }, { x: 420, y: 60 }, card(0, 0), card(420, 0), solo)
    expect(r.path.startsWith('M280,60 C')).toBe(true)
    expect([r.labelX, r.labelY]).toEqual([350, 60])
  })

  it('fans same-pair forward edges apart', () => {
    const a = routeEdge({ x: 280, y: 60 }, { x: 420, y: 60 }, card(0, 0), card(420, 0), { index: 0, count: 2 })
    const b = routeEdge({ x: 280, y: 60 }, { x: 420, y: 60 }, card(0, 0), card(420, 0), { index: 1, count: 2 })
    expect([a.labelY, b.labelY]).toEqual([46, 74])
  })

  it('arcs a same-row backward edge over both cards', () => {
    const r = routeEdge({ x: 1120, y: 60 }, { x: 420, y: 60 }, card(840, 0), card(420, 0), solo)
    expect([r.labelX, r.labelY]).toEqual([770, -44])
  })

  it('stacks a second backward edge on the same pair further out', () => {
    const r = routeEdge({ x: 1120, y: 60 }, { x: 420, y: 60 }, card(840, 0), card(420, 0), { index: 1, count: 2 })
    expect(r.labelY).toBe(-74)
  })

  it('routes a backward edge to a lower card through the gap between them', () => {
    const r = routeEdge({ x: 1120, y: 60 }, { x: 840, y: 380 }, card(840, 0), card(840, 320), solo)
    expect([r.labelX, r.labelY]).toEqual([980, 250])
    expect(r.path).toBe(
      'M1120,60 L1132,60 Q1144,60 1144,72 L1144,236 Q1144,250 1130,250 L830,250 Q816,250 816,264 L816,368 Q816,380 828,380 L840,380',
    )
  })
})

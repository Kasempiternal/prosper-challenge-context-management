import { describe, expect, it } from 'vitest'
import type { AgentConfig, AgentNode } from '../types/agent'
import { deleteNode, duplicateNode, makeDoc, nodeRefs, renameNode, replaceNode, updateEdge } from './ops'

const node = (name: string, targets: string[] = [], extra: Partial<AgentNode> = {}): AgentNode => ({
  name,
  task_messages: [{ role: 'developer', content: `do ${name}` }],
  edges: targets.map((t) => ({ function: `go_${t}`, description: '', target: t, properties: {}, required: [] })),
  ...extra,
})

const agent = (nodes: AgentNode[], extra: Partial<AgentConfig> = {}): AgentConfig => ({
  id: 'a',
  name: 'A',
  initial_node: nodes[0].name,
  nodes,
  persona: '',
  voice_id: 'v',
  model: 'm',
  ...extra,
})

/** Simulates typing `text` one character at a time into a node's name field. */
function typeName(doc: ReturnType<typeof makeDoc>, key: string, text: string) {
  const refs = nodeRefs(doc, key)
  let next = renameNode(doc, key, '', refs)
  for (let i = 1; i <= text.length; i++) next = renameNode(next, key, text.slice(0, i), refs)
  return next
}

describe('renameNode', () => {
  it('moves incoming edges and the start node with the renamed node', () => {
    const doc = makeDoc(agent([node('greet', ['ask']), node('ask', ['greet'])]))
    const next = renameNode(doc, doc.keys[0], 'hello', nodeRefs(doc, doc.keys[0]))
    expect(next.agent.initial_node).toBe('hello')
    expect(next.agent.nodes.map((n) => n.name)).toEqual(['hello', 'ask'])
    expect(next.agent.nodes[1].edges?.[0].target).toBe('hello')
  })

  it("typing through another node's name does not steal its edges", () => {
    // c -> foo, d -> bar. Rename foo to "bars", passing through "bar".
    const doc = makeDoc(agent([node('foo'), node('bar'), node('c', ['foo']), node('d', ['bar'])]))
    const next = typeName(doc, doc.keys[0], 'bars')
    expect(next.agent.nodes.map((n) => n.name)).toEqual(['bars', 'bar', 'c', 'd'])
    expect(next.agent.nodes[2].edges?.[0].target).toBe('bars')
    expect(next.agent.nodes[3].edges?.[0].target).toBe('bar')
    expect(next.agent.initial_node).toBe('bars')
  })

  it("typing through the start node's name keeps the start node", () => {
    const doc = makeDoc(agent([node('start', ['x']), node('x')]))
    const next = typeName(doc, doc.keys[1], 'starter')
    expect(next.agent.initial_node).toBe('start')
    expect(next.agent.nodes[0].edges?.[0].target).toBe('starter')
  })

  it('a node sharing its name with another owns no references', () => {
    const doc = makeDoc(agent([node('dup'), node('dup'), node('c', ['dup'])]))
    const next = renameNode(doc, doc.keys[1], 'other', nodeRefs(doc, doc.keys[1]))
    expect(next.agent.nodes[2].edges?.[0].target).toBe('dup')
    expect(next.agent.initial_node).toBe('dup')
  })
})

describe('replaceNode', () => {
  it('propagates a name change from the JSON tab', () => {
    const doc = makeDoc(agent([node('a', ['b']), node('b')]))
    const next = replaceNode(doc, doc.keys[1], node('renamed'))
    expect(next.agent.nodes[0].edges?.[0].target).toBe('renamed')
  })
})

describe('deleteNode', () => {
  it('drops edges into the node and moves the start to the first remaining node', () => {
    const doc = makeDoc(agent([node('a', ['b']), node('b', ['a']), node('c', ['b'])]))
    const next = deleteNode(doc, doc.keys[0])
    expect(next.keys).toEqual(doc.keys.slice(1))
    expect(next.agent.nodes.map((n) => [n.name, n.edges?.map((e) => e.target)])).toEqual([
      ['b', []],
      ['c', ['b']],
    ])
    expect(next.agent.initial_node).toBe('b')
  })

  it('returns the same doc for an unknown key', () => {
    const doc = makeDoc(agent([node('a')]))
    expect(deleteNode(doc, 'missing')).toBe(doc)
  })
})

describe('duplicateNode', () => {
  it('inserts an offset copy with a unique name right after the source', () => {
    const doc = makeDoc(agent([node('a', ['b'], { ui: { x: 10, y: 20 } }), node('b'), node('a_copy')]))
    const { doc: next, key } = duplicateNode(doc, doc.keys[0])
    expect(next.agent.nodes.map((n) => n.name)).toEqual(['a', 'a_copy_2', 'b', 'a_copy'])
    expect(next.keys[1]).toBe(key)
    expect(next.agent.nodes[1].ui).toEqual({ x: 58, y: 68 })
    expect(next.agent.nodes[1].edges).toEqual(next.agent.nodes[0].edges)
    expect(next.agent.nodes[1].edges).not.toBe(next.agent.nodes[0].edges)
  })
})

describe('unknown keys', () => {
  it('survive edits on agent, node and edge', () => {
    const raw = agent(
      [
        node('a', ['b'], { tools: ['lookup'], context_strategy: 'reset' }),
        node('b', [], { end: true }),
      ],
      { phase2: { flag: true } },
    )
    raw.nodes[0].edges![0].guard = 'x > 1'
    const doc = makeDoc(structuredClone(raw))
    let next = renameNode(doc, doc.keys[1], 'bye', nodeRefs(doc, doc.keys[1]))
    next = updateEdge(next, next.keys[0], 0, { description: 'when done' })
    next = typeName(next, next.keys[0], 'hello')

    expect(next.agent.phase2).toEqual({ flag: true })
    expect(next.agent.nodes[0].tools).toEqual(['lookup'])
    expect(next.agent.nodes[0].context_strategy).toBe('reset')
    expect(next.agent.nodes[0].edges?.[0]).toEqual({
      function: 'go_b',
      description: 'when done',
      target: 'bye',
      properties: {},
      required: [],
      guard: 'x > 1',
    })
    expect(JSON.parse(JSON.stringify(next.agent)).nodes[0].edges[0].guard).toBe('x > 1')
  })
})

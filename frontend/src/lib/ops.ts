import type { AgentConfig, AgentEdge, AgentNode, Doc, NodePosition } from '../types/agent'

// Pure edits over the editor Doc. Every op spreads the original objects so keys the
// editor does not know about survive.

let keySeq = 0
export const newKey = () => `n${(++keySeq).toString(36)}`

export function makeDoc(agent: AgentConfig): Doc {
  return { agent, keys: agent.nodes.map(newKey) }
}

export const edgeId = (nodeKey: string, index: number) => `${nodeKey}::${index}`

export function parseEdgeId(id: string): { key: string; index: number } {
  const [key, index] = id.split('::')
  return { key, index: Number(index) }
}

export function nodeByKey(doc: Doc, key: string): AgentNode | undefined {
  return doc.agent.nodes[doc.keys.indexOf(key)]
}

export function keyByName(doc: Doc, name: string): string | undefined {
  const i = doc.agent.nodes.findIndex((n) => n.name === name)
  return i === -1 ? undefined : doc.keys[i]
}

export function uniqueName(base: string, taken: Iterable<string>): string {
  const used = new Set(taken)
  if (!used.has(base)) return base
  let i = 2
  while (used.has(`${base}_${i}`)) i++
  return `${base}_${i}`
}

export function toIdentifier(text: string): string {
  const slug = text
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, '_')
    .replace(/^_+|_+$/g, '')
  return /^[a-z_]/.test(slug) ? slug : `n_${slug}`
}

function mapNodes(doc: Doc, fn: (node: AgentNode, key: string) => AgentNode): Doc {
  return { ...doc, agent: { ...doc.agent, nodes: doc.agent.nodes.map((n, i) => fn(n, doc.keys[i])) } }
}

export function updateNode(doc: Doc, key: string, patch: Partial<AgentNode>): Doc {
  return mapNodes(doc, (n, k) => (k === key ? { ...n, ...patch } : n))
}

/**
 * The references that belong to one node: whether it is the start node, and which edges
 * (by source key and index) point at it. Captured while the node's name is unique, so a
 * rename that passes through another node's name never takes that node's references.
 */
export interface NodeRefs {
  initial: boolean
  edges: Array<{ key: string; index: number }>
}

export function nodeRefs(doc: Doc, key: string): NodeRefs {
  const node = nodeByKey(doc, key)
  const owners = doc.agent.nodes.filter((n) => n.name === node?.name).length
  if (!node || owners !== 1) return { initial: false, edges: [] }
  const edges = doc.agent.nodes.flatMap((n, i) =>
    (n.edges ?? []).flatMap((e, index) => (e.target === node.name ? [{ key: doc.keys[i], index }] : [])),
  )
  return { initial: doc.agent.initial_node === node.name, edges }
}

function pointRefs(doc: Doc, refs: NodeRefs, name: string): Doc {
  const hit = new Set(refs.edges.map((r) => edgeId(r.key, r.index)))
  const nodes = doc.agent.nodes.map((n, i) =>
    n.edges?.some((_, index) => hit.has(edgeId(doc.keys[i], index)))
      ? { ...n, edges: n.edges.map((e, index) => (hit.has(edgeId(doc.keys[i], index)) ? { ...e, target: name } : e)) }
      : n,
  )
  return {
    ...doc,
    agent: { ...doc.agent, nodes, initial_node: refs.initial ? name : doc.agent.initial_node },
  }
}

/** Rename a node and move exactly `refs` (captured with `nodeRefs` when editing began) along with it. */
export function renameNode(doc: Doc, key: string, name: string, refs: NodeRefs): Doc {
  const prev = nodeByKey(doc, key)
  if (!prev || prev.name === name) return doc
  return pointRefs(updateNode(doc, key, { name }), refs, name)
}

/** Replace a node wholesale (JSON tab). A name change propagates like a rename. */
export function replaceNode(doc: Doc, key: string, next: AgentNode): Doc {
  const prev = nodeByKey(doc, key)
  if (!prev) return doc
  const refs = nodeRefs(doc, key)
  const replaced = mapNodes(doc, (n, k) => (k === key ? next : n))
  return prev.name === next.name ? replaced : pointRefs(replaced, refs, next.name)
}

export function addNode(doc: Doc, position: NodePosition): { doc: Doc; key: string } {
  const key = newKey()
  const name = uniqueName('new_node', doc.agent.nodes.map((n) => n.name))
  const node: AgentNode = {
    name,
    task_messages: [{ role: 'developer', content: '' }],
    edges: [],
    ui: position,
  }
  const agent = { ...doc.agent, nodes: [...doc.agent.nodes, node] }
  if (!agent.initial_node) agent.initial_node = name
  return { doc: { agent, keys: [...doc.keys, key] }, key }
}

export function duplicateNode(doc: Doc, key: string): { doc: Doc; key: string } {
  const src = nodeByKey(doc, key)
  if (!src) return { doc, key }
  const copyKey = newKey()
  const copy: AgentNode = {
    ...structuredClone(src),
    name: uniqueName(`${src.name}_copy`, doc.agent.nodes.map((n) => n.name)),
    ui: { x: (src.ui?.x ?? 0) + 48, y: (src.ui?.y ?? 0) + 48 },
  }
  const i = doc.keys.indexOf(key) + 1
  const nodes = [...doc.agent.nodes]
  const keys = [...doc.keys]
  nodes.splice(i, 0, copy)
  keys.splice(i, 0, copyKey)
  return { doc: { agent: { ...doc.agent, nodes }, keys }, key: copyKey }
}

export function deleteNode(doc: Doc, key: string): Doc {
  const i = doc.keys.indexOf(key)
  if (i === -1) return doc
  const name = doc.agent.nodes[i].name
  const nodes = doc.agent.nodes
    .filter((_, j) => j !== i)
    .map((n) =>
      n.edges?.some((e) => e.target === name) ? { ...n, edges: n.edges.filter((e) => e.target !== name) } : n,
    )
  const initial = doc.agent.initial_node === name ? (nodes[0]?.name ?? '') : doc.agent.initial_node
  return {
    agent: { ...doc.agent, nodes, initial_node: initial },
    keys: doc.keys.filter((_, j) => j !== i),
  }
}

export function setStart(doc: Doc, key: string): Doc {
  const node = nodeByKey(doc, key)
  return node ? { ...doc, agent: { ...doc.agent, initial_node: node.name } } : doc
}

export function toggleEnd(doc: Doc, key: string): Doc {
  return mapNodes(doc, (n, k) => (k === key ? { ...n, end: !n.end } : n))
}

export function moveNodes(doc: Doc, positions: Record<string, NodePosition>): Doc {
  return mapNodes(doc, (n, k) => {
    const p = positions[k]
    return p ? { ...n, ui: { x: Math.round(p.x), y: Math.round(p.y) } } : n
  })
}

export function addEdge(doc: Doc, sourceKey: string, targetKey: string): { doc: Doc; index: number } {
  const source = nodeByKey(doc, sourceKey)
  const target = nodeByKey(doc, targetKey)
  if (!source || !target) return { doc, index: -1 }
  const edges = source.edges ?? []
  const edge: AgentEdge = {
    function: uniqueName(`go_to_${toIdentifier(target.name)}`, edges.map((e) => e.function)),
    description: '',
    target: target.name,
    properties: {},
    required: [],
  }
  return { doc: updateNode(doc, sourceKey, { edges: [...edges, edge] }), index: edges.length }
}

export function updateEdge(doc: Doc, key: string, index: number, patch: Partial<AgentEdge>): Doc {
  return mapNodes(doc, (n, k) =>
    k === key && n.edges ? { ...n, edges: n.edges.map((e, i) => (i === index ? { ...e, ...patch } : e)) } : n,
  )
}

export function replaceEdge(doc: Doc, key: string, index: number, edge: AgentEdge): Doc {
  return mapNodes(doc, (n, k) =>
    k === key && n.edges ? { ...n, edges: n.edges.map((e, i) => (i === index ? edge : e)) } : n,
  )
}

export function deleteEdge(doc: Doc, key: string, index: number): Doc {
  return mapNodes(doc, (n, k) => (k === key && n.edges ? { ...n, edges: n.edges.filter((_, i) => i !== index) } : n))
}

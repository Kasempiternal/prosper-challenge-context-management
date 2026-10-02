import type { Edge, Node } from '@xyflow/react'
import type { Lane } from '../../lib/edgeRoute'
import type { AgentNode } from '../../types/agent'

export type CallMark = 'active' | 'visited' | null

export type AgentNodeData = {
  node: AgentNode
  isStart: boolean
  issueCount: number
  call: CallMark
  /** A test call is connecting or live. */
  inCall: boolean
}

export type FunctionEdgeData = {
  fn: string
  invalid: boolean
  inCall: boolean
  /** Nonce of the live-call transition that just used this edge; drives the flow animation. */
  takenNonce: number | null
  lane: Lane
}

export type AgentFlowNode = Node<AgentNodeData, 'agent'>
export type FunctionFlowEdge = Edge<FunctionEdgeData, 'function'>

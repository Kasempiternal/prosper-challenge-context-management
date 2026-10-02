import type { Doc, ValidationIssue } from '../types/agent'

export type IssueTarget =
  | { kind: 'agent'; field: string }
  | { kind: 'node'; key: string; field: string }
  | { kind: 'edge'; key: string; index: number; field: string }

export interface Issue extends ValidationIssue {
  target: IssueTarget
}

const PATH = /^nodes\[(\d+)\](?:\.edges\[(\d+)\])?(?:\.(.*))?$/

/** Resolve contract paths ("nodes[1].edges[0].target") to stable node keys of the doc that was validated. */
export function resolveIssues(doc: Doc, issues: ValidationIssue[]): Issue[] {
  return issues.map((issue) => {
    const m = PATH.exec(issue.path)
    const key = m ? doc.keys[Number(m[1])] : undefined
    if (!m || !key) return { ...issue, target: { kind: 'agent', field: issue.path } }
    const field = m[3] ?? ''
    if (m[2] !== undefined) return { ...issue, target: { kind: 'edge', key, index: Number(m[2]), field } }
    return { ...issue, target: { kind: 'node', key, field } }
  })
}

/** Messages for one field. `field` matches exactly or as a prefix ("task_messages" matches "task_messages[0].content"). */
export function fieldErrors(issues: Issue[], scope: Omit<IssueTarget, 'field'>, field: string): string[] {
  return issues
    .filter((i) => {
      const t = i.target
      if (t.kind !== scope.kind) return false
      if ('key' in scope && 'key' in t && t.key !== scope.key) return false
      if ('index' in scope && 'index' in t && t.index !== scope.index) return false
      return t.field === field || t.field.startsWith(`${field}[`) || t.field.startsWith(`${field}.`)
    })
    .map((i) => i.message)
}

export function issuesForNode(issues: Issue[], key: string): Issue[] {
  return issues.filter((i) => i.target.kind !== 'agent' && i.target.key === key)
}

export function describeTarget(doc: Doc, target: IssueTarget): string {
  if (target.kind === 'agent') return 'Agent'
  const node = doc.agent.nodes[doc.keys.indexOf(target.key)]
  if (!node) return 'Unknown'
  if (target.kind === 'node') return node.name
  return `${node.name} → ${node.edges?.[target.index]?.function ?? 'edge'}`
}

export function callBlockedHint(issueCount: number): string {
  return `Fix ${issueCount} ${issueCount === 1 ? 'issue' : 'issues'} to start a call`
}

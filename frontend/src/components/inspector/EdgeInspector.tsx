import { ArrowRight, Spline, Trash2 } from 'lucide-react'
import { useCallback, useState } from 'react'
import { deleteSelection } from '../../lib/actions'
import { fieldErrors } from '../../lib/issues'
import { nodeByKey, replaceEdge, updateEdge } from '../../lib/ops'
import { useEditor } from '../../store/editor'
import type { AgentEdge } from '../../types/agent'
import { Button } from '../ui/Button'
import { Field, Input, Select, Textarea } from '../ui/Field'
import { Tabs } from '../ui/Tabs'
import { FieldsTable } from './FieldsTable'
import { JsonEditor } from './JsonEditor'
import { PanelBody, PanelHeader, Section } from './Panel'

type Tab = 'configure' | 'json'

export function EdgeInspector({ nodeKey, index }: { nodeKey: string; index: number }) {
  const source = useEditor((s) => (s.doc ? nodeByKey(s.doc, nodeKey) : undefined))
  const nodeNames = useEditor((s) => s.doc?.agent.nodes.map((n) => n.name).join('\n') ?? '')
  const issues = useEditor((s) => s.issues)
  const { apply, select } = useEditor.getState()
  const [tab, setTab] = useState<Tab>('configure')

  const checkJson = useCallback((v: unknown) => {
    if (typeof v !== 'object' || v === null || Array.isArray(v)) return 'An edge must be a JSON object'
    const e = v as Partial<AgentEdge>
    if (typeof e.function !== 'string') return '"function" must be a string'
    if (typeof e.target !== 'string') return '"target" must be a string'
    return null
  }, [])

  const edge = source?.edges?.[index]
  if (!source || !edge) return null

  const scope = { kind: 'edge' as const, key: nodeKey, index }
  const errs = (field: string) => fieldErrors(issues, scope, field)
  const edit = (patch: Partial<AgentEdge>, coalesce?: string) =>
    apply((d) => updateEdge(d, nodeKey, index, patch), { coalesce: coalesce && `${nodeKey}:${index}:${coalesce}` })
  const targets = nodeNames.split('\n').filter((n) => n !== source.name)
  const issueCount = issues.filter((i) => i.target.kind === 'edge' && i.target.key === nodeKey && i.target.index === index).length

  return (
    <>
      <PanelHeader
        icon={<Spline className="size-4" />}
        eyebrow="Transition"
        title={
          <span className="flex items-center gap-1.5 font-mono">
            <span className="truncate">{source.name}</span>
            <ArrowRight className="size-3.5 shrink-0 text-muted" />
            <span className="truncate">{edge.target || '?'}</span>
          </span>
        }
        onClose={() => select(null)}
      />
      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        tabs={[
          { value: 'configure', label: 'Configure', badge: issueCount },
          { value: 'json', label: 'Advanced (JSON)' },
        ]}
      />
      <PanelBody>
        {tab === 'json' ? (
          <JsonEditor<AgentEdge>
            value={edge}
            check={checkJson}
            onApply={(next) => apply((d) => replaceEdge(d, nodeKey, index, next))}
            hint="The transition exactly as saved, including keys the editor doesn’t render. Ctrl+Enter applies."
          />
        ) : (
          <div className="flex flex-col gap-7">
            <div className="flex flex-col gap-4">
              <Field
                label="Function name"
                htmlFor="edge-fn"
                errors={errs('function')}
                hint="The tool the LLM calls to take this transition. Letters, digits and underscores."
              >
                <Input
                  id="edge-fn"
                  className="font-mono"
                  spellCheck={false}
                  value={edge.function}
                  invalid={errs('function').length > 0}
                  onChange={(e) => edit({ function: e.target.value }, 'fn')}
                />
              </Field>
              <Field
                label="When should the agent take this transition?"
                htmlFor="edge-desc"
                errors={errs('description')}
                hint="Shown to the LLM as the tool description."
              >
                <Textarea
                  id="edge-desc"
                  value={edge.description}
                  invalid={errs('description').length > 0}
                  placeholder="e.g. Once the caller has confirmed their date of birth."
                  onChange={(e) => edit({ description: e.target.value }, 'desc')}
                />
              </Field>
              <Field label="Go to" htmlFor="edge-target" errors={errs('target')}>
                <Select
                  id="edge-target"
                  className="font-mono"
                  value={edge.target}
                  invalid={errs('target').length > 0}
                  onChange={(e) => edit({ target: e.target.value })}
                >
                  {!targets.includes(edge.target) && <option value={edge.target}>{edge.target || 'Choose a node'}</option>}
                  {targets.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>

            <Section title="Collected fields">
              <p className="-mt-1 text-[12.5px] leading-snug text-muted">
                Arguments the LLM must fill when it calls this function. Values land in the call’s collected data.
              </p>
              <FieldsTable edge={edge} onChange={(patch, coalesce) => edit(patch, coalesce && `fields:${coalesce}`)} errors={errs} />
            </Section>

            <div className="border-t border-border-subtle pt-5">
              <Button variant="danger-ghost" icon={<Trash2 className="size-4" />} onClick={deleteSelection}>
                Delete transition
              </Button>
            </div>
          </div>
        )}
      </PanelBody>
    </>
  )
}

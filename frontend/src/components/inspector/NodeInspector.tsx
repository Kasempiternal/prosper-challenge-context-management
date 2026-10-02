import { AnimatePresence, motion } from 'motion/react'
import { ArrowRight, Box, ChevronDown, Play, Plus, Trash2, Wrench, X } from 'lucide-react'
import { useCallback, useRef, useState } from 'react'
import { deleteSelection } from '../../lib/actions'
import { cn } from '../../lib/cn'
import { fieldErrors } from '../../lib/issues'
import { fade, softSpring, spring } from '../../lib/motion'
import { nodeByKey, nodeRefs, renameNode, replaceNode, setStart, toggleEnd, updateNode, type NodeRefs } from '../../lib/ops'
import { CONTEXT_STRATEGIES, TOOL_DESCRIPTIONS } from '../../lib/tools'
import { useEditor } from '../../store/editor'
import type { AgentNode, ContextStrategy, TaskMessage } from '../../types/agent'
import { Button, IconButton } from '../ui/Button'
import { Field, FieldErrors, Input, Select, Textarea } from '../ui/Field'
import { Tabs } from '../ui/Tabs'
import { Toggle } from '../ui/Toggle'
import { JsonEditor } from './JsonEditor'
import { PanelBody, PanelHeader, Section } from './Panel'

type Tab = 'configure' | 'json'

export function NodeInspector({ nodeKey }: { nodeKey: string }) {
  const node = useEditor((s) => (s.doc ? nodeByKey(s.doc, nodeKey) : undefined))
  const isStart = useEditor((s) => !!node && s.doc?.agent.initial_node === node.name)
  const issues = useEditor((s) => s.issues)
  const { apply, select } = useEditor.getState()
  const [tab, setTab] = useState<Tab>('configure')
  // References owned by this node when the user started typing its name; see nodeRefs.
  const renameRefs = useRef<NodeRefs | null>(null)

  const checkJson = useCallback((v: unknown) => {
    if (typeof v !== 'object' || v === null || Array.isArray(v)) return 'A node must be a JSON object'
    const n = v as Partial<AgentNode>
    if (typeof n.name !== 'string') return '"name" must be a string'
    if (!Array.isArray(n.task_messages)) return '"task_messages" must be an array'
    return null
  }, [])

  if (!node) return null
  const scope = { kind: 'node' as const, key: nodeKey }
  const errs = (field: string) => fieldErrors(issues, scope, field)
  const messages = node.task_messages
  const edit = (patch: Partial<AgentNode>, coalesce?: string) =>
    apply((d) => updateNode(d, nodeKey, patch), { coalesce: coalesce && `${nodeKey}:${coalesce}` })
  const setMessages = (next: TaskMessage[], coalesce?: string) => edit({ task_messages: next }, coalesce)
  const jsonErrors = issues.filter((i) => i.target.kind === 'node' && i.target.key === nodeKey).length

  return (
    <>
      <PanelHeader
        icon={<Box className="size-4" />}
        eyebrow="Node"
        title={<span className="font-mono">{node.name || 'unnamed'}</span>}
        onClose={() => select(null)}
      />
      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        tabs={[
          { value: 'configure', label: 'Configure', badge: jsonErrors },
          { value: 'json', label: 'Advanced (JSON)' },
        ]}
      />
      <PanelBody>
        {tab === 'json' ? (
          <JsonEditor<AgentNode>
            value={node}
            check={checkJson}
            onApply={(next) => apply((d) => replaceNode(d, nodeKey, next))}
            hint="The node exactly as saved. Keys the editor doesn’t know about are preserved. Ctrl+Enter applies."
          />
        ) : (
          <div className="flex flex-col gap-7">
            <div className="flex flex-col gap-4">
              <Field label="Name" htmlFor="node-name" errors={errs('name')} hint="Referenced by transitions. Renaming updates them.">
                <Input
                  id="node-name"
                  className="font-mono"
                  spellCheck={false}
                  value={node.name}
                  invalid={errs('name').length > 0}
                  onFocus={() => {
                    const doc = useEditor.getState().doc
                    renameRefs.current = doc && nodeRefs(doc, nodeKey)
                  }}
                  onBlur={() => {
                    renameRefs.current = null
                  }}
                  onChange={(e) => {
                    const name = e.target.value
                    apply((d) => renameNode(d, nodeKey, name, renameRefs.current ?? nodeRefs(d, nodeKey)), {
                      coalesce: `${nodeKey}:name`,
                    })
                  }}
                />
              </Field>

              <div className="flex flex-col divide-y divide-border-subtle rounded-[12px] border border-border-subtle">
                <div className="flex items-center justify-between gap-3 px-3.5 py-2.5">
                  <div>
                    <p className="text-[13px] font-medium">Start node</p>
                    <p className="text-[12px] text-muted">Where every call begins</p>
                  </div>
                  {isStart ? (
                    <span className="rounded-full bg-info-soft px-2.5 py-1 text-[11.5px] font-semibold text-info">Start</span>
                  ) : (
                    <Button size="sm" icon={<Play className="size-3" />} onClick={() => apply((d) => setStart(d, nodeKey))}>
                      Set as start
                    </Button>
                  )}
                </div>
                <label htmlFor="node-end" className="flex cursor-pointer items-center justify-between gap-3 px-3.5 py-2.5">
                  <div>
                    <p className="text-[13px] font-medium">End node</p>
                    <p className="text-[12px] text-muted">Hang up after this node speaks</p>
                  </div>
                  <Toggle id="node-end" label="End node" checked={!!node.end} onChange={() => apply((d) => toggleEnd(d, nodeKey))} />
                </label>
              </div>
              <FieldErrors errors={errs('end')} />
            </div>

            <Section
              title="Instructions"
              aside={
                <Button
                  size="sm"
                  variant="ghost"
                  icon={<Plus className="size-3.5" />}
                  onClick={() => setMessages([...messages, { role: 'developer', content: '' }])}
                >
                  Add
                </Button>
              }
            >
              <p className="-mt-1 text-[12.5px] leading-snug text-muted">
                What the agent should accomplish in this node. Sent as developer messages.
              </p>
              <AnimatePresence initial={false}>
                {messages.map((m, i) => (
                  <motion.div
                    key={i}
                    layout
                    initial={{ opacity: 0, y: -4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, height: 0 }}
                    transition={spring}
                    className="group relative"
                  >
                    <Textarea
                      aria-label={`Instruction ${i + 1}`}
                      value={m.content}
                      placeholder="e.g. Ask for the caller’s date of birth to verify identity."
                      invalid={errs(`task_messages[${i}]`).length > 0}
                      onChange={(e) =>
                        setMessages(
                          messages.map((x, j) => (j === i ? { ...x, content: e.target.value } : x)),
                          `task:${i}`,
                        )
                      }
                      className="pr-9"
                    />
                    {messages.length > 1 && (
                      <IconButton
                        label={`Remove instruction ${i + 1}`}
                        onClick={() => setMessages(messages.filter((_, j) => j !== i))}
                        className="absolute top-1.5 right-1.5 size-7 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100"
                      >
                        <X className="size-3.5" />
                      </IconButton>
                    )}
                    <FieldErrors errors={errs(`task_messages[${i}]`)} />
                  </motion.div>
                ))}
              </AnimatePresence>
              {messages.length === 0 && <FieldErrors errors={errs('task_messages')} />}
            </Section>

            <RoleOverride
              value={node.role_message ?? ''}
              errors={errs('role_message')}
              onChange={(text) => {
                const { role_message: _drop, ...rest } = node
                apply((d) => replaceNode(d, nodeKey, text ? { ...rest, role_message: text } : rest), {
                  coalesce: `${nodeKey}:role`,
                })
              }}
            />

            {(node.tools ?? []).length > 0 && (
              <Section title="Tools">
                <p className="-mt-1 text-[12.5px] leading-snug text-muted">
                  Functions the agent can call here without leaving the node. Edit in Advanced (JSON).
                </p>
                <ul className="flex flex-col gap-1">
                  {(node.tools ?? []).map((t) => (
                    <li key={t} className="flex items-start gap-2.5 rounded-[10px] border border-border-subtle px-3 py-2">
                      <Wrench className="mt-0.5 size-3.5 shrink-0 text-faint" aria-hidden />
                      <div className="min-w-0">
                        <p className="truncate font-mono text-[12.5px] font-medium">{t}</p>
                        <p className={cn('text-[12px] leading-snug', TOOL_DESCRIPTIONS[t] ? 'text-muted' : 'text-warning')}>
                          {TOOL_DESCRIPTIONS[t] ?? 'Not a known tool; the backend will reject it.'}
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>
                <FieldErrors errors={errs('tools')} />
              </Section>
            )}

            <Field
              label="Context"
              htmlFor="node-context"
              errors={errs('context_strategy')}
              hint={CONTEXT_STRATEGIES.find((c) => c.value === (node.context_strategy ?? 'append'))?.hint}
            >
              <Select
                id="node-context"
                value={node.context_strategy ?? 'append'}
                onChange={(e) => edit({ context_strategy: e.target.value as ContextStrategy })}
              >
                {CONTEXT_STRATEGIES.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </Select>
            </Field>

            <Section title="Transitions">
              <FieldErrors errors={errs('edges')} />
              {(node.edges ?? []).length === 0 ? (
                <p className="rounded-[12px] border border-dashed border-border px-3.5 py-3 text-[12.5px] leading-snug text-muted">
                  {node.end
                    ? 'End nodes hang up, so they need no transitions.'
                    : 'Drag from this node’s right handle onto another node to add a transition.'}
                </p>
              ) : (
                <ul className="flex flex-col gap-1">
                  {(node.edges ?? []).map((e, i) => (
                    <li key={`${e.function}-${i}`}>
                      <button
                        type="button"
                        onClick={() => select({ kind: 'edge', key: nodeKey, index: i })}
                        className="group flex w-full items-center gap-2 rounded-[10px] border border-border-subtle px-3 py-2 text-left transition-colors duration-150 hover:border-border hover:bg-surface"
                      >
                        <span className="truncate font-mono text-[12.5px] font-medium">{e.function || 'unnamed'}</span>
                        <ArrowRight className="size-3.5 shrink-0 text-muted transition-transform duration-200 group-hover:translate-x-0.5" />
                        <span className="truncate font-mono text-[12.5px] text-muted">{e.target}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Section>

            <div className="border-t border-border-subtle pt-5">
              <Button variant="danger-ghost" icon={<Trash2 className="size-4" />} onClick={deleteSelection}>
                Delete node
              </Button>
            </div>
          </div>
        )}
      </PanelBody>
    </>
  )
}

function RoleOverride({ value, errors, onChange }: { value: string; errors: string[]; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(!!value)
  return (
    <section className="rounded-[12px] border border-border-subtle">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between gap-3 px-3.5 py-2.5 text-left"
      >
        <span>
          <span className="block text-[13px] font-medium">Role override</span>
          <span className="block text-[12px] text-muted">{value ? 'Replaces the agent persona here' : 'Uses the agent persona'}</span>
        </span>
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={softSpring}>
          <ChevronDown className="size-4 text-muted" />
        </motion.span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={fade}
            className="overflow-hidden"
          >
            <div className="px-3.5 pb-3.5">
              <Field label="Role message" htmlFor="role-message" errors={errors}>
                <Textarea
                  id="role-message"
                  value={value}
                  placeholder="Leave empty to inherit the agent persona."
                  onChange={(e) => onChange(e.target.value)}
                />
              </Field>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  )
}

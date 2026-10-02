import { Settings2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { catalogCounts, catalogOptions, useCatalogs } from '../../lib/catalogs'
import { effectiveChooser, effectiveTimeoutMs } from '../../lib/chooser'
import { fieldErrors } from '../../lib/issues'
import { useEditor } from '../../store/editor'
import type { AgentConfig, ResolverConfig, Voice } from '../../types/agent'
import { ChooserControl } from '../call/ChooserControl'
import { Field, FieldErrors, Input, Select, Textarea } from '../ui/Field'
import { Toggle } from '../ui/Toggle'
import { PanelBody, PanelHeader, Section } from './Panel'

interface VoiceOptions {
  voices: Voice[]
  models: string[]
}

let optionsRequest: Promise<VoiceOptions> | null = null
function loadOptions(): Promise<VoiceOptions> {
  optionsRequest ??= Promise.all([api.listVoices(), api.listModels()])
    .then(([voices, models]) => ({ voices, models }))
    .catch((err) => {
      optionsRequest = null
      throw err
    })
  return optionsRequest
}

export function AgentSettings() {
  const agent = useEditor((s) => s.doc?.agent)
  const issues = useEditor((s) => s.issues)
  const { apply, select } = useEditor.getState()
  const [options, setOptions] = useState<VoiceOptions | null>(null)

  useEffect(() => {
    loadOptions().then(setOptions, () => setOptions({ voices: [], models: [] }))
  }, [])

  if (!agent) return null
  const errs = (field: string) => fieldErrors(issues, { kind: 'agent' }, field)
  const edit = (patch: Partial<AgentConfig>, coalesce?: string) =>
    apply((d) => ({ ...d, agent: { ...d.agent, ...patch } }), { coalesce: coalesce && `agent:${coalesce}` })

  const voices = options?.voices ?? []
  const models = options?.models ?? []

  return (
    <>
      <PanelHeader icon={<Settings2 className="size-4" />} eyebrow="Agent" title="Settings" onClose={() => select(null)} />
      <div className="h-px bg-border-subtle" />
      <PanelBody>
        <div className="flex flex-col gap-5">
          <Field label="Start node" htmlFor="agent-start" errors={errs('initial_node')} hint="The node every call begins in.">
            <Select
              id="agent-start"
              className="font-mono"
              value={agent.initial_node}
              invalid={errs('initial_node').length > 0}
              onChange={(e) => edit({ initial_node: e.target.value })}
            >
              {!agent.nodes.some((n) => n.name === agent.initial_node) && (
                <option value={agent.initial_node}>{agent.initial_node || 'Choose a node'}</option>
              )}
              {agent.nodes.map((n) => (
                <option key={n.name} value={n.name}>
                  {n.name}
                </option>
              ))}
            </Select>
          </Field>

          <Field
            label="Persona"
            htmlFor="agent-persona"
            errors={errs('persona')}
            hint="Applied to every node unless a node overrides its role. Replies are spoken, so keep it voice-friendly."
          >
            <Textarea
              id="agent-persona"
              value={agent.persona}
              className="min-h-[140px]"
              onChange={(e) => edit({ persona: e.target.value }, 'persona')}
            />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Voice" htmlFor="agent-voice" errors={errs('voice_id')}>
              <Select id="agent-voice" value={agent.voice_id} onChange={(e) => edit({ voice_id: e.target.value })} disabled={!options}>
                {!voices.some((v) => v.id === agent.voice_id) && <option value={agent.voice_id}>{agent.voice_id || '—'}</option>}
                {voices.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Model" htmlFor="agent-model" errors={errs('model')}>
              <Select id="agent-model" value={agent.model} onChange={(e) => edit({ model: e.target.value })} disabled={!options}>
                {!models.includes(agent.model) && <option value={agent.model}>{agent.model || '—'}</option>}
                {models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          {voices.find((v) => v.id === agent.voice_id)?.description && (
            <p className="-mt-2 text-[12px] text-muted">{voices.find((v) => v.id === agent.voice_id)?.description}</p>
          )}

          {typeof agent.catalog === 'string' && (
            <Scheduling
              catalog={agent.catalog}
              resolver={agent.resolver ?? {}}
              errs={errs}
              onCatalogChange={(path) => edit({ catalog: path })}
              onChange={(resolver, coalesce) => edit({ resolver }, coalesce)}
            />
          )}
        </div>
      </PanelBody>
    </>
  )
}

interface SchedulingProps {
  catalog: string
  resolver: ResolverConfig
  errs: (field: string) => string[]
  onCatalogChange: (path: string) => void
  onChange: (resolver: ResolverConfig, coalesce?: string) => void
}

/** Only the key being edited is written; untouched resolver keys stay absent so backend defaults apply. */
function Scheduling({ catalog, resolver, errs, onCatalogChange, onChange }: SchedulingProps) {
  const catalogs = useCatalogs()
  const selected = catalogs?.find((c) => c.path === catalog)
  const speakDirect = resolver.speak_direct ?? true
  const chooser = effectiveChooser(resolver)
  const networked = chooser === 'jev' || chooser === 'openai'

  return (
    <div className="mt-2 border-t border-border-subtle pt-5">
      <Section title="Scheduling">
        <Field label="Catalog" htmlFor="agent-catalog" errors={errs('catalog')} hint="Doctors, locations and visit types the tools resolve against.">
          <Select
            id="agent-catalog"
            value={catalog}
            invalid={errs('catalog').length > 0}
            disabled={!catalogs}
            onChange={(e) => onCatalogChange(e.target.value)}
          >
            {catalogOptions(catalogs ?? [], catalog).map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
          <p className="flex items-baseline justify-between gap-3 text-[12px]">
            <span className="shrink-0 text-ink-soft tabular-nums">{selected ? catalogCounts(selected) : 'Not in the catalog list'}</span>
            <span className="min-w-0 truncate font-mono text-[11px] text-faint" title={catalog}>
              {catalog}
            </span>
          </p>
        </Field>

        <div className="flex flex-col divide-y divide-border-subtle rounded-[12px] border border-border-subtle">
          <label htmlFor="resolver-speak" className="flex cursor-pointer items-center justify-between gap-3 px-3.5 py-2.5">
            <div>
              <p className="text-[13px] font-medium">Speak offers directly</p>
              <p className="text-[12px] leading-snug text-muted">Templated offers and questions go straight to the voice, skipping an LLM turn.</p>
            </div>
            <Toggle
              id="resolver-speak"
              label="Speak offers directly"
              checked={speakDirect}
              onChange={(v) => onChange({ ...resolver, speak_direct: v })}
            />
          </label>
          <div className="flex flex-col gap-2.5 px-3.5 py-2.5">
            <ChooserControl id="resolver-chooser" />
            <div className="flex items-center justify-between gap-3">
              <label htmlFor="resolver-timeout" className={networked ? 'text-[12.5px] text-ink-soft' : 'text-[12.5px] text-faint'}>
                Model timeout per turn
              </label>
              <div className="relative w-[120px]">
                <Input
                  id="resolver-timeout"
                  type="number"
                  min={1}
                  max={30000}
                  step={100}
                  inputMode="numeric"
                  disabled={!networked}
                  className="[appearance:textfield] pr-9 text-right font-mono tabular-nums disabled:opacity-50 [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                  placeholder={String(effectiveTimeoutMs(resolver))}
                  invalid={errs('resolver').length > 0}
                  value={resolver.timeout_ms ?? ''}
                  onChange={(e) => {
                    const { timeout_ms: _drop, ...rest } = resolver
                    const ms = e.target.valueAsNumber
                    onChange(Number.isFinite(ms) ? { ...rest, timeout_ms: Math.round(ms) } : rest, 'resolver-timeout')
                  }}
                />
                <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-[12px] text-faint">ms</span>
              </div>
            </div>
          </div>
        </div>
        <FieldErrors errors={errs('resolver')} />
      </Section>
    </div>
  )
}

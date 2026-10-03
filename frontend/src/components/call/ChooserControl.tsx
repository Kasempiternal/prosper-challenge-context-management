import { CHOOSERS, effectiveChooser, modeLabel, type Chooser } from '../../lib/chooser'
import { isCallActive, useCall } from '../../store/call'
import { useEditor } from '../../store/editor'
import { useTelemetry } from '../../store/telemetry'
import { KeyRow } from '../keys/KeyRow'
import { Segmented } from '../ui/Segmented'

/**
 * "Disambiguator: JEV / OpenAI / Embeddings / Off" for the agent being edited. Writes
 * resolver.chooser into the draft, which is what the next test call sends; locked while a call runs.
 * With JEV or OpenAI picked, that provider's key row sits under the switch.
 */
export function ChooserControl({ id }: { id: string }) {
  const resolver = useEditor((s) => s.doc?.agent.resolver)
  const locked = useCall((s) => isCallActive(s.status))
  const mode = useTelemetry((s) => s.mode)
  const chooser = effectiveChooser(resolver)
  const set = (next: Chooser) =>
    useEditor.getState().apply((d) => ({ ...d, agent: { ...d.agent, resolver: { ...d.agent.resolver, chooser: next } } }))

  return (
    <div className="flex w-full flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[12.5px] font-medium text-ink-soft">Disambiguator</span>
        {locked && (
          <span className="truncate text-[11.5px] text-faint">{mode ? `This call: ${modeLabel(mode)}` : 'Locked during the call'}</span>
        )}
      </div>
      <Segmented
        id={id}
        label="Disambiguator"
        value={chooser}
        onChange={set}
        disabled={locked}
        options={CHOOSERS.map((c) => ({ value: c.value, label: c.label, title: c.hint }))}
      />
      <p className="text-[12px] leading-snug text-muted">{CHOOSERS.find((c) => c.value === chooser)?.hint}</p>
      {(chooser === 'jev' || chooser === 'openai') && <KeyRow key={chooser} provider={chooser} id={id} variant="inline" locked={locked} />}
    </div>
  )
}

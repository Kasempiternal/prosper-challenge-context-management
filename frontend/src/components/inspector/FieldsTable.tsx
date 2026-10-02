import { AnimatePresence, motion } from 'motion/react'
import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { cn } from '../../lib/cn'
import { spring } from '../../lib/motion'
import { uniqueName } from '../../lib/ops'
import { FIELD_TYPES, type AgentEdge, type PropertySchema } from '../../types/agent'
import { Button, IconButton } from '../ui/Button'
import { ChipsInput } from '../ui/ChipsInput'
import { FieldErrors, Select } from '../ui/Field'
import { controlClass } from '../ui/control'

interface FieldsTableProps {
  edge: AgentEdge
  onChange: (patch: Pick<AgentEdge, 'properties' | 'required'>, coalesce?: string) => void
  errors: (field: string) => string[]
}

/** Edits `properties` (JSON-schema per field) and `required` together; renames keep order and required-ness. */
export function FieldsTable({ edge, onChange, errors }: FieldsTableProps) {
  const properties = edge.properties ?? {}
  const required = edge.required ?? []
  const entries = Object.entries(properties)
  // A name typed onto a sibling's name can't be stored (keys are unique), so it stays local to the row.
  const [clashes, setClashes] = useState<Record<number, string>>({})

  const rebuild = (fn: (list: Array<[string, PropertySchema]>) => Array<[string, PropertySchema]>) =>
    Object.fromEntries(fn(entries))

  const rename = (row: number, from: string, to: string) => {
    if (to !== from && Object.hasOwn(properties, to)) {
      setClashes({ ...clashes, [row]: to })
      return
    }
    const { [row]: _resolved, ...rest } = clashes
    setClashes(rest)
    onChange(
      {
        properties: rebuild((list) => list.map(([k, v]) => [k === from ? to : k, v])),
        required: required.map((r) => (r === from ? to : r)),
      },
      `rename:${row}`,
    )
  }

  const patch = (name: string, next: PropertySchema, coalesce?: string) =>
    onChange({ properties: { ...properties, [name]: next }, required }, coalesce && `${name}:${coalesce}`)

  const remove = (name: string) => {
    setClashes({})
    onChange({ properties: rebuild((list) => list.filter(([k]) => k !== name)), required: required.filter((r) => r !== name) })
  }

  const add = () => {
    const name = uniqueName('field', Object.keys(properties))
    onChange({ properties: { ...properties, [name]: { type: 'string', description: '' } }, required: [...required, name] })
  }

  return (
    <div className="flex flex-col gap-2">
      <AnimatePresence initial={false}>
        {entries.map(([name, schema], i) => {
          const isRequired = required.includes(name)
          const clash = clashes[i]
          const errs = [
            ...(clash !== undefined ? [`Another field is already named “${clash}”`] : []),
            ...errors(`properties.${name}`),
          ]
          return (
            <motion.div
              // Keyed by position: renames keep a field's slot, so focus survives typing in the name.
              key={i}
              layout
              initial={{ opacity: 0, y: -6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.98 }}
              transition={spring}
              className={cn('flex flex-col gap-2 rounded-[12px] border bg-card p-2.5', errs.length ? 'border-danger/40' : 'border-border-subtle')}
            >
              <div className="flex items-center gap-1.5">
                <input
                  aria-label="Field name"
                  value={clash ?? name}
                  spellCheck={false}
                  aria-invalid={clash !== undefined}
                  onChange={(e) => rename(i, name, e.target.value)}
                  onBlur={() => clash !== undefined && setClashes(({ [i]: _dropped, ...rest }) => rest)}
                  className={cn(controlClass(clash !== undefined), 'h-8 w-auto! min-w-0 flex-1 font-mono text-[12.5px]')}
                />
                <Select
                  aria-label="Field type"
                  value={schema.type ?? 'string'}
                  onChange={(e) => patch(name, { ...schema, type: e.target.value })}
                  className="h-8 w-[104px]! shrink-0 text-[12.5px]"
                >
                  {FIELD_TYPES.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                  {schema.type && !(FIELD_TYPES as readonly string[]).includes(schema.type) && <option>{schema.type}</option>}
                </Select>
                <label
                  title="Required"
                  className={cn(
                    'flex h-8 shrink-0 cursor-pointer items-center gap-1.5 rounded-[8px] px-2 text-[12px] font-medium transition-colors duration-150',
                    isRequired ? 'bg-accent-soft text-accent' : 'text-muted hover:bg-raised',
                  )}
                >
                  <input
                    type="checkbox"
                    checked={isRequired}
                    onChange={(e) =>
                      onChange({
                        properties,
                        required: e.target.checked ? [...required, name] : required.filter((r) => r !== name),
                      })
                    }
                    className="size-3.5 accent-accent"
                  />
                  Req.
                </label>
                <IconButton label={`Remove field ${name}`} onClick={() => remove(name)} className="size-7 hover:text-danger">
                  <Trash2 className="size-3.5" />
                </IconButton>
              </div>
              <input
                aria-label={`Description of ${name}`}
                value={schema.description ?? ''}
                placeholder="What this field holds, e.g. “Caller’s date of birth”"
                onChange={(e) => patch(name, { ...schema, description: e.target.value }, 'desc')}
                className={cn(controlClass(), 'h-8 text-[12.5px]')}
              />
              {(schema.type ?? 'string') === 'string' && (
                <ChipsInput
                  label={`Allowed values for ${name}`}
                  values={(schema.enum ?? []).map(String)}
                  placeholder="Allowed values (optional) — type and press Enter"
                  onChange={(values) => {
                    const { enum: _enum, ...rest } = schema
                    patch(name, values.length ? { ...rest, enum: values } : rest)
                  }}
                />
              )}
              <FieldErrors errors={errs} />
            </motion.div>
          )
        })}
      </AnimatePresence>
      {entries.length === 0 && (
        <p className="rounded-[12px] border border-dashed border-border px-3.5 py-3 text-[12.5px] leading-snug text-muted">
          No fields. Add one to have the agent collect structured data when it takes this transition.
        </p>
      )}
      <FieldErrors errors={errors('required')} />
      <Button size="sm" variant="ghost" className="self-start" icon={<Plus className="size-3.5" />} onClick={add}>
        Add field
      </Button>
    </div>
  )
}

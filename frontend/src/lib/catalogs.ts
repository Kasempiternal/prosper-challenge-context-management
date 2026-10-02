import { useEffect, useState } from 'react'
import type { CatalogSummary } from '../types/agent'
import { api } from './api'

/** The LLM context window the naive "whole catalog in the prompt" baseline is compared against. */
export const CONTEXT_WINDOW_TOKENS = 128_000
/** Measured once for the SF clinic catalog; used only when the selected catalog has no sidecar. */
const FALLBACK_NAIVE_TOKENS = 8_400

const plural = (n: number, one: string, many: string) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`

/** "40 metros · 300 sites · 5,000 doctors"; a single-metro catalog leaves the metro out. */
export function catalogCounts(c: CatalogSummary): string {
  return [
    ...(c.metros > 1 ? [plural(c.metros, 'metro', 'metros')] : []),
    plural(c.locations, 'site', 'sites'),
    plural(c.providers, 'doctor', 'doctors'),
  ].join(' · ')
}

/** 950 -> "950", 8412 -> "8.4k", 651234 -> "651k", 1_240_000 -> "1.2M". */
export function formatTokens(n: number): string {
  if (n < 1_000) return String(n)
  if (n < 10_000) return `${(n / 1_000).toFixed(1).replace(/\.0$/, '')}k`
  if (n < 999_500) return `${Math.round(n / 1_000)}k`
  return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`
}

export interface Baseline {
  label: string
  exceedsContext: boolean
}

export function naiveBaseline(naiveTokens: number | null | undefined): Baseline {
  const tokens = naiveTokens ?? FALLBACK_NAIVE_TOKENS
  const exceedsContext = tokens > CONTEXT_WINDOW_TOKENS
  const label = `naive prompt ≈ ${formatTokens(tokens)} tok/turn`
  return { label: exceedsContext ? `${label} — exceeds ${formatTokens(CONTEXT_WINDOW_TOKENS)} context` : label, exceedsContext }
}

export interface CatalogOption {
  value: string
  label: string
}

/** Select options (counts go on their own line under the select: they would truncate inside a narrow one);
 *  a path the server does not list (hand-edited JSON, deleted file) stays selectable as itself. */
export function catalogOptions(catalogs: CatalogSummary[], current: string): CatalogOption[] {
  const listed = catalogs.map((c) => ({ value: c.path, label: c.label }))
  return catalogs.some((c) => c.path === current) ? listed : [{ value: current, label: current || '—' }, ...listed]
}

let catalogsRequest: Promise<CatalogSummary[]> | null = null
function loadCatalogs(): Promise<CatalogSummary[]> {
  catalogsRequest ??= api.listCatalogs().catch((err) => {
    catalogsRequest = null
    throw err
  })
  return catalogsRequest
}

/** Catalog summaries from /api/catalogs, fetched once per page; null until loaded, [] if unavailable. */
export function useCatalogs(): CatalogSummary[] | null {
  const [catalogs, setCatalogs] = useState<CatalogSummary[] | null>(null)
  useEffect(() => {
    loadCatalogs().then(setCatalogs, () => setCatalogs([]))
  }, [])
  return catalogs
}

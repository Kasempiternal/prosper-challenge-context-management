import { describe, expect, it } from 'vitest'
import type { CatalogSummary } from '../types/agent'
import { parseCatalogs } from './api'
import { catalogCounts, catalogOptions, formatTokens, naiveBaseline } from './catalogs'

const SF: CatalogSummary = {
  path: 'data/catalog.json',
  label: 'San Francisco clinics',
  locations: 8,
  providers: 50,
  appointment_types: 82,
  metros: 1,
  naive_tokens: 8412,
}
const NATIONAL: CatalogSummary = {
  path: 'data/national/catalog.json',
  label: 'National (synthetic, 40 metros)',
  locations: 300,
  providers: 5000,
  appointment_types: 300,
  metros: 40,
  naive_tokens: 651_234,
}

describe('catalogCounts', () => {
  it('names metros, sites and doctors with thousands separators', () => {
    expect(catalogCounts(NATIONAL)).toBe('40 metros · 300 sites · 5,000 doctors')
  })
  it('leaves the metro out of a single-metro catalog', () => {
    expect(catalogCounts(SF)).toBe('8 sites · 50 doctors')
  })
  it('uses the singular for one', () => {
    expect(catalogCounts({ ...SF, locations: 1, providers: 1 })).toBe('1 site · 1 doctor')
  })
})

describe('formatTokens', () => {
  it.each([
    [950, '950'],
    [8412, '8.4k'],
    [8000, '8k'],
    [651_234, '651k'],
    [128_000, '128k'],
    [1_240_000, '1.2M'],
  ])('%d -> %s', (n, label) => {
    expect(formatTokens(n)).toBe(label)
  })
})

describe('naiveBaseline', () => {
  it('flags a catalog that does not fit in a 128k context', () => {
    expect(naiveBaseline(651_234)).toEqual({ label: 'naive prompt ≈ 651k tok/turn — exceeds 128k context', exceedsContext: true })
  })
  it('shows a catalog that fits without the warning', () => {
    expect(naiveBaseline(8412)).toEqual({ label: 'naive prompt ≈ 8.4k tok/turn', exceedsContext: false })
  })
  it('falls back to the measured SF figure when the catalog has no sidecar', () => {
    expect(naiveBaseline(null)).toEqual({ label: 'naive prompt ≈ 8.4k tok/turn', exceedsContext: false })
    expect(naiveBaseline(undefined)).toEqual({ label: 'naive prompt ≈ 8.4k tok/turn', exceedsContext: false })
  })
})

describe('catalogOptions', () => {
  it('offers each listed catalog by label', () => {
    expect(catalogOptions([SF, NATIONAL], 'data/national/catalog.json')).toEqual([
      { value: 'data/catalog.json', label: 'San Francisco clinics' },
      { value: 'data/national/catalog.json', label: 'National (synthetic, 40 metros)' },
    ])
  })
  it('keeps an unlisted current path selectable', () => {
    expect(catalogOptions([SF], 'data/old.json').map((o) => o.value)).toEqual(['data/old.json', 'data/catalog.json'])
  })
})

describe('parseCatalogs', () => {
  it('keeps well-formed entries, nulls a missing naive_tokens, drops malformed ones', () => {
    const { naive_tokens: _drop, ...noSidecar } = SF
    expect(parseCatalogs([noSidecar, NATIONAL, { path: 'x', locations: 'many' }, null])).toEqual([
      { ...SF, naive_tokens: null },
      NATIONAL,
    ])
  })
  it('rejects a non-list body', () => {
    expect(() => parseCatalogs({})).toThrow('malformed catalog list')
  })
})

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Provider } from '../lib/keyProviders'
import type { AgentConfig } from '../types/agent'

/** The store as a page load sees it: a fresh module over whatever localStorage holds. */
async function load() {
  vi.resetModules()
  return import('./keys')
}

function respond(status: number, body: unknown) {
  const fetch = vi.fn(async (..._args: unknown[]) => new Response(JSON.stringify(body), { status }))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const PROVIDERS: Provider[] = ['openai', 'elevenlabs', 'jev']
const KEY: Record<Provider, string> = { openai: 'sk-proj-test-0000abcd', elevenlabs: 'sk_el_test_0000wxyz', jev: 'cc-test-0000qrst' }
const NONE = { openai: null, elevenlabs: null, jev: null }
const ALL_SERVER = { openai: true, elevenlabs: true, jev: true }
const NO_SERVER = { openai: false, elevenlabs: false, jev: false }

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

describe.each(PROVIDERS)('the browser %s key', (p) => {
  it('survives a reload under its namespaced key, trimmed, alone', async () => {
    const first = await load()
    first.useKeys.getState().save(p, `  ${KEY[p]}\n`)
    expect(localStorage.getItem(`agent-studio:${p}-key`)).toBe(KEY[p])
    expect(localStorage.length).toBe(1)

    const { browser } = (await load()).useKeys.getState()
    expect(browser).toEqual({ ...NONE, [p]: KEY[p] })
  })

  it('is gone after Remove, also after a reload, and the others stay', async () => {
    for (const q of PROVIDERS) localStorage.setItem(`agent-studio:${q}-key`, KEY[q])
    const { useKeys } = await load()
    useKeys.getState().remove(p)
    expect(useKeys.getState().browser[p]).toBeNull()
    expect(localStorage.getItem(`agent-studio:${p}-key`)).toBeNull()
    const after = (await load()).useKeys.getState().browser
    expect(after[p]).toBeNull()
    expect(PROVIDERS.filter((q) => after[q] !== null)).toEqual(PROVIDERS.filter((q) => q !== p))
  })

  it('ignores a blank save and a blank stored value', async () => {
    localStorage.setItem(`agent-studio:${p}-key`, '   ')
    const { useKeys } = await load()
    expect(useKeys.getState().browser[p]).toBeNull()
    useKeys.getState().save(p, '  ')
    expect(useKeys.getState().browser[p]).toBeNull()
    expect(localStorage.getItem(`agent-studio:${p}-key`)).toBe('   ')
  })

  it('testing -> verified, the key in the X-Api-Key header only, the provider in the query', async () => {
    const { useKeys, checkFor } = await load()
    const fetch = respond(200, { ok: true, ms: 412 })
    const done = useKeys.getState().test(p, ` ${KEY[p]} `)
    expect(useKeys.getState().checks[p]).toEqual({ status: 'testing', key: KEY[p] })
    await done
    expect(useKeys.getState().checks).toEqual({ ...NONE, [p]: { status: 'verified', key: KEY[p], ms: 412, limited: false } })

    const [url, init] = fetch.mock.calls[0] as [string, RequestInit]
    expect(url).toBe(`/api/keys/test?provider=${p}`)
    expect(init.method).toBe('POST')
    expect(init.headers).toEqual({ 'X-Api-Key': KEY[p] })
    expect(init.body).toBeUndefined()
    expect(checkFor(useKeys.getState().checks[p], `${KEY[p]} `)?.status).toBe('verified')
    expect(checkFor(useKeys.getState().checks[p], 'sk-other')).toBeNull()
  })
})

describe('test', () => {
  it('a restricted key is verified with limited permissions', async () => {
    const { useKeys } = await load()
    respond(200, { ok: true, ms: 230, limited: true })
    await useKeys.getState().test('elevenlabs', KEY.elevenlabs)
    expect(useKeys.getState().checks.elevenlabs).toEqual({ status: 'verified', key: KEY.elevenlabs, ms: 230, limited: true })
  })

  it.each([
    [200, { ok: false, ms: 380, error: 'invalid key' }, 'invalid key'],
    [200, { ok: false, ms: 15002, error: 'network' }, 'network'],
    [429, { ok: false, ms: 0, error: 'busy' }, 'busy'],
    [502, 'Bad Gateway', 'backend'],
  ])('HTTP %i %j fails with %s', async (status, body, error) => {
    const { useKeys } = await load()
    respond(status, body)
    await useKeys.getState().test('openai', KEY.openai)
    expect(useKeys.getState().checks.openai).toEqual({ status: 'failed', key: KEY.openai, error })
  })

  it('an unreachable backend is "backend", not a thrown error', async () => {
    const { useKeys } = await load()
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
    await useKeys.getState().test('jev', KEY.jev)
    expect(useKeys.getState().checks.jev).toEqual({ status: 'failed', key: KEY.jev, error: 'backend' })
  })

  it('a result for a key no longer under test is dropped; other providers keep theirs', async () => {
    const { useKeys } = await load()
    respond(200, { ok: true, ms: 412 })
    await useKeys.getState().test('jev', KEY.jev)
    const first = useKeys.getState().test('openai', 'sk-first-0000')
    const second = useKeys.getState().test('openai', 'sk-second-0000')
    await Promise.all([first, second])
    expect(useKeys.getState().checks.openai).toEqual({ status: 'verified', key: 'sk-second-0000', ms: 412, limited: false })
    expect(useKeys.getState().checks.jev?.status).toBe('verified')
  })
})

describe('loadServerStatus', () => {
  it('reads only the booleans, once per page load', async () => {
    const { useKeys } = await load()
    const fetch = respond(200, { openai: { server_key: true }, elevenlabs: { server_key: false }, jev: { server_key: 'yes' } })
    expect(useKeys.getState().server).toBeNull()
    await useKeys.getState().loadServerStatus()
    await useKeys.getState().loadServerStatus()
    expect(useKeys.getState().server).toEqual({ openai: true, elevenlabs: false, jev: false })
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0][0]).toBe('/api/keys/status')
  })

  it('a failed status request counts as no server keys', async () => {
    const { useKeys } = await load()
    respond(500, 'boom')
    await useKeys.getState().loadServerStatus()
    expect(useKeys.getState().server).toEqual(NO_SERVER)
  })
})

describe('the sheet', () => {
  it('opens on a row, or none, and closes', async () => {
    const { useKeys } = await load()
    useKeys.getState().openSheet('elevenlabs')
    expect(useKeys.getState().sheet).toEqual({ focus: 'elevenlabs' })
    useKeys.getState().openSheet()
    expect(useKeys.getState().sheet).toEqual({ focus: null })
    useKeys.getState().closeSheet()
    expect(useKeys.getState().sheet).toBeNull()
  })
})

describe('maskKey', () => {
  it('shows the last four characters only, behind four dots', async () => {
    const { maskKey } = await load()
    expect(maskKey('sk-test-0000abcd')).toBe('••••abcd')
    expect(maskKey('123456789')).toBe('••••6789')
  })

  it('shows nothing of a key of eight characters or fewer', async () => {
    const { maskKey } = await load()
    expect(maskKey('abcd1234')).toBe('••••')
    expect(maskKey('abc')).toBe('••••')
  })

  it('is the same length for every key, so it never tells a key length', async () => {
    const { maskKey } = await load()
    expect(new Set(['sk-a-much-longer-key-0123456789abcd', 'sk-short-9z', KEY.jev].map((k) => maskKey(k).length))).toEqual(new Set([8]))
  })
})

const agent = (resolver: AgentConfig['resolver'], catalog: string | null = 'data/catalog.json') => ({ catalog: catalog ?? undefined, resolver })

describe('key sources, health and the call gate', () => {
  it.each([
    [{ ...NONE }, null, 'unknown'],
    [{ ...NONE }, NO_SERVER, 'missing'],
    [{ ...NONE }, ALL_SERVER, 'server'],
    [{ ...NONE, openai: 'k' }, null, 'browser'],
    [{ ...NONE, openai: 'k' }, NO_SERVER, 'browser'],
  ])('browser %j, server %j: OpenAI is %s', async (browser, server, source) => {
    const { keySource } = await load()
    expect(keySource({ browser, server }, 'openai')).toBe(source)
  })

  it.each([
    ['ready', { ...NONE }, ALL_SERVER],
    ['ready', { openai: 'a', elevenlabs: 'b', jev: 'c' }, NO_SERVER],
    ['ready', { ...NONE, jev: 'c' }, { openai: true, elevenlabs: true, jev: false }],
    ['no-jev', { ...NONE }, { openai: true, elevenlabs: true, jev: false }],
    ['no-jev', { openai: 'a', elevenlabs: 'b', jev: null }, NO_SERVER],
    ['blocked', { ...NONE }, NO_SERVER],
    ['blocked', { ...NONE, openai: 'a', jev: 'c' }, NO_SERVER],
    ['blocked', { ...NONE }, { openai: false, elevenlabs: true, jev: true }],
    ['unknown', { ...NONE }, null],
    ['unknown', { openai: 'a', elevenlabs: 'b', jev: null }, null],
  ] as const)('%s: browser %j, server %j', async (health, browser, server) => {
    const { keyHealth } = await load()
    expect(keyHealth({ browser, server })).toBe(health)
  })

  it.each([
    ['no keys anywhere', { ...NONE }, NO_SERVER, agent({ chooser: 'none' }), { kind: 'missing', providers: ['openai', 'elevenlabs'] }],
    ['ElevenLabs missing', { ...NONE, openai: 'a' }, NO_SERVER, agent({ chooser: 'none' }), { kind: 'missing', providers: ['elevenlabs'] }],
    [
      'the OpenAI disambiguator with no OpenAI key',
      { ...NONE, elevenlabs: 'b' },
      NO_SERVER,
      agent({ chooser: 'openai' }),
      { kind: 'missing', providers: ['openai'] },
    ],
    ['JEV picked with no JEV key', { ...NONE }, { ...ALL_SERVER, jev: false }, agent({ chooser: 'jev' }), { kind: 'keyless-jev' }],
    ['an absent chooser is JEV', { ...NONE }, { ...ALL_SERVER, jev: false }, agent({}), { kind: 'keyless-jev' }],
    ['JEV key in this browser', { ...NONE, jev: 'c' }, { ...ALL_SERVER, jev: false }, agent({ chooser: 'jev' }), { kind: 'ready' }],
    ['no catalog: JEV is never consulted', { ...NONE }, { ...ALL_SERVER, jev: false }, agent({ chooser: 'jev' }, null), { kind: 'ready' }],
    ['OpenAI picked, no JEV key', { ...NONE }, { ...ALL_SERVER, jev: false }, agent({ chooser: 'openai' }), { kind: 'ready' }],
    ['server status not known yet', { ...NONE }, null, agent({ chooser: 'jev' }), { kind: 'ready' }],
  ] as const)('%s', async (_case, browser, server, a, gate) => {
    const { callGate } = await load()
    expect(callGate({ browser, server }, a)).toEqual(gate)
  })
})

describe('callKeys', () => {
  const all = { openai: KEY.openai, elevenlabs: KEY.elevenlabs, jev: KEY.jev }

  it('the browser keys by env var name, JEV only when the call consults JEV', async () => {
    const { callKeys } = await load()
    expect(callKeys(all, agent({ chooser: 'jev' }))).toEqual({
      OPENAI_API_KEY: KEY.openai,
      ELEVENLABS_API_KEY: KEY.elevenlabs,
      CMD_API_KEY: KEY.jev,
    })
    expect(callKeys(all, agent({ chooser: 'openai' }))).toEqual({ OPENAI_API_KEY: KEY.openai, ELEVENLABS_API_KEY: KEY.elevenlabs })
    expect(callKeys(all, agent({ chooser: 'jev' }, null))).toEqual({ OPENAI_API_KEY: KEY.openai, ELEVENLABS_API_KEY: KEY.elevenlabs })
  })

  it('nothing for keys the browser does not hold', async () => {
    const { callKeys } = await load()
    expect(callKeys({ ...NONE }, agent({ chooser: 'jev' }))).toEqual({})
    expect(callKeys({ ...NONE, elevenlabs: KEY.elevenlabs }, agent({}))).toEqual({ ELEVENLABS_API_KEY: KEY.elevenlabs })
  })
})

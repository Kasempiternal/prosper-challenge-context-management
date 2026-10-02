import { create } from 'zustand'
import type { Usage } from '../store/telemetry'

export interface Prices {
  openaiInPerM: number
  openaiOutPerM: number
  ttsPer1kChars: number
  sttPerHour: number
  jevPerM: number
}

/** gpt-4o list prices; ElevenLabs and JEV rates are estimates for the demo, editable in the UI. */
export const DEFAULT_PRICES: Prices = {
  openaiInPerM: 2.5,
  openaiOutPerM: 10,
  ttsPer1kChars: 0.3,
  sttPerHour: 0.4,
  jevPerM: 0.04,
}

export const PRICE_FIELDS: ReadonlyArray<{ key: keyof Prices; label: string; unit: string }> = [
  { key: 'openaiInPerM', label: 'OpenAI input', unit: '$ / M tok' },
  { key: 'openaiOutPerM', label: 'OpenAI output', unit: '$ / M tok' },
  { key: 'ttsPer1kChars', label: 'ElevenLabs TTS', unit: '$ / 1k chars' },
  { key: 'sttPerHour', label: 'ElevenLabs STT', unit: '$ / hour' },
  { key: 'jevPerM', label: 'JEV input', unit: '$ / M tok' },
]

export interface CostLine {
  key: 'openai' | 'tts' | 'stt' | 'jev'
  label: string
  detail: string
  usd: number
  estimate: boolean
}

const n = (v: number) => Math.round(v).toLocaleString('en-US')

export function costLines(usage: Usage, sttSeconds: number, prices: Prices): CostLine[] {
  return [
    {
      key: 'openai',
      label: 'OpenAI',
      detail: `${n(usage.promptTokens)} in · ${n(usage.completionTokens)} out`,
      usd: (usage.promptTokens * prices.openaiInPerM + usage.completionTokens * prices.openaiOutPerM) / 1e6,
      estimate: false,
    },
    {
      key: 'tts',
      label: 'ElevenLabs TTS',
      detail: `${n(usage.ttsChars)} chars`,
      usd: (usage.ttsChars / 1000) * prices.ttsPer1kChars,
      estimate: true,
    },
    {
      key: 'stt',
      label: 'ElevenLabs STT',
      detail: `${sttSeconds.toFixed(1)} s${usage.sttSeconds === null ? ' (call time)' : ''}`,
      usd: (sttSeconds / 3600) * prices.sttPerHour,
      estimate: true,
    },
    {
      key: 'jev',
      label: 'JEV',
      detail: `${n(usage.jevTokens)} tok · ${usage.jevCalls} ${usage.jevCalls === 1 ? 'call' : 'calls'}`,
      usd: (usage.jevTokens * prices.jevPerM) / 1e6,
      estimate: false,
    },
  ]
}

export const usd = (v: number) => `$${v.toFixed(4)}`

const STORAGE_KEY = 'agent-studio:prices'

function readPrices(): Prices {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}') as Record<string, unknown>
    const entries = PRICE_FIELDS.map(({ key }) => {
      const v = stored[key]
      return [key, typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : DEFAULT_PRICES[key]]
    })
    return Object.fromEntries(entries) as unknown as Prices
  } catch {
    return DEFAULT_PRICES
  }
}

interface PricingState {
  prices: Prices
  setPrice: (key: keyof Prices, value: number) => void
  resetPrices: () => void
}

export const usePricing = create<PricingState>()((set, get) => ({
  prices: readPrices(),
  setPrice: (key, value) => {
    const prices = { ...get().prices, [key]: value }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prices))
    set({ prices })
  },
  resetPrices: () => {
    localStorage.removeItem(STORAGE_KEY)
    set({ prices: DEFAULT_PRICES })
  },
}))

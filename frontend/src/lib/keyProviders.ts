/** The API keys a live call can use (backend api_keys.PROVIDERS), in the order the keys sheet lists them. */
export type Provider = 'openai' | 'elevenlabs' | 'jev'

export interface ProviderInfo {
  id: Provider
  label: string
  /** What a key is called: "Add your …". */
  keyName: string
  purpose: string
  /** The /start body's api_keys field and the backend's env var. */
  env: string
  /** A call cannot start without this key. */
  required: boolean
  url: string
  host: string
}

export const PROVIDERS: Record<Provider, ProviderInfo> = {
  openai: {
    id: 'openai',
    label: 'OpenAI',
    keyName: 'OpenAI API key',
    purpose: 'The conversation, and the OpenAI disambiguator.',
    env: 'OPENAI_API_KEY',
    required: true,
    url: 'https://platform.openai.com/api-keys',
    host: 'platform.openai.com',
  },
  elevenlabs: {
    id: 'elevenlabs',
    label: 'ElevenLabs',
    keyName: 'ElevenLabs API key',
    purpose: 'Speech to text and the agent’s voice.',
    env: 'ELEVENLABS_API_KEY',
    required: true,
    url: 'https://elevenlabs.io/app/settings/api-keys',
    host: 'elevenlabs.io',
  },
  jev: {
    id: 'jev',
    label: 'Command Code JEV',
    keyName: 'Command Code API key',
    purpose: 'The JEV disambiguator and the call review. Optional.',
    env: 'CMD_API_KEY',
    required: false,
    url: 'https://commandcode.ai',
    host: 'commandcode.ai',
  },
}

export const PROVIDER_IDS: readonly Provider[] = ['openai', 'elevenlabs', 'jev']

/** "OpenAI", "ElevenLabs", "JEV": the short name in running text. */
export const shortLabel = (p: Provider) => (p === 'jev' ? 'JEV' : PROVIDERS[p].label)

/** "OpenAI and ElevenLabs". */
export function joinLabels(providers: readonly Provider[]): string {
  const labels = providers.map(shortLabel)
  return labels.length > 1 ? `${labels.slice(0, -1).join(', ')} and ${labels.at(-1)}` : (labels[0] ?? '')
}

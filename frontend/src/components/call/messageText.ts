import type { BotOutputText, ConversationMessage } from '@pipecat-ai/client-react'
import { isValidElement } from 'react'

export function isBotText(text: unknown): text is BotOutputText {
  return typeof text === 'object' && text !== null && !isValidElement(text) && 'spoken' in text
}

export function plainText(message: ConversationMessage): string {
  return message.parts
    .map((part) => (typeof part.text === 'string' ? part.text : isBotText(part.text) ? part.text.spoken : ''))
    .join(' ')
    .trim()
}

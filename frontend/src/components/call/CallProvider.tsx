import { PipecatClient, RTVIEvent, type ErrorData } from '@pipecat-ai/client-js'
import { PipecatClientAudio, PipecatClientProvider, usePipecatClient, useRTVIClientEvent } from '@pipecat-ai/client-react'
import { SmallWebRTCTransport } from '@pipecat-ai/small-webrtc-transport'
import { useState, type ReactNode } from 'react'
import { REJECTED, parseFlowEvent, useCall } from '../../store/call'

/**
 * Mounted once at the app root so a live call (and its bot audio) survives the
 * test-call panel being hidden while the user inspects nodes.
 */
export function CallProvider({ children }: { children: ReactNode }) {
  const [client] = useState(
    () => new PipecatClient({ transport: new SmallWebRTCTransport(), enableMic: true, enableCam: false }),
  )
  return (
    <PipecatClientProvider client={client}>
      {children}
      <CallEvents />
      <PipecatClientAudio />
    </PipecatClientProvider>
  )
}

function CallEvents() {
  const client = usePipecatClient()

  useRTVIClientEvent(RTVIEvent.ServerMessage, (data: unknown) => {
    const event = parseFlowEvent(data)
    if (!event) return
    useCall.getState().ingest(event)
    if (event.type === 'call_ended') {
      useCall.getState().end()
      void client?.disconnect()
    }
  })
  useRTVIClientEvent(RTVIEvent.BotReady, () => useCall.getState().connected())
  useRTVIClientEvent(RTVIEvent.Disconnected, () => {
    // The bot ends the session before BotReady when AgentBuilder refuses the draft.
    const call = useCall.getState()
    call.end(call.status === 'connecting' ? REJECTED : undefined)
  })
  useRTVIClientEvent(RTVIEvent.Error, (message) => {
    const data = message.data as Partial<ErrorData> | undefined
    if (!data?.fatal) return
    useCall.getState().end({ message: data.error ?? data.message ?? 'The call hit an error.' })
    void client?.disconnect()
  })
  return null
}

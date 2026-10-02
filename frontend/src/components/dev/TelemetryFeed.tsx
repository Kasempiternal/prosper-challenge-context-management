import { usePipecatClient } from '@pipecat-ai/client-react'
import { useEffect } from 'react'
import { ADAPTED_EVENTS, toSignals, type TelemetrySignal } from '../../lib/telemetry'
import { useCall } from '../../store/call'
import { useTelemetry } from '../../store/telemetry'

const emit = (signals: TelemetrySignal[]) => {
  const at = Date.now()
  useTelemetry.getState().dispatch(signals.map((s) => ({ ...s, at })))
}

/** Feeds every RTVI event the dev view understands into the telemetry store, whether or not dev view is showing. */
export function TelemetryFeed() {
  const client = usePipecatClient()
  const attempt = useCall((s) => s.attempt)
  const status = useCall((s) => s.status)

  useEffect(() => {
    if (attempt > 0) emit([{ kind: 'reset' }])
  }, [attempt])

  useEffect(() => {
    if (status === 'live') emit([{ kind: 'call_live' }])
    if (status === 'ended') emit([{ kind: 'call_ended' }])
  }, [status])

  useEffect(() => {
    if (!client) return
    const offs = ADAPTED_EVENTS.map((event) => {
      const handler = (data: unknown) => emit(toSignals(event, data))
      client.on(event, handler)
      return () => void client.off(event, handler)
    })
    return () => offs.forEach((off) => off())
  }, [client])

  return null
}

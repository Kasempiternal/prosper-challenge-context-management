import { RTVIEvent, type DeviceErrorReason } from '@pipecat-ai/client-js'
import { usePipecatClient, usePipecatClientMicControl, useRTVIClientEvent } from '@pipecat-ai/client-react'
import { AnimatePresence, motion, useMotionValue, useSpring, useTransform, type MotionValue } from 'motion/react'
import { AlertTriangle, AudioLines, ChevronRight, Mic, MicOff, Phone, PhoneOff, RotateCcw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { cn } from '../../lib/cn'
import { callBlockedHint } from '../../lib/issues'
import { softSpring, spring } from '../../lib/motion'
import { clock } from '../../lib/time'
import { isCallActive, useCall, type CallError, type CallStatus } from '../../store/call'
import { useEditor } from '../../store/editor'
import { IconButton } from '../ui/Button'
import { PanelHeader } from '../inspector/Panel'
import { Tabs } from '../ui/Tabs'
import { CollectedData } from './CollectedData'
import { Decisions } from './Decisions'
import { Transcript } from './Transcript'

const STATUS_LABEL: Record<CallStatus, string> = {
  idle: 'Ready to test',
  connecting: 'Connecting…',
  live: 'Live',
  ended: 'Call ended',
}

const MIC_ERRORS: Record<DeviceErrorReason, CallError> = {
  blocked: {
    message: 'Microphone access is blocked.',
    hint: 'Click the site-settings icon at the left of the address bar, set Microphone to Allow, then call again.',
  },
  'already-in-use': {
    message: 'Your microphone is in use by another app.',
    hint: 'Close the app that is using it (a meeting or recorder), then call again.',
  },
  'not-found': { message: 'No microphone found.', hint: 'Plug one in or choose an input device in your system settings.' },
  'invalid-constraints': { message: 'The microphone could not be opened.', hint: 'Try another input device.' },
  'not-supported': {
    message: 'This browser can’t use a microphone here.',
    hint: 'Open the studio over http://localhost or HTTPS in a current browser.',
  },
  unknown: { message: 'The microphone could not be started.', hint: 'Check your input device and browser permissions.' },
}

export function TestCallPanel() {
  const client = usePipecatClient()
  const status = useCall((s) => s.status)
  const error = useCall((s) => s.error)
  const issueCount = useEditor((s) => (s.validation === 'valid' ? 0 : s.issues.length))
  const { setRightPanel, setIssuesOpen } = useEditor.getState()
  const userLevel = useMotionValue(0)
  const botLevel = useMotionValue(0)

  useRTVIClientEvent(RTVIEvent.LocalAudioLevel, (level) => userLevel.set(level))
  useRTVIClientEvent(RTVIEvent.RemoteAudioLevel, (level) => botLevel.set(level))

  const blocked = !isCallActive(status) && issueCount > 0
  const decisionCount = useCall((s) => s.decisions.length)
  const scheduling = useEditor((s) => typeof s.doc?.agent.catalog === 'string')
  const [tab, setTab] = useState<'transcript' | 'decisions'>('transcript')
  const showDecisions = scheduling || decisionCount > 0

  const start = async () => {
    const agent = useEditor.getState().doc?.agent
    if (!client || !agent || blocked || isCallActive(useCall.getState().status)) return
    const attempt = useCall.getState().begin()
    // A hang-up (or a newer attempt) while awaiting makes the rest of this one moot.
    const superseded = () => {
      const call = useCall.getState()
      return call.attempt !== attempt || call.status !== 'connecting'
    }

    // A denied mic surfaces in mediaState (the transport emits a device error instead of rejecting).
    await client.initDevices().catch(() => undefined)
    if (superseded()) return
    const mic = client.mediaState.mic
    if (mic.state === 'error') {
      useCall.getState().end(MIC_ERRORS[mic.reason])
      return
    }

    try {
      // The runner's /start returns {sessionId, iceConfig}; the transport then posts its
      // offer to /sessions/{sessionId}/api/offer (derived from this endpoint).
      // JSON round-trip: a detached snapshot typed as the client's Serializable.
      await client.startBotAndConnect({
        endpoint: '/start',
        requestData: { transport: 'webrtc', body: { agent: JSON.parse(JSON.stringify(agent)) } },
      })
      // Hung up while the transport was still negotiating: make sure nothing stays connected.
      if (!isCallActive(useCall.getState().status)) void client.disconnect()
    } catch (err) {
      if (superseded()) return
      useCall.getState().end({ message: err instanceof Error ? err.message : 'Could not start the call.' })
      void client.disconnect()
    }
  }

  const hangUp = () => {
    useCall.getState().end()
    void client?.disconnect()
  }

  return (
    <>
      <PanelHeader
        icon={<AudioLines className="size-4" />}
        eyebrow="Test call"
        title={<StatusLine status={status} />}
        onClose={() => setRightPanel('inspector')}
      />
      <div className="h-px bg-border-subtle" />

      <div className="flex flex-col items-center gap-5 px-5 pt-6 pb-5">
        <div className="flex w-full items-center justify-center gap-8">
          <LevelMeter label="You" level={userLevel} active={status === 'live'} />
          <CallButton status={status} blocked={blocked} onStart={start} onHangUp={hangUp} />
          <LevelMeter label="Agent" level={botLevel} active={status === 'live'} accent />
        </div>
        {blocked ? (
          <button
            type="button"
            onClick={() => setIssuesOpen(true)}
            className="flex items-center gap-1.5 rounded-full border border-danger/30 bg-danger-soft py-1 pr-2 pl-3 text-[12.5px] font-medium text-danger transition-colors duration-150 hover:border-danger/55"
          >
            <AlertTriangle className="size-3.5" aria-hidden />
            {callBlockedHint(issueCount)}
            <ChevronRight className="size-3.5" aria-hidden />
          </button>
        ) : (
          <CallCaption status={status} error={error} />
        )}
        {status === 'live' && <MicToggle />}
      </div>

      <div className="flex min-h-0 flex-1 flex-col border-t border-border-subtle bg-surface/60">
        {showDecisions && (
          <div className="pt-3">
            <Tabs
              value={tab}
              onChange={setTab}
              tabs={[
                { value: 'transcript', label: 'Transcript' },
                { value: 'decisions', label: 'Decisions', count: decisionCount },
              ]}
            />
          </div>
        )}
        {showDecisions && tab === 'decisions' ? <Decisions /> : <Transcript />}
        <CollectedData />
      </div>
    </>
  )
}

function StatusLine({ status }: { status: CallStatus }) {
  const startedAt = useCall((s) => s.startedAt)
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (status !== 'live') return
    const t = window.setInterval(() => setNow(Date.now()), 500)
    return () => window.clearInterval(t)
  }, [status])
  return (
    <span className="flex items-center gap-2">
      {status === 'live' && <span className="size-2 animate-pulse rounded-full bg-accent" />}
      {STATUS_LABEL[status]}
      {status === 'live' && startedAt && (
        <span className="font-mono text-[13px] font-medium text-muted tabular-nums">{clock(now - startedAt)}</span>
      )}
    </span>
  )
}

interface CallButtonProps {
  status: CallStatus
  blocked: boolean
  onStart: () => void
  onHangUp: () => void
}

function CallButton({ status, blocked, onStart, onHangUp }: CallButtonProps) {
  const live = isCallActive(status)
  return (
    <div className="relative flex size-[88px] items-center justify-center">
      {status === 'connecting' && (
        <motion.span
          className="absolute inset-0 rounded-full border-2 border-accent/50"
          animate={{ scale: [1, 1.25], opacity: [0.8, 0] }}
          transition={{ duration: 1.2, repeat: Infinity, ease: 'easeOut' }}
        />
      )}
      <motion.button
        type="button"
        aria-label={status === 'connecting' ? 'Cancel call' : live ? 'Hang up' : 'Start test call'}
        disabled={blocked}
        onClick={live ? onHangUp : onStart}
        whileHover={blocked ? undefined : { scale: 1.04 }}
        whileTap={blocked ? undefined : { scale: 0.94 }}
        animate={{ borderRadius: live ? '28px' : '44px', width: live ? 76 : 88, height: live ? 76 : 88 }}
        transition={spring}
        className={cn(
          'relative flex items-center justify-center transition-[background-color,color,box-shadow] duration-300',
          blocked
            ? 'cursor-not-allowed border border-border bg-raised text-faint'
            : live
              ? 'bg-danger text-danger-ink'
              : 'bg-accent text-accent-ink shadow-accent',
        )}
      >
        <AnimatePresence mode="popLayout" initial={false}>
          <motion.span
            key={live ? 'off' : 'on'}
            initial={{ rotate: live ? -90 : 90, opacity: 0, scale: 0.6 }}
            animate={{ rotate: 0, opacity: 1, scale: 1 }}
            exit={{ rotate: live ? 90 : -90, opacity: 0, scale: 0.6 }}
            transition={softSpring}
          >
            {live ? <PhoneOff className="size-7" /> : status === 'ended' ? <RotateCcw className="size-7" /> : <Phone className="size-7" />}
          </motion.span>
        </AnimatePresence>
      </motion.button>
    </div>
  )
}

function LevelMeter({ label, level, active, accent }: { label: string; level: MotionValue<number>; active: boolean; accent?: boolean }) {
  const smooth = useSpring(level, { stiffness: 300, damping: 24 })
  return (
    <div className="flex w-14 flex-col items-center gap-2">
      <div className="flex h-10 items-center gap-[3px]" aria-hidden>
        {[0.55, 0.85, 1, 0.75, 0.5].map((weight, i) => (
          <Bar key={i} level={smooth} weight={weight} active={active} accent={accent} />
        ))}
      </div>
      <span className="text-[11.5px] font-medium text-muted">{label}</span>
    </div>
  )
}

function Bar({ level, weight, active, accent }: { level: MotionValue<number>; weight: number; active: boolean; accent?: boolean }) {
  const height = useTransform(level, (v) => `${Math.max(14, Math.min(100, v * 260 * weight))}%`)
  return (
    <motion.span
      style={{ height: active ? height : '14%' }}
      className={cn('w-[5px] rounded-full transition-colors duration-300', active ? (accent ? 'bg-accent' : 'bg-ink') : 'bg-border')}
    />
  )
}

function CallCaption({ status, error }: { status: CallStatus; error: CallError | null }) {
  const reason = useCall((s) => s.endReason)
  const text = error
    ? error.message
    : status === 'idle'
      ? 'Talk to the current draft — unsaved edits included. Uses your microphone.'
      : status === 'connecting'
        ? 'Starting the agent and negotiating audio…'
        : status === 'live'
          ? 'Speak naturally. The canvas follows the conversation.'
          : reason === 'end_node'
            ? 'The agent reached an end node and hung up. Press to call again.'
            : 'Press to call again.'
  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        key={text}
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -4 }}
        transition={{ duration: 0.18 }}
        role={error ? 'alert' : undefined}
        className="flex max-w-[300px] flex-col items-center gap-1 text-center"
      >
        <p className={cn('text-[12.5px] leading-snug', error ? 'font-medium text-danger' : 'text-muted')}>{text}</p>
        {error?.hint && <p className="text-[12px] leading-snug text-muted">{error.hint}</p>}
      </motion.div>
    </AnimatePresence>
  )
}

function MicToggle() {
  const { enableMic, isMicEnabled } = usePipecatClientMicControl()
  return (
    <IconButton
      label={isMicEnabled ? 'Mute microphone' : 'Unmute microphone'}
      onClick={() => enableMic(!isMicEnabled)}
      active={!isMicEnabled}
      className="border border-border bg-card shadow-card"
    >
      {isMicEnabled ? <Mic className="size-4" /> : <MicOff className="size-4 text-danger" />}
    </IconButton>
  )
}

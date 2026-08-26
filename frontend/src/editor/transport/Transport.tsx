'use client'

/**
 * The transport — under the picture, where it belongs.
 *
 * M4.5 item 2: the play button was in the application header at the top of the
 * window, the picture it plays is in the middle, and the playhead it moves is at
 * the bottom. *"Nothing connects the control to either."* It was in the header
 * because the header existed, which is how controls end up where they are.
 *
 * Two things came with the move, from the same item:
 *
 * * **Frame-step, jump-to-start and jump-to-end already existed** as keyboard
 *   shortcuts (`←`, `→`, `Home`, `End`) with no visible control. A shortcut with
 *   nothing on screen is a feature only its author knows about, so each one now
 *   has a button, and each button names its shortcut in the tooltip.
 * * **The timecode moved with it.** Reading position and duration next to the
 *   control that changes them is the point; in the header they were next to the
 *   account email.
 *
 * The keyboard path is unchanged and is still the one in `editor/keyboard.ts` —
 * these buttons call the same store actions, so there is one behaviour with two
 * ways in rather than two implementations to keep in agreement.
 */

import {
  IconPause,
  IconPlay,
  IconSkipEnd,
  IconSkipStart,
  IconStepBack,
  IconStepForward,
} from '@/editor/icons'
import { NUDGE_MS } from '@/editor/keyboard'
import { selectDurationMs, useEditor } from '@/editor/state/store'
import { formatTimecode } from '@/editor/timeline/scale'

export function Transport() {
  const isPlaying = useEditor((state) => state.isPlaying)
  const playheadMs = useEditor((state) => state.playheadMs)
  const durationMs = useEditor(selectDurationMs)
  const empty = durationMs === 0

  const store = useEditor.getState

  return (
    <div
      className="flex shrink-0 items-center justify-center gap-1 px-3 py-1.5"
      style={{ borderTop: '1px solid var(--color-rule)', background: 'var(--color-surface-2)' }}
      data-testid="transport"
    >
      <span
        className="tnum mr-auto pl-1"
        style={{ color: 'var(--color-ink)', fontSize: 'var(--text-sm, 12px)' }}
        data-testid="transport-position"
      >
        {formatTimecode(playheadMs, { withMillis: true })}
      </span>

      <TransportButton
        label="Jump to start"
        shortcut="Home"
        disabled={empty}
        onClick={() => store().setPlayhead(0)}
        testId="go-start"
      >
        <IconSkipStart size={15} />
      </TransportButton>

      <TransportButton
        label="Back one frame"
        shortcut="←"
        disabled={empty}
        onClick={() => store().setPlayhead(store().playheadMs - NUDGE_MS)}
        testId="step-back"
      >
        <IconStepBack size={15} />
      </TransportButton>

      {/* The one control with a fill: charter §8 wants the primary action to be
          the only accented thing in its group. */}
      <button
        type="button"
        onClick={() => store().setPlaying(!isPlaying)}
        disabled={empty}
        title={`${isPlaying ? 'Pause' : 'Play'} · Space`}
        aria-label={isPlaying ? 'Pause' : 'Play'}
        className="mx-1 flex h-8 w-8 items-center justify-center disabled:opacity-40"
        style={{
          borderRadius: 'var(--radius-pill)',
          background: 'var(--color-accent)',
          color: 'var(--color-accent-ink)',
          transition: 'background var(--duration-micro) ease-out',
        }}
        data-testid="play"
        data-playing={isPlaying}
      >
        {isPlaying ? <IconPause size={15} aria-hidden="true" /> : <IconPlay size={15} aria-hidden="true" />}
      </button>

      <TransportButton
        label="Forward one frame"
        shortcut="→"
        disabled={empty}
        onClick={() => store().setPlayhead(store().playheadMs + NUDGE_MS)}
        testId="step-forward"
      >
        <IconStepForward size={15} />
      </TransportButton>

      <TransportButton
        label="Jump to end"
        shortcut="End"
        disabled={empty}
        onClick={() => store().setPlayhead(selectDurationMs(store()))}
        testId="go-end"
      >
        <IconSkipEnd size={15} />
      </TransportButton>

      <span
        className="tnum ml-auto pr-1"
        style={{ color: 'var(--color-ink-3)', fontSize: 'var(--text-sm, 12px)' }}
        data-testid="transport-duration"
      >
        {formatTimecode(durationMs, { withMillis: true })}
      </span>
    </div>
  )
}

function TransportButton({
  label,
  shortcut,
  disabled,
  onClick,
  testId,
  children,
}: {
  label: string
  shortcut: string
  disabled?: boolean
  onClick: () => void
  testId: string
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      // The shortcut is in the tooltip rather than beside the icon: naming it on
      // screen would double the width of the transport to teach something once.
      title={`${label} · ${shortcut}`}
      aria-label={label}
      aria-keyshortcuts={shortcut}
      className="flex h-7 w-7 items-center justify-center disabled:opacity-40"
      style={{
        borderRadius: 'var(--radius-sm)',
        color: 'var(--color-ink-2)',
        transition: 'background var(--duration-micro) ease-out',
      }}
      data-testid={testId}
    >
      <span aria-hidden="true">{children}</span>
    </button>
  )
}

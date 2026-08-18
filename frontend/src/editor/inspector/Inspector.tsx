'use client'

/**
 * The inspector — clip properties, contract §4.2 and scope §3.3.
 *
 * Every control here writes through an operation in `editor/state/operations.ts`
 * that already clamps to the range the contract allows, so this file validates
 * nothing itself. That is deliberate: a range enforced in a component is a range
 * that only holds while that component is the way in, and M4's tools are
 * another way in.
 *
 * Charter §8: every control shows its value as a number as well as a position,
 * because a slider alone cannot tell you that speed is exactly 1.00.
 */

import { selectSelectedAnyClip, useEditor } from '@/editor/state/store'
import type { AnyClip, MediaClip, TextClip } from '@/editor/state/timeline-document'

export function Inspector() {
  const clip = useEditor(selectSelectedAnyClip)
  const selectionSize = useEditor((state) => state.selection.size)

  if (!clip) {
    return (
      <Shell>
        <p style={{ color: 'var(--color-ink-3)' }}>
          {selectionSize > 1
            ? `${selectionSize} clips selected — pick one to edit its properties`
            : 'Nothing selected'}
        </p>
      </Shell>
    )
  }

  return (
    <Shell>
      {isText(clip) ? <TextProperties clip={clip} /> : <MediaProperties clip={clip} />}
    </Shell>
  )
}

function isText(clip: AnyClip): clip is TextClip {
  return 'text' in clip
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <aside
      className="flex flex-col gap-3 overflow-y-auto px-4 py-3 text-xs"
      style={{ borderTop: '1px solid var(--color-rule)', color: 'var(--color-ink-2)' }}
      data-testid="inspector"
    >
      {children}
    </aside>
  )
}

function MediaProperties({ clip }: { clip: MediaClip }) {
  const store = useEditor.getState
  const transform = clip.transform

  return (
    <>
      <Header title="Clip" subtitle={clip.assetId} />

      <Slider
        label="Volume"
        value={clip.volume}
        min={0}
        max={2}
        step={0.05}
        format={(v) => `${Math.round(v * 100)} %`}
        onChange={(volume) => store().setClipProperties(clip.id, { volume })}
      />
      <Slider
        label="Speed"
        value={clip.speed}
        min={0.25}
        max={4}
        step={0.05}
        format={(v) => `${v.toFixed(2)}x`}
        onChange={(speed) => store().setClipProperties(clip.id, { speed })}
      />
      <Slider
        label="Fade in"
        value={clip.audioFadeInMs}
        min={0}
        max={Math.min(5_000, clip.durationMs)}
        step={50}
        format={(v) => `${Math.round(v)} ms`}
        onChange={(audioFadeInMs) => store().setClipProperties(clip.id, { audioFadeInMs })}
      />
      <Slider
        label="Fade out"
        value={clip.audioFadeOutMs}
        min={0}
        max={Math.min(5_000, clip.durationMs)}
        step={50}
        format={(v) => `${Math.round(v)} ms`}
        onChange={(audioFadeOutMs) => store().setClipProperties(clip.id, { audioFadeOutMs })}
      />

      <Row label="Rotate">
        {([0, 90, 180, 270] as const).map((rotation) => (
          <Chip
            key={rotation}
            active={(transform?.rotation ?? 0) === rotation}
            onClick={() => store().setClipProperties(clip.id, { rotation })}
          >
            {rotation}&deg;
          </Chip>
        ))}
      </Row>

      <Row label="Flip">
        <Chip
          active={transform?.flipH ?? false}
          onClick={() => store().setClipProperties(clip.id, { flipH: !(transform?.flipH ?? false) })}
        >
          Horizontal
        </Chip>
        <Chip
          active={transform?.flipV ?? false}
          onClick={() => store().setClipProperties(clip.id, { flipV: !(transform?.flipV ?? false) })}
        >
          Vertical
        </Chip>
      </Row>

      <Row label="Reframe">
        {/* Normalised, never pixels — contract §4.3. A crop written in pixels
            puts the subject somewhere else at export than in the preview. */}
        <Chip
          active={!transform?.crop}
          onClick={() => store().setClipProperties(clip.id, { crop: null })}
        >
          Full frame
        </Chip>
        <Chip
          active={Boolean(transform?.crop)}
          onClick={() =>
            store().setClipProperties(clip.id, {
              crop: { x: 0.1565, y: 0, width: 0.687, height: 1 },
            })
          }
        >
          Centre 9:16
        </Chip>
      </Row>

      <TransitionRow clip={clip} side="in" />
      <TransitionRow clip={clip} side="out" />
    </>
  )
}

/**
 * Transitions — scope §3.3: cut, fade to black, cross dissolve, and nothing else
 * in phase 1.
 *
 * `cut` is stored as no transition at all rather than as a zero-length one, so
 * the renderer never has to ask whether a zero-length dissolve means anything.
 */
function TransitionRow({ clip, side }: { clip: MediaClip; side: 'in' | 'out' }) {
  const current = side === 'in' ? clip.transitionIn : clip.transitionOut
  const type = current?.type ?? 'cut'
  const store = useEditor.getState

  return (
    <Row label={side === 'in' ? 'Transition in' : 'Transition out'}>
      {(['cut', 'fade', 'dissolve'] as const).map((option) => (
        <Chip
          key={option}
          active={type === option}
          onClick={() =>
            store().setTransition(
              clip.id,
              side,
              option === 'cut'
                ? null
                : // Clamped to half the shorter clip by the operation, which is
                  // invariant 7. Asking for 400 ms on a 300 ms clip is allowed
                  // here and comes back as 150.
                  { type: option, durationMs: Math.min(400, Math.floor(clip.durationMs / 2)) },
            )
          }
        >
          {option === 'fade' ? 'Fade to black' : option === 'dissolve' ? 'Dissolve' : 'Cut'}
        </Chip>
      ))}
      {current && <span className="tnum ml-auto">{current.durationMs} ms</span>}
    </Row>
  )
}

function TextProperties({ clip }: { clip: TextClip }) {
  const store = useEditor.getState
  return (
    <>
      <Header title={clip.kind === 'caption' ? 'Caption' : 'Title'} subtitle={clip.styleId} />
      <label className="flex flex-col gap-1">
        <span style={{ color: 'var(--color-ink-3)' }}>Text</span>
        <input
          value={clip.text}
          onChange={(event) => store().setText(clip.id, event.target.value)}
          className="px-2 py-1"
          style={{
            background: 'var(--color-surface-3)',
            border: '1px solid var(--color-rule)',
            borderRadius: 'var(--radius-sm)',
            color: 'var(--color-ink)',
          }}
          data-testid="text-input"
        />
      </label>
      <p style={{ color: 'var(--color-ink-3)' }}>
        Position {clip.position?.x.toFixed(2) ?? '0.50'} / {clip.position?.y.toFixed(2) ?? '0.82'} —
        normalised to the canvas, so the preview and the export agree.
      </p>
    </>
  )
}

// --------------------------------------------------------------------------
// Controls
// --------------------------------------------------------------------------

function Header({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <span style={{ color: 'var(--color-ink)', fontWeight: 600 }}>{title}</span>
      <span className="tnum truncate" style={{ color: 'var(--color-ink-3)' }}>
        {subtitle}
      </span>
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-24 shrink-0" style={{ color: 'var(--color-ink-3)' }}>
        {label}
      </span>
      {children}
    </div>
  )
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className="px-2 py-1"
      style={{
        borderRadius: 'var(--radius-sm)',
        // Charter §8: active is a tint plus the accent ink, never colour alone —
        // `aria-pressed` carries it for anyone not seeing the tint.
        background: active ? 'var(--color-accent-soft)' : 'var(--color-surface-3)',
        color: active ? 'var(--color-accent)' : 'var(--color-ink-2)',
        fontWeight: active ? 600 : 400,
        transition: 'background var(--duration-micro) ease-out',
      }}
    >
      {children}
    </button>
  )
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  format,
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  step: number
  format: (value: number) => string
  onChange: (value: number) => void
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-24 shrink-0" style={{ color: 'var(--color-ink-3)' }}>
        {label}
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        aria-label={label}
        className="flex-1"
      />
      <span className="tnum w-16 text-right" style={{ color: 'var(--color-ink)' }}>
        {format(value)}
      </span>
    </div>
  )
}

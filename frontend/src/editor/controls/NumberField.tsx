'use client'

/**
 * A compact numeric control: a typed field, with the slider as the secondary.
 *
 * M4.5 item 5, all three halves of it — the size, the missing typed input, and
 * the unexplained label:
 *
 * * **Size.** The old control was a native `<input type="range">` at full panel
 *   width, one per row, in a panel 176 px tall — four of them filled it. Here
 *   the number is the primary control and the track is a 3 px rail under it, so
 *   a row is 34 px instead of 56 and the panel holds what it is asked to.
 * * **Typed input.** Speed exactly 1.25 is now four keystrokes rather than a
 *   drag that has to land on the right pixel.
 * * **The label can carry a `hint`**, which is what *"Fade in"* needed: it is
 *   the audio volume ramp at the start of the clip, in a product that also has
 *   a video transition called *fade*.
 *
 * **Committing follows the store's second rule** — *a drag does not commit*.
 * `onChange` on a range input fires per pixel of travel, so committing from it
 * puts forty entries in the undo stack for one pull of a handle and queues
 * forty autosaves. The value under the hand is local; the release is the edit.
 * That was already true of the slider this replaces, and it stays true of the
 * typed field: `Enter` and blur commit, `Escape` abandons.
 */

import { useEffect, useId, useRef, useState } from 'react'

import {
  decimalsFor,
  displayRange,
  parseNumericInput,
  toDisplay,
  toDocument,
  type NumericRange,
} from '@/editor/controls/number-field'

export interface NumberFieldProps extends NumericRange {
  label: string
  /**
   * Multiplier between the document's unit and the one on screen.
   *
   * **This exists because of a bug that only showed up by using the control.**
   * Strength is stored 0-1 and displayed as a percentage; the field showed
   * `66 %`, and typing `42` into it — which is what anybody would type — parsed
   * as 42, clamped to the maximum, and set the grade to full strength. The
   * value read back was silently the wrong one, which is worse than a rejected
   * keystroke and is precisely what item 5 set out to remove.
   *
   * With a scale, `min`, `max` and `step` stay in the document's unit and every
   * number the user sees or types is in the displayed one. 100 for a
   * percentage, 1 (the default) for everything already shown in its own unit.
   */
  scale?: number
  /**
   * The sentence explaining what this actually changes, shown on hover and to a
   * screen reader. Optional — most rows do not need one, and a hint on a row
   * that is already obvious is noise.
   */
  hint?: string
  icon?: React.ReactNode
  value: number
  /** How the number reads to a person: `1.25x`, `80 %`, `250 ms`. */
  format: (value: number) => string
  /** The unit shown beside the field while it is being edited. */
  suffix?: string
  disabled?: boolean
  onChange: (value: number) => void
  'data-testid'?: string
}

export function NumberField({
  label,
  hint,
  icon,
  value,
  min,
  max,
  step,
  format,
  suffix,
  disabled,
  onChange,
  scale = 1,
  'data-testid': testId,
}: NumberFieldProps) {
  // The range the *field* works in. Everything below this line is in displayed
  // units; `onChange` is the one place it converts back.
  const range = displayRange({ min, max, step }, scale)
  /** Set while the pointer is down on the track, or while the field has focus. */
  const [draft, setDraft] = useState<number | null>(null)
  const [text, setText] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const id = useId()

  const shown = toDisplay(draft ?? value, scale, range.step)

  // The document can change under this control — undo, a tool result, or
  // another clip being selected — and a stale draft would show the old number
  // until the next interaction.
  useEffect(() => {
    setDraft(null)
    setText(null)
  }, [value])

  function commitSlider() {
    if (draft === null) return
    const next = draft
    setDraft(null)
    if (next !== value) onChange(next)
  }

  function commitText() {
    if (text === null) return
    const parsed = parseNumericInput(text, range)
    setText(null)
    setDraft(null)
    if (parsed === null) return
    const inDocumentUnits = toDocument(parsed, scale, step)
    if (inDocumentUnits !== value) onChange(inDocumentUnits)
  }

  const fill = range.max > range.min ? ((shown - range.min) / (range.max - range.min)) * 100 : 0

  return (
    <div
      className="flex flex-col gap-1"
      data-testid={testId}
      data-value={value}
      style={{ opacity: disabled ? 0.45 : 1 }}
    >
      <div className="flex items-center gap-2">
        <label
          htmlFor={id}
          className="flex min-w-0 flex-1 items-center gap-1.5 truncate"
          style={{ color: 'var(--color-ink-3)' }}
          // Charter §14: the hint is the accessible description, not a visual
          // flourish. `title` gives it to a mouse, `aria-describedby` would
          // need an element — this is one string, so `title` on the label and
          // the same text on the input is the smaller correct thing.
          title={hint}
        >
          {icon && (
            <span aria-hidden="true" className="shrink-0" style={{ color: 'var(--color-ink-faint)' }}>
              {icon}
            </span>
          )}
          <span className="truncate">{label}</span>
          {hint && (
            <span aria-hidden="true" style={{ color: 'var(--color-ink-faint)' }}>
              ⓘ
            </span>
          )}
        </label>

        <input
          ref={inputRef}
          id={id}
          type="text"
          inputMode="decimal"
          disabled={disabled}
          title={hint}
          value={text ?? format(shown)}
          onFocus={() => setText(String(round(shown, decimalsFor(range.step))))}
          onChange={(event) => setText(event.target.value)}
          onBlur={commitText}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              commitText()
              inputRef.current?.blur()
            }
            if (event.key === 'Escape') {
              setText(null)
              setDraft(null)
              inputRef.current?.blur()
            }
            // Arrow keys nudge by one step, which is what a numeric field is
            // expected to do and what makes the slider genuinely optional.
            if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
              event.preventDefault()
              const base = text === null ? shown : (parseNumericInput(text, range) ?? shown)
              const next = clampTo(
                base + (event.key === 'ArrowUp' ? range.step : -range.step),
                range,
              )
              setText(String(round(next, decimalsFor(range.step))))
            }
          }}
          className="tnum w-20 shrink-0 px-1.5 py-0.5 text-right"
          style={{
            background: 'var(--color-surface-3)',
            border: '1px solid var(--color-rule)',
            borderRadius: 'var(--radius-sm)',
            color: 'var(--color-ink)',
          }}
          aria-label={hint ? `${label} — ${hint}` : label}
          data-testid={testId ? `${testId}-input` : undefined}
        />
        {suffix && (
          <span className="w-6 shrink-0 text-left" style={{ color: 'var(--color-ink-faint)' }}>
            {suffix}
          </span>
        )}
      </div>

      {/* The track. Secondary by design — it is 3 px tall and carries no
          number, because the number is above it. */}
      <div className="relative h-3">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute top-1/2 h-[3px] w-full -translate-y-1/2 overflow-hidden"
          style={{ background: 'var(--color-rule)', borderRadius: 999 }}
        >
          <div className="h-full" style={{ width: `${fill}%`, background: 'var(--color-accent-line)' }} />
        </div>
        <input
          type="range"
          min={range.min}
          max={range.max}
          step={range.step}
          value={shown}
          disabled={disabled}
          onChange={(event) => setDraft(toDocument(Number(event.target.value), scale, step))}
          onPointerUp={commitSlider}
          onLostPointerCapture={commitSlider}
          onKeyUp={commitSlider}
          onBlur={commitSlider}
          // Hidden visually, not removed: it is still the control a keyboard or
          // a screen reader drives, and `appearance: none` with a transparent
          // thumb keeps the hit area over the painted track above.
          className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
          aria-label={`${label} slider`}
          tabIndex={-1}
          data-testid={testId ? `${testId}-range` : undefined}
        />
      </div>
    </div>
  )
}

function clampTo(value: number, range: NumericRange): number {
  return Math.min(range.max, Math.max(range.min, value))
}

function round(value: number, decimals: number): number {
  const factor = 10 ** decimals
  return Math.round(value * factor) / factor
}

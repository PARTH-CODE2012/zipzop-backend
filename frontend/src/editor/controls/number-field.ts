/**
 * Turning what someone typed into a value the document will accept.
 *
 * M4.5 item 5: *"There is no way to enter a value. Setting speed to exactly
 * 1.25 means dragging until the readout agrees."* Every value in the inspector
 * is a number with a known range, so every one of them can be typed — and the
 * moment a control accepts typing, it has to deal with what people actually
 * type into it.
 *
 * The parsing lives here rather than in the component because the interesting
 * cases are all pure, and because a field that silently turns `"abc"` into `0`
 * is worse than one that refuses it: `0` is a real volume, and a clip that goes
 * silent because of a typo looks like a bug in playback rather than a rejected
 * keystroke.
 *
 * **`null` means "do not write this"**, never "write zero".
 */

export interface NumericRange {
  min: number
  max: number
  /** The granularity the document wants. `0.05` for volume, `1` for a rotation. */
  step: number
}

/**
 * Parse and clamp, or refuse.
 *
 * Accepts what a person types and a `<input type="number">` produces: leading
 * and trailing space, a leading `+`, a comma as the decimal separator — which is
 * what a French or Hindi keyboard layout gives you, and this product accepts
 * both those languages for captions, so it will see them.
 *
 * Refuses empty strings and anything that is not fully a number. `"1.2x"` is a
 * typo, not a request for 1.2 — `parseFloat` would take the 1.2 and leave the
 * user with a value they did not mean and no sign anything was ignored.
 */
export function parseNumericInput(raw: string, range: NumericRange): number | null {
  const trimmed = raw.trim().replace(',', '.')
  if (trimmed === '' || trimmed === '-' || trimmed === '+' || trimmed === '.') return null
  if (!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(trimmed)) return null

  const parsed = Number(trimmed)
  if (!Number.isFinite(parsed)) return null

  return snapToStep(clamp(parsed, range.min, range.max), range)
}

/**
 * Round to the nearest step, measured **from `min` rather than from zero**.
 *
 * A range that starts at 0.25 with a step of 0.05 has valid values at 0.25,
 * 0.30, 0.35 — not at 0.20 or 0.40-and-a-third. Snapping from zero happens to
 * agree whenever `min` is a whole multiple of `step`, which is most of them,
 * which is exactly why getting it wrong survives casual testing.
 *
 * The result is re-rounded to a sane number of decimal places: `0.1 + 0.2` is
 * the reason, and a `speed` of `1.3000000000000003` in the document is a value
 * that will be shown to somebody eventually.
 */
export function snapToStep(value: number, range: NumericRange): number {
  const { min, step } = range
  if (step <= 0) return value
  const steps = Math.round((value - min) / step)
  return round(min + steps * step, decimalsFor(step))
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

/**
 * How many decimal places a step implies.
 *
 * Read off the step rather than fixed at two, so a step of `0.001` is not
 * rounded away by the very function meant to keep it exact.
 */
export function decimalsFor(step: number): number {
  if (!Number.isFinite(step) || step <= 0) return 0
  const text = String(step)
  const exponent = text.indexOf('e-')
  if (exponent !== -1) return Number(text.slice(exponent + 2))
  const dot = text.indexOf('.')
  return dot === -1 ? 0 : text.length - dot - 1
}

function round(value: number, decimals: number): number {
  const factor = 10 ** decimals
  // `+0` rather than the raw result: `Math.round(-0.4)` is `-0`, and `-0`
  // serialises into the timeline document as `-0`, which is valid JSON and
  // reads as a mistake to anyone looking at the file.
  return Math.round(value * factor) / factor + 0
}

// --------------------------------------------------------------------------
// Display units
// --------------------------------------------------------------------------

/**
 * The conversion between what the document stores and what the field shows.
 *
 * **Extracted from the component because leaving it there produced a real bug.**
 * Strength is stored 0-1 and shown as a percentage. The field displayed `66 %`,
 * and typing `42` — which is what anybody looking at a percentage types — parsed
 * as 42, clamped to the maximum of 1, and set the grade to full strength. The
 * user got a value they did not ask for, silently, from the control that item 5
 * added specifically so values could be set exactly.
 *
 * Two functions and a rule: **`min`, `max` and `step` are always in the
 * document's unit, and everything the user sees or types is in the displayed
 * one.** Scale 100 for a percentage; 1 — the default — for anything already
 * shown in its own unit, like milliseconds or a speed multiplier.
 */
export function toDisplay(value: number, scale: number, displayStep: number = 1): number {
  if (scale === 1) return value
  // Rounded on the way *out* as well as on the way in. `0.07 * 100` is
  // 7.000000000000001, and that number reaches the range input as its `value`
  // against a `step` of 1 — a handle the browser is entitled to snap somewhere
  // else, and a readout one `format` away from showing the noise.
  return round(value * scale, decimalsFor(displayStep))
}

export function toDocument(displayed: number, scale: number, step: number): number {
  if (scale === 1) return displayed
  // Re-rounded to the document's own precision: 42 / 100 is 0.42, but 4.2 / 100
  // is 0.042000000000000003, and that is what would be written to the timeline.
  return round(displayed / scale, decimalsFor(step))
}

/** The range a scaled field's own arithmetic works in. */
export function displayRange(range: NumericRange, scale: number): NumericRange {
  if (scale === 1) return range
  return {
    min: round(range.min * scale, decimalsFor(range.step)),
    max: round(range.max * scale, decimalsFor(range.step)),
    step: round(range.step * scale, decimalsFor(range.step)),
  }
}

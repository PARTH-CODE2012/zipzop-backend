/**
 * Time to pixels, and back.
 *
 * Every position on the timeline is **integer milliseconds** (docs/README.md,
 * Conventions). Pixels are a property of the current zoom and the scroll
 * offset, and exist only at the moment of drawing — nothing derived from them
 * is ever stored.
 *
 * The two directions are not symmetric, and that asymmetry is on purpose:
 * `msToPx` returns a float, because a clip edge must be able to land between
 * two device pixels rather than jumping as you zoom. `pxToMs` returns a
 * rounded integer, because whatever it produces may become a `startMs` in the
 * document, and a float there is the drift the conventions exist to prevent.
 */

/** Pixels per second. The single number that defines the zoom. */
export type Zoom = number

export const MIN_ZOOM: Zoom = 2 // ~8 minutes across a 1000px viewport
export const MAX_ZOOM: Zoom = 400 // ~2.5 seconds across the same
export const DEFAULT_ZOOM: Zoom = 40

export function clampZoom(zoom: Zoom): Zoom {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom))
}

export function msToPx(ms: number, zoom: Zoom): number {
  return (ms / 1000) * zoom
}

export function pxToMs(px: number, zoom: Zoom): number {
  return Math.round((px / zoom) * 1000)
}

/**
 * Zoom around a fixed point.
 *
 * Zooming with the cursor over a clip must leave that clip under the cursor,
 * not slide the view out from under the hand. Returns the scroll offset that
 * holds `anchorMs` at `anchorPx`.
 *
 * **Clamped at zero**, and the anchor is not held when it clamps. Zooming out
 * with the cursor near the right of the viewport can ask for a negative
 * scroll — the timeline would have to start before zero for the anchor to stay
 * put. There is nothing there, so the view stops at the beginning and the
 * anchor drifts left. Any caller relying on the anchor holding exactly has to
 * check for `0`.
 */
export function scrollForAnchoredZoom(anchorMs: number, anchorPx: number, zoom: Zoom): number {
  return Math.max(0, msToPx(anchorMs, zoom) - anchorPx)
}

// --------------------------------------------------------------------------
// Ruler ticks
// --------------------------------------------------------------------------

/**
 * Intervals a person reads without doing arithmetic, in milliseconds.
 *
 * Deliberately not powers of two or a `nice number` algorithm: a ruler that
 * labels every 8 or 16 seconds is technically well spaced and unreadable,
 * because nobody thinks about video in eighths of a minute.
 */
const TICK_STEPS_MS = [
  100, 200, 500,
  1_000, 2_000, 5_000, 10_000, 15_000, 30_000,
  60_000, 120_000, 300_000, 600_000, 900_000, 1_800_000,
  3_600_000,
] as const

/** One hour — the last entry above. Nothing coarser is useful against a
 *  60-minute upload ceiling, and naming it keeps the fallback total. */
const COARSEST_STEP_MS = 3_600_000

/** The smallest readable step that keeps labels at least `minGapPx` apart. */
export function tickStepMs(zoom: Zoom, minGapPx = 80): number {
  for (const step of TICK_STEPS_MS) {
    if (msToPx(step, zoom) >= minGapPx) return step
  }
  return COARSEST_STEP_MS
}

export interface Tick {
  ms: number
  px: number
  major: boolean
}

/**
 * Ticks for the visible window only.
 *
 * A 60-minute project at full zoom is 360 000 hundredths of a second; laying
 * out ticks for the whole document rather than the window is how a ruler
 * becomes the slowest thing on the page.
 */
export function ticksForWindow(
  fromMs: number,
  toMs: number,
  zoom: Zoom,
  minGapPx = 80,
): Tick[] {
  const step = tickStepMs(zoom, minGapPx)
  // A minor tick every fifth of a major one, unless that would be denser than
  // a tick every 4 pixels — past that they read as a solid line.
  const minor = msToPx(step / 5, zoom) >= 4 ? step / 5 : step

  const first = Math.floor(fromMs / minor) * minor
  const ticks: Tick[] = []
  for (let ms = first; ms <= toMs; ms += minor) {
    if (ms < 0) continue
    ticks.push({ ms, px: msToPx(ms, zoom), major: ms % step === 0 })
  }
  return ticks
}

// --------------------------------------------------------------------------
// Timecode
// --------------------------------------------------------------------------

/**
 * `m:ss` at coarse zoom, `m:ss.mmm` when the zoom makes milliseconds visible.
 *
 * Hours only appear once there are hours: a leading `0:` on every label is
 * noise for the clips people actually cut.
 */
export function formatTimecode(ms: number, { withMillis = false } = {}): string {
  const sign = ms < 0 ? '-' : ''
  const total = Math.abs(Math.round(ms))

  const hours = Math.floor(total / 3_600_000)
  const minutes = Math.floor((total % 3_600_000) / 60_000)
  const seconds = Math.floor((total % 60_000) / 1000)
  const millis = total % 1000

  const pad = (value: number, width = 2) => String(value).padStart(width, '0')
  const head = hours > 0 ? `${hours}:${pad(minutes)}` : `${minutes}`
  const body = `${head}:${pad(seconds)}`
  return withMillis ? `${sign}${body}.${pad(millis, 3)}` : `${sign}${body}`
}

/** The label a ruler tick carries at this zoom. */
export function tickLabel(ms: number, zoom: Zoom): string {
  return formatTimecode(ms, { withMillis: tickStepMs(zoom) < 1000 })
}

// --------------------------------------------------------------------------
// Snapping
// --------------------------------------------------------------------------

/**
 * Pull a dragged position onto a nearby edge.
 *
 * The threshold is in **pixels**, not milliseconds, so snapping feels the same
 * at every zoom — a fixed millisecond threshold would be unusably sticky when
 * zoomed out and useless when zoomed in.
 *
 * Returns the original value when nothing is close enough, so a caller can
 * compare identity to know whether a snap happened.
 */
export function snapMs(
  ms: number,
  candidates: readonly number[],
  zoom: Zoom,
  thresholdPx = 8,
): number {
  let best = ms
  let bestDistance = Infinity
  for (const candidate of candidates) {
    const distance = Math.abs(msToPx(candidate - ms, zoom))
    if (distance <= thresholdPx && distance < bestDistance) {
      best = candidate
      bestDistance = distance
    }
  }
  return best
}

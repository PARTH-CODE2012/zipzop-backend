/**
 * Turning a job result into edits.
 *
 * ⚠️ **Every job result is in asset time. The timeline is not.** A clip trimmed
 * to start four seconds into its media and played at 1.5× has a clock of its
 * own, and a caption placed at the millisecond the server reported lands
 * somewhere else entirely — early, late, and drifting further the longer the
 * clip runs. The conversion lives here, in one place, tested against exactly
 * that clip (contract §6.2: *"ranges are in asset time, not timeline time — the
 * client maps them onto the clip"*).
 *
 * Two rules the conversion obeys, both of which cost a bug if forgotten:
 *
 * 1. **Clip to the window.** The server analysed the *whole file*; the clip
 *    shows part of it. Words from a part the clip does not include must be
 *    dropped, not placed — otherwise trimming a clip silently gains captions
 *    for footage nobody can see.
 * 2. **Speed divides.** Asset time runs `speed` times faster than timeline
 *    time, so a range four seconds long in a 2× clip occupies two seconds of
 *    timeline.
 */

import { assetMsToTimelineMs, sourceOutMs } from '@/editor/state/timeline-document'
import type { MediaClip } from '@/editor/state/timeline-document'

/** One word from a `captions` result — contract §6.2's short keys. */
export interface CaptionWord {
  /** The word itself. */
  w: string
  /** Start, in asset milliseconds. */
  s: number
  /** End, in asset milliseconds. */
  e: number
  /** Confidence, 0–1. Below `LOW_CONFIDENCE` is worth flagging to the user. */
  c: number
  /** Emphasis, 0–1. Drives the animation's intensity. */
  em: number
}

export interface CaptionsResult {
  language: string
  durationMs: number
  wordCount: number
  words: CaptionWord[]
}

export interface TrimRemoval {
  startMs: number
  endMs: number
  reason: 'silence' | 'filler' | 'stutter' | 'repeat'
  confidence: number
}

export interface SmartTrimResult {
  analyzedDurationMs: number
  keptDurationMs: number
  removals: TrimRemoval[]
}

/**
 * The contract's own threshold: *"`c` below 0.7 is worth flagging in the UI so
 * the user checks it."* A name the recogniser guessed at is the single most
 * likely thing to be wrong, and the whole point of captions being clips is
 * that it can be corrected.
 */
export const LOW_CONFIDENCE = 0.7

/** A caption ready to become a text clip, in **timeline** milliseconds. */
export interface PlacedWord {
  text: string
  startMs: number
  durationMs: number
  emphasis: number
  confidence: number
  /** True when the user should look at this one. */
  uncertain: boolean
}

/**
 * The portion of the asset a clip actually shows, in asset milliseconds.
 *
 * `sourceOutMs` is derived rather than stored precisely so the two cannot
 * disagree (contract §4.2).
 */
export function clipWindow(clip: MediaClip): { fromMs: number; toMs: number } {
  return { fromMs: clip.sourceInMs, toMs: sourceOutMs(clip) }
}

/**
 * Words placed on the timeline, dropping everything the clip does not show.
 *
 * A word straddling the edge is **kept and clipped**, not dropped: half a word
 * of audio is still a word the viewer hears, and removing it would leave a
 * silent gap exactly where the cut is most noticeable.
 */
export function placeWords(
  words: readonly CaptionWord[],
  clip: MediaClip,
  options: { minDurationMs?: number } = {},
): PlacedWord[] {
  const { fromMs, toMs } = clipWindow(clip)
  const minDurationMs = options.minDurationMs ?? 1

  const placed: PlacedWord[] = []
  for (const word of words) {
    // Reject before converting: a word outside the window has no timeline
    // position at all, and computing one would place it over a neighbour.
    if (word.e <= fromMs || word.s >= toMs) continue

    const startAsset = Math.max(word.s, fromMs)
    const endAsset = Math.min(word.e, toMs)
    const startMs = assetMsToTimelineMs(clip, startAsset)
    const endMs = assetMsToTimelineMs(clip, endAsset)

    placed.push({
      text: word.w,
      startMs,
      durationMs: Math.max(minDurationMs, endMs - startMs),
      emphasis: clamp01(word.em),
      confidence: clamp01(word.c),
      uncertain: word.c < LOW_CONFIDENCE,
    })
  }

  // A very fast clip can round two words onto the same millisecond, and
  // invariant 1 forbids the overlap that would follow. Nudging the later one is
  // the smaller lie than dropping a word the viewer can hear.
  for (let index = 1; index < placed.length; index += 1) {
    const previous = placed[index - 1]!
    const current = placed[index]!
    const previousEnd = previous.startMs + previous.durationMs
    if (current.startMs < previousEnd) {
      current.startMs = previousEnd
      current.durationMs = Math.max(minDurationMs, current.durationMs)
    }
  }

  return placed
}

/** A range to cut, in **timeline** milliseconds. */
export interface PlacedRemoval {
  startMs: number
  endMs: number
  reason: TrimRemoval['reason']
  confidence: number
}

/**
 * Smart-trim ranges mapped onto a clip, clipped to what it shows.
 *
 * Ordered and non-overlapping on the way in (the server guarantees it) and on
 * the way out — the conversion is monotonic, so order survives it, and clipping
 * cannot introduce an overlap that was not there.
 */
export function placeRemovals(
  removals: readonly TrimRemoval[],
  clip: MediaClip,
): PlacedRemoval[] {
  const { fromMs, toMs } = clipWindow(clip)

  const placed: PlacedRemoval[] = []
  for (const removal of removals) {
    if (removal.endMs <= fromMs || removal.startMs >= toMs) continue
    const startMs = assetMsToTimelineMs(clip, Math.max(removal.startMs, fromMs))
    const endMs = assetMsToTimelineMs(clip, Math.min(removal.endMs, toMs))
    if (endMs <= startMs) continue
    placed.push({ startMs, endMs, reason: removal.reason, confidence: removal.confidence })
  }
  return placed
}

/**
 * How much of the clip a set of removals would take away, as a fraction.
 *
 * Shown before applying, because *"this will cut 38% of your clip"* is the one
 * number that stops someone running `aggressive` on a interview and undoing two
 * hundred edits.
 */
export function removedFraction(removals: readonly PlacedRemoval[], clip: MediaClip): number {
  if (clip.durationMs <= 0) return 0
  const removed = removals.reduce((total, range) => total + (range.endMs - range.startMs), 0)
  return Math.min(1, removed / clip.durationMs)
}

function clamp01(value: number): number {
  return value < 0 ? 0 : value > 1 ? 1 : value
}

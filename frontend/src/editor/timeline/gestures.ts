/**
 * The arithmetic behind dragging, snapping, virtualising and the marquee.
 *
 * Pure functions of numbers, deliberately: pointer handling is the part of an
 * editor that is hardest to test through a component and easiest to get subtly
 * wrong. The component below is left with nothing but `addEventListener` and
 * `setState`.
 */

import { clipEndMs, type AnyClip } from '@/editor/state/timeline-document'
import { msToPx, pxToMs, type Zoom } from '@/editor/timeline/scale'

/**
 * How close an edge has to be, **in pixels rather than milliseconds**.
 *
 * A tolerance in milliseconds would mean snapping felt sticky when zoomed out
 * and unreachable when zoomed in — at 2 px/s a 100 ms tolerance is a fifth of a
 * pixel. What the user is judging is distance on screen, so that is what the
 * tolerance is measured in.
 */
export const SNAP_TOLERANCE_PX = 8

/**
 * The nearest snap candidate, or the position unchanged.
 *
 * `suppressed` is the modifier key: holding it turns snapping off for the
 * duration of the gesture, which is the only way to place a clip one frame away
 * from an edge it wants to stick to.
 */
export function snapMs(
  positionMs: number,
  candidates: readonly number[],
  zoom: Zoom,
  suppressed = false,
): number {
  if (suppressed) return positionMs
  const toleranceMs = pxToMs(SNAP_TOLERANCE_PX, zoom)
  let best = positionMs
  let bestGap = toleranceMs
  for (const candidate of candidates) {
    const gap = Math.abs(candidate - positionMs)
    if (gap <= bestGap) {
      bestGap = gap
      best = candidate
    }
  }
  return best
}

/**
 * Snap candidates for a gesture: every clip edge except the dragged clip's own,
 * plus the playhead and zero.
 *
 * Excluding the clip being dragged matters — left in, a clip snaps to where it
 * already is and cannot be nudged off its own edge.
 */
export function snapCandidatesFor(
  clips: readonly AnyClip[],
  movingClipId: string | null,
  playheadMs: number,
): number[] {
  const edges = new Set<number>([0, playheadMs])
  for (const clip of clips) {
    if (clip.id === movingClipId) continue
    edges.add(clip.startMs)
    edges.add(clipEndMs(clip))
  }
  return [...edges].sort((a, b) => a - b)
}

/**
 * The clips that intersect the visible time window — PHASE1-TASKS M3,
 * *"virtualised by time window ⚠️ — must stay smooth at 500 clips"*.
 *
 * By **time**, not by row: the timeline is one row deep per track and thousands
 * of clips wide, so windowing the rows would save nothing. The overscan keeps a
 * screenful either side so a clip is never seen popping in at the edge during a
 * scroll.
 */
export function clipsInWindow<T extends AnyClip>(
  clips: readonly T[],
  fromMs: number,
  toMs: number,
  overscanMs = 0,
): T[] {
  const from = fromMs - overscanMs
  const to = toMs + overscanMs
  return clips.filter((clip) => clip.startMs <= to && clipEndMs(clip) >= from)
}

/** Every clip the marquee touches. Intersection, not containment — a lasso that
 * only caught clips it fully enclosed would miss the long one you dragged
 * across, which is usually the one you meant. */
export function marqueeHits(clips: readonly AnyClip[], fromMs: number, toMs: number): string[] {
  const [low, high] = fromMs <= toMs ? [fromMs, toMs] : [toMs, fromMs]
  return clips.filter((clip) => clip.startMs <= high && clipEndMs(clip) >= low).map((c) => c.id)
}

/** Which part of a clip a pointer landed on, in the clip's own pixel space. */
export type ClipZone = 'trim-start' | 'trim-end' | 'move'

/**
 * The trim handles are 8 px, and they shrink rather than disappear on a narrow
 * clip: two 8 px handles on a 14 px clip would leave no way to move it at all,
 * so no handle may take more than a quarter of the width.
 */
export function zoneAt(offsetPx: number, widthPx: number): ClipZone {
  const handle = Math.min(8, Math.max(2, widthPx / 4))
  if (offsetPx <= handle) return 'trim-start'
  if (offsetPx >= widthPx - handle) return 'trim-end'
  return 'move'
}

/** Pixels from the left edge of the lane to a timeline position. */
export function laneX(ms: number, zoom: Zoom, scrollPx: number): number {
  return msToPx(ms, zoom) - scrollPx
}

/** …and back, which is what every pointer event needs. */
export function msAtLaneX(x: number, zoom: Zoom, scrollPx: number): number {
  return Math.max(0, pxToMs(scrollPx + x, zoom))
}

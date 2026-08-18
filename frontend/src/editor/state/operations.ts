/**
 * The editing operations, as recipes over a draft document.
 *
 * Pure and free of React, Zustand and the network, which is what makes them
 * testable without a browser — and they are where the editor's correctness
 * actually lives. The store's job is to wrap them in `commit()`; theirs is to
 * leave the document valid.
 *
 * **Every operation leaves contract §4.3 satisfied.** Clips stay ordered and
 * never overlap, durations stay positive, ids stay unique. That is not a
 * convenience: an autosave that comes back `422 INVALID_TIMELINE` arrives two
 * seconds and several edits after the mistake, and the user has no idea which
 * action caused it.
 *
 * Where an edit would break an invariant it is **clamped, not rejected**.
 * Dragging a clip into its neighbour butts it against that neighbour rather
 * than refusing to move. Phase 1 has no magnetic timeline and no ripple edits
 * (`docs/02-scope-v1.md` §3.3), so the neighbours are the wall.
 */

import type { Draft } from 'immer'

import {
  clipEndMs,
  isMediaTrack,
  type MediaClip,
  type MediaTrack,
  type TimelineDocument,
  type Track,
  type Transition,
} from '@/editor/state/timeline-document'

/** The shortest clip an edit may produce. One frame at 30 fps is 33 ms. */
export const MIN_CLIP_MS = 40

export type Draftable = Draft<TimelineDocument>

// --------------------------------------------------------------------------
// Ids
// --------------------------------------------------------------------------

/**
 * Client-generated and stable for the life of the clip (contract §4.2).
 *
 * `crypto.randomUUID` is available in every browser phase 1 supports and in
 * Node 19+, so tests do not need a polyfill.
 */
export function newClipId(): string {
  return `clp_${crypto.randomUUID().slice(0, 8)}`
}

export function newTrackId(kind: Track['kind']): string {
  return `trk_${kind}`
}

// --------------------------------------------------------------------------
// Lookup helpers, on the draft
// --------------------------------------------------------------------------

function mediaTracks(document: Draftable): Draft<MediaTrack>[] {
  return document.tracks.filter((track) => isMediaTrack(track as Track)) as Draft<MediaTrack>[]
}

function findMedia(
  document: Draftable,
  clipId: string,
): { track: Draft<MediaTrack>; index: number; clip: Draft<MediaClip> } | null {
  for (const track of mediaTracks(document)) {
    const index = track.clips.findIndex((clip) => clip.id === clipId)
    const clip = index >= 0 ? track.clips[index] : undefined
    if (clip) return { track, index, clip }
  }
  return null
}

export function ensureTrack(document: Draftable, kind: 'video' | 'audio'): Draft<MediaTrack> {
  const existing = mediaTracks(document).find((track) => track.kind === kind)
  if (existing) return existing
  const track = {
    id: newTrackId(kind),
    kind,
    index: 0,
    muted: false,
    locked: false,
    clips: [],
  } as Draft<MediaTrack>
  document.tracks.push(track)
  return track
}

/** Clips ordered by start, which invariant 2 requires and every edit restores. */
function reorder(track: Draft<MediaTrack>): void {
  track.clips.sort((a, b) => a.startMs - b.startMs)
}

// --------------------------------------------------------------------------
// Adding
// --------------------------------------------------------------------------

export function appendClip(
  document: Draftable,
  input: { assetId: string; durationMs: number; kind?: 'video' | 'audio'; id?: string },
): string {
  const track = ensureTrack(document, input.kind ?? 'video')
  const id = input.id ?? newClipId()
  // The end of the track is the only position that is always legal without
  // moving something else.
  const startMs = track.clips.reduce((end, clip) => Math.max(end, clipEndMs(clip)), 0)
  track.clips.push({
    id,
    assetId: input.assetId,
    startMs,
    durationMs: Math.max(MIN_CLIP_MS, Math.round(input.durationMs)),
    sourceInMs: 0,
    speed: 1,
    volume: 1,
    audioFadeInMs: 0,
    audioFadeOutMs: 0,
    effects: [],
  })
  return id
}

// --------------------------------------------------------------------------
// Splitting
// --------------------------------------------------------------------------

/**
 * Split a clip at a timeline position.
 *
 * The right-hand piece starts reading where the left one stopped, and **speed
 * is what makes that arithmetic worth writing down**: at 2x, one second of
 * timeline consumed two seconds of source, so the new `sourceInMs` advances by
 * `elapsed * speed` and not by `elapsed`. Getting this wrong produces a cut
 * that looks right in the timeline and jumps in the picture.
 *
 * Returns the new clip's id, or null when the position is not inside a clip or
 * would leave a piece too short to be useful.
 */
export function splitAt(document: Draftable, clipId: string, timelineMs: number): string | null {
  const found = findMedia(document, clipId)
  if (!found) return null

  const { track, index, clip } = found
  const at = Math.round(timelineMs)
  const elapsed = at - clip.startMs
  const remaining = clipEndMs(clip) - at
  if (elapsed < MIN_CLIP_MS || remaining < MIN_CLIP_MS) return null

  const rightId = newClipId()
  const right: Draft<MediaClip> = {
    ...clip,
    id: rightId,
    startMs: at,
    durationMs: remaining,
    sourceInMs: clip.sourceInMs + Math.round(elapsed * clip.speed),
    // The two halves meet at a cut. A transition belongs to the outside edges
    // of the pair — duplicating it onto the new join would invent a crossfade
    // the user never asked for.
    transitionIn: null,
    transitionOut: clip.transitionOut ?? null,
    // Effects follow the picture, so both halves keep the grade. Immer's draft
    // is structurally shared, so this copy is cheap.
    effects: clip.effects.map((effect) => ({ ...effect })),
  }

  clip.durationMs = elapsed
  clip.transitionOut = null
  track.clips.splice(index + 1, 0, right)
  return rightId
}

// --------------------------------------------------------------------------
// Trimming
// --------------------------------------------------------------------------

/**
 * Drag the left edge. The clip's content stays where it is on screen.
 *
 * `startMs` and `sourceInMs` move **together**: trimming a second off the head
 * means the clip both begins a second later on the timeline and starts a
 * second later inside its media. Moving only one of them slides the picture,
 * which is a different edit entirely.
 *
 * `maxSourceMs` is the asset's duration when the caller knows it. The client
 * cannot check invariant 4 without it, so it is optional and the server is
 * still the authority.
 */
export function trimStart(
  document: Draftable,
  clipId: string,
  newStartMs: number,
  bounds: { minStartMs?: number } = {},
): void {
  const found = findMedia(document, clipId)
  if (!found) return
  const { track, index, clip } = found

  const previous = track.clips[index - 1]
  const floor = Math.max(
    0,
    bounds.minStartMs ?? 0,
    previous ? clipEndMs(previous) : 0,
    // Cannot pull the head back past the start of the media.
    clip.startMs - Math.floor(clip.sourceInMs / clip.speed),
  )
  const ceiling = clipEndMs(clip) - MIN_CLIP_MS
  const target = Math.min(ceiling, Math.max(floor, Math.round(newStartMs)))

  const delta = target - clip.startMs
  if (delta === 0) return
  clip.startMs = target
  clip.durationMs -= delta
  clip.sourceInMs = Math.max(0, clip.sourceInMs + Math.round(delta * clip.speed))
}

/**
 * Drag the right edge. Only the duration changes — the clip keeps reading from
 * the same place in its media.
 */
export function trimEnd(
  document: Draftable,
  clipId: string,
  newEndMs: number,
  bounds: { maxSourceMs?: number } = {},
): void {
  const found = findMedia(document, clipId)
  if (!found) return
  const { track, index, clip } = found

  const next = track.clips[index + 1]
  let ceiling = next ? next.startMs : Number.MAX_SAFE_INTEGER
  if (bounds.maxSourceMs !== undefined) {
    // Invariant 4, as far as the client can see it: the clip may not read past
    // the end of its media, and at 2x it consumes source twice as fast.
    const available = Math.max(0, bounds.maxSourceMs - clip.sourceInMs)
    ceiling = Math.min(ceiling, clip.startMs + Math.floor(available / clip.speed))
  }
  const floor = clip.startMs + MIN_CLIP_MS
  const target = Math.min(ceiling, Math.max(floor, Math.round(newEndMs)))
  clip.durationMs = target - clip.startMs
}

// --------------------------------------------------------------------------
// Moving
// --------------------------------------------------------------------------

/**
 * Move a clip along its track.
 *
 * Clamped between its neighbours rather than refused, and rather than pushing
 * them along: phase 1 has no magnetic timeline and no ripple edits, so a clip
 * dragged into its neighbour butts against it. That is predictable, and it
 * keeps the document valid at every intermediate position — which matters
 * because autosave can fire between two drags.
 */
export function moveClip(document: Draftable, clipId: string, startMs: number): void {
  const found = findMedia(document, clipId)
  if (!found) return
  const { track, index, clip } = found

  const previous = track.clips[index - 1]
  const next = track.clips[index + 1]
  const floor = previous ? clipEndMs(previous) : 0
  const ceiling = next ? next.startMs - clip.durationMs : Number.MAX_SAFE_INTEGER
  clip.startMs = Math.max(floor, Math.min(ceiling, Math.max(0, Math.round(startMs))))
  reorder(track)
}

// --------------------------------------------------------------------------
// Duplicating and removing
// --------------------------------------------------------------------------

/**
 * Copy a clip in immediately after itself, or at the end of the track when
 * there is no room. A duplicate that silently overlapped its original would
 * break invariant 1 the moment it was made.
 */
export function duplicateClip(document: Draftable, clipId: string): string | null {
  const found = findMedia(document, clipId)
  if (!found) return null
  const { track, index, clip } = found

  const next = track.clips[index + 1]
  const gap = next ? next.startMs - clipEndMs(clip) : Number.MAX_SAFE_INTEGER
  const startMs =
    gap >= clip.durationMs
      ? clipEndMs(clip)
      : track.clips.reduce((end, each) => Math.max(end, clipEndMs(each)), 0)

  const id = newClipId()
  const copy: Draft<MediaClip> = {
    ...clip,
    id,
    startMs,
    effects: clip.effects.map((effect) => ({ ...effect })),
  }
  track.clips.push(copy)
  reorder(track)
  return id
}

export function removeClips(document: Draftable, clipIds: Iterable<string>): void {
  const doomed = new Set(clipIds)
  if (doomed.size === 0) return
  for (const track of document.tracks) {
    track.clips = track.clips.filter((clip) => !doomed.has(clip.id)) as typeof track.clips
  }
  // A track left empty is kept. Removing it would delete the user's mute and
  // lock settings along with it, and re-adding a clip would silently restore
  // defaults they had changed.
}

// --------------------------------------------------------------------------
// Properties
// --------------------------------------------------------------------------

export interface ClipProperties {
  volume?: number
  speed?: number
  audioFadeInMs?: number
  audioFadeOutMs?: number
  rotation?: 0 | 90 | 180 | 270
  flipH?: boolean
  flipV?: boolean
  crop?: { x: number; y: number; width: number; height: number } | null
}

const clamp = (value: number, low: number, high: number) =>
  Math.min(high, Math.max(low, value))

/**
 * Clip properties — contract §4.2 ranges, enforced here so the interface
 * cannot produce a value the server will reject.
 *
 * **Changing `speed` holds the timeline duration and moves the source
 * window.** The alternative — holding the source window and stretching the
 * clip — would push every later clip along, which is a ripple edit, and phase
 * 1 does not have those.
 */
export function setClipProperties(
  document: Draftable,
  clipId: string,
  properties: ClipProperties,
): void {
  const found = findMedia(document, clipId)
  if (!found) return
  const { clip } = found

  if (properties.volume !== undefined) clip.volume = clamp(properties.volume, 0, 2)
  if (properties.speed !== undefined) clip.speed = clamp(properties.speed, 0.25, 4)
  if (properties.audioFadeInMs !== undefined) {
    clip.audioFadeInMs = clamp(Math.round(properties.audioFadeInMs), 0, clip.durationMs)
  }
  if (properties.audioFadeOutMs !== undefined) {
    clip.audioFadeOutMs = clamp(Math.round(properties.audioFadeOutMs), 0, clip.durationMs)
  }

  const spatial =
    properties.rotation !== undefined ||
    properties.flipH !== undefined ||
    properties.flipV !== undefined ||
    properties.crop !== undefined
  if (!spatial) return

  clip.transform ??= {
    scale: 1,
    offsetX: 0,
    offsetY: 0,
    rotation: 0,
    flipH: false,
    flipV: false,
    crop: null,
  }
  if (properties.rotation !== undefined) clip.transform.rotation = properties.rotation
  if (properties.flipH !== undefined) clip.transform.flipH = properties.flipH
  if (properties.flipV !== undefined) clip.transform.flipV = properties.flipV
  if (properties.crop !== undefined) clip.transform.crop = properties.crop
}

// --------------------------------------------------------------------------
// Transitions
// --------------------------------------------------------------------------

/**
 * Set or clear a transition — contract §4.3 invariant 7.
 *
 * Clamped to half the shorter of the two clips it joins. Longer than that and
 * the two overlaps meet in the middle: the renderer would be asked to read
 * past the end of one clip while still inside the other, and the picture would
 * depend on which side it evaluated first.
 */
export function setTransition(
  document: Draftable,
  clipId: string,
  side: 'in' | 'out',
  transition: Transition | null,
): void {
  const found = findMedia(document, clipId)
  if (!found) return
  const { track, index, clip } = found

  if (transition === null || transition.type === 'cut') {
    if (side === 'in') clip.transitionIn = null
    else clip.transitionOut = null
    return
  }

  const neighbour = side === 'in' ? track.clips[index - 1] : track.clips[index + 1]
  const shortest = Math.min(clip.durationMs, neighbour?.durationMs ?? clip.durationMs)
  const durationMs = clamp(Math.round(transition.durationMs), 0, Math.floor(shortest / 2))
  const value: Draft<Transition> = { type: transition.type, durationMs }
  if (side === 'in') clip.transitionIn = value
  else clip.transitionOut = value
}

// --------------------------------------------------------------------------
// Snapping
// --------------------------------------------------------------------------

/**
 * Snap a position to the nearest edge within `toleranceMs`, or leave it alone.
 *
 * The tolerance is in milliseconds and the caller converts it from pixels, so
 * snapping feels the same at every zoom: a fixed pixel tolerance would snap
 * across ten seconds when zoomed out.
 */
export function snapTo(candidates: number[], positionMs: number, toleranceMs: number): number {
  let best = positionMs
  let distance = toleranceMs
  for (const candidate of candidates) {
    const gap = Math.abs(candidate - positionMs)
    if (gap <= distance) {
      distance = gap
      best = candidate
    }
  }
  return best
}

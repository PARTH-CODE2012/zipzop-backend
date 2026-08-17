/**
 * The timeline document.
 *
 * Exactly the shape of docs/05-api-contract.md §4 — the frontend produces it,
 * the backend validates and stores it, the export renderer consumes it, and
 * there is deliberately no client-side variant and no mapping layer
 * (docs/04-frontend-architecture.md §3.1).
 *
 * ⚠️ **These types are hand-written, and that is temporary.** M3's first task
 * is *"Timeline document type generated from the OpenAPI schema"*, which is
 * only possible once `PATCH /projects/{id}` exists to put the schema in
 * `openapi.json`. Until then there is nothing to generate from. What is here
 * is the M2 subset — one video track, one clip — written to match §4 field for
 * field so the generated version replaces it without touching a caller.
 *
 * Conventions that are not negotiable, and that this file exists to hold:
 * times are **integer milliseconds**, spatial values are **normalised 0–1**
 * relative to the canvas, and nothing derived is ever stored — clip end times
 * and track durations are selectors, because a derived field is a field that
 * can be wrong.
 */

export type TrackKind = 'video' | 'audio' | 'text'

export interface Transform {
  scale: number
  offsetX: number
  offsetY: number
  rotation: number
  flipH: boolean
  flipV: boolean
  crop: { x: number; y: number; width: number; height: number } | null
}

export interface Effect {
  type: 'color_grade'
  lut: string
  strength: number
  sourceJobId?: string | null
}

export interface Transition {
  type: 'cut' | 'fade' | 'dissolve'
  durationMs: number
}

/** A clip on a `video` or `audio` track. */
export interface MediaClip {
  id: string
  assetId: string
  /** Where the clip begins on the timeline. */
  startMs: number
  /** How long it occupies on the timeline. Always > 0. */
  durationMs: number
  /** Where playback starts inside the asset. */
  sourceInMs: number
  /** 0.25–4.0. Source consumed is `durationMs × speed`. */
  speed: number
  /** 0.0–2.0. */
  volume: number
  audioFadeInMs: number
  audioFadeOutMs: number
  transform?: Transform
  effects?: Effect[]
  transitionIn?: Transition | null
  transitionOut?: Transition | null
}

export interface Track {
  id: string
  kind: TrackKind
  /** Layer order within the kind. Higher draws on top. */
  index: number
  muted: boolean
  locked: boolean
  clips: MediaClip[]
}

export interface TimelineDocument {
  schemaVersion: 1
  tracks: Track[]
}

export function emptyTimeline(): TimelineDocument {
  return { schemaVersion: 1, tracks: [] }
}

// --------------------------------------------------------------------------
// Selectors. Derived, never stored.
// --------------------------------------------------------------------------

/**
 * There is deliberately **no `sourceOutMs` field** — it is derivable, and
 * storing a derivable value invites the two to disagree with no way for the
 * renderer to know which is right (contract §4.2).
 */
export function sourceOutMs(clip: MediaClip): number {
  return clip.sourceInMs + Math.round(clip.durationMs * clip.speed)
}

export function clipEndMs(clip: MediaClip): number {
  return clip.startMs + clip.durationMs
}

export function trackDurationMs(track: Track): number {
  return track.clips.reduce((longest, clip) => Math.max(longest, clipEndMs(clip)), 0)
}

export function timelineDurationMs(document: TimelineDocument): number {
  return document.tracks.reduce((longest, track) => Math.max(longest, trackDurationMs(track)), 0)
}

/**
 * Timeline position to position inside the asset.
 *
 * The conversion M4 depends on for captions and smart trim, and the one the
 * M1 spike measured to a millisecond. Speed multiplies: two seconds into a
 * clip playing at 2× is four seconds into its media.
 */
export function timelineMsToAssetMs(clip: MediaClip, timelineMs: number): number {
  return clip.sourceInMs + Math.round((timelineMs - clip.startMs) * clip.speed)
}

export function assetMsToTimelineMs(clip: MediaClip, assetMs: number): number {
  return clip.startMs + Math.round((assetMs - clip.sourceInMs) / clip.speed)
}

/** The clip under a playhead position, or null in a gap. */
export function clipAt(track: Track, timelineMs: number): MediaClip | null {
  for (const clip of track.clips) {
    if (timelineMs >= clip.startMs && timelineMs < clipEndMs(clip)) return clip
  }
  return null
}

/** Every edge a drag can snap to: clip starts, clip ends, and zero. */
export function snapCandidates(document: TimelineDocument): number[] {
  const edges = new Set<number>([0])
  for (const track of document.tracks) {
    for (const clip of track.clips) {
      edges.add(clip.startMs)
      edges.add(clipEndMs(clip))
    }
  }
  return [...edges].sort((a, b) => a - b)
}

// --------------------------------------------------------------------------
// Invariants — contract §4.3
// --------------------------------------------------------------------------

/**
 * The subset of §4.3 the client can check on its own.
 *
 * Invariants 4 and 5 need the asset's duration and ownership, which only the
 * server knows, so they are not here. This is **not** a substitute for the
 * server's validation — plan limits and document validity are enforced
 * server-side, and this exists so the editor can refuse to build something
 * invalid rather than discover it at save time (which is M3).
 */
export function violatedInvariants(document: TimelineDocument): string[] {
  const problems: string[] = []
  const seenIds = new Set<string>()

  const note = (id: string) => {
    if (seenIds.has(id)) problems.push(`id ${id} appears more than once`)
    seenIds.add(id)
  }

  const byKind = new Map<TrackKind, number>()
  for (const track of document.tracks) {
    note(track.id)
    byKind.set(track.kind, (byKind.get(track.kind) ?? 0) + 1)

    let previousEnd = -1
    let previousStart = -1
    for (const clip of track.clips) {
      note(clip.id)
      if (clip.durationMs <= 0) problems.push(`clip ${clip.id} has no duration`)
      if (clip.startMs < previousStart) problems.push(`clip ${clip.id} is out of order`)
      if (clip.startMs < previousEnd) problems.push(`clip ${clip.id} overlaps the one before it`)
      previousStart = clip.startMs
      previousEnd = clipEndMs(clip)
    }
  }

  for (const [kind, count] of byKind) {
    // Phase 1 allows one track of each kind.
    if (count > 1) problems.push(`${count} ${kind} tracks; phase 1 allows one`)
  }
  return problems
}

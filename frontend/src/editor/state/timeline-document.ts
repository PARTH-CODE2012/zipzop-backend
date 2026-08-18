/**
 * The timeline document.
 *
 * **Every type here is an alias into `generated.ts`.** There is no client
 * variant and no mapping layer: the frontend produces this document, the
 * backend validates and stores it, the export renderer consumes it, and
 * `docs/04-frontend-architecture.md` §3.1 requires all three to be reading one
 * definition. When the contract changes, this file stops compiling — which is
 * the entire point of generating rather than writing them.
 *
 * The hand-written M2 subset that used to live here is gone. It was written to
 * match contract §4 field for field precisely so this swap would touch no
 * caller, and it did not.
 *
 * Conventions the module exists to hold: times are **integer milliseconds**,
 * spatial values are **normalised 0-1** relative to the canvas, and nothing
 * derived is ever stored — clip ends and track durations are functions,
 * because a derived field is a field that can disagree with what it came from.
 */

import type { components } from '@/lib/api/generated'

type Schemas = components['schemas']

export type TimelineDocument = Schemas['TimelineDocument']
export type MediaTrack = Schemas['MediaTrack']
export type TextTrack = Schemas['TextTrack']
/** Either kind. Discriminated on `kind`, so a `switch` narrows it. */
export type Track = MediaTrack | TextTrack
export type TrackKind = Track['kind']

export type MediaClip = Schemas['MediaClip']
export type TextClip = Schemas['TextClip']
export type AnyClip = MediaClip | TextClip

export type Transform = Schemas['Transform']
export type Crop = Schemas['Crop']
export type Effect = Schemas['ColorGradeEffect']
export type Transition = Schemas['Transition']
export type TextStyle = Schemas['TextStyle']
export type TextPosition = Schemas['TextPosition']

export const SCHEMA_VERSION = 1 as const

export function emptyTimeline(): TimelineDocument {
  return { schemaVersion: SCHEMA_VERSION, tracks: [] }
}

// --------------------------------------------------------------------------
// Narrowing
// --------------------------------------------------------------------------

export function isMediaTrack(track: Track): track is MediaTrack {
  return track.kind === 'video' || track.kind === 'audio'
}

export function isTextTrack(track: Track): track is TextTrack {
  return track.kind === 'text'
}

/**
 * The one track of a kind, or undefined.
 *
 * Phase 1 allows one track per kind (contract §4.3 invariant 8), which is why
 * this returns a single track rather than a list. Multiple video tracks are
 * phase 2, and when they arrive this signature changing is the compiler
 * telling every caller to look again.
 */
export function trackOfKind(document: TimelineDocument, kind: 'video' | 'audio'): MediaTrack | undefined
export function trackOfKind(document: TimelineDocument, kind: 'text'): TextTrack | undefined
export function trackOfKind(document: TimelineDocument, kind: TrackKind): Track | undefined {
  return document.tracks.find((track) => track.kind === kind)
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

export function clipEndMs(clip: AnyClip): number {
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
 * clip playing at 2x is four seconds into its media.
 */
export function timelineMsToAssetMs(clip: MediaClip, timelineMs: number): number {
  return clip.sourceInMs + Math.round((timelineMs - clip.startMs) * clip.speed)
}

export function assetMsToTimelineMs(clip: MediaClip, assetMs: number): number {
  return clip.startMs + Math.round((assetMs - clip.sourceInMs) / clip.speed)
}

/** The clip under a playhead position, or null in a gap. */
export function clipAt<ClipT extends AnyClip>(
  track: { clips: ClipT[] },
  timelineMs: number,
): ClipT | null {
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

/** Find a clip anywhere in the document, with the track that holds it. */
export function locateClip(
  document: TimelineDocument,
  clipId: string,
): { track: Track; clip: AnyClip; index: number } | null {
  for (const track of document.tracks) {
    const index = track.clips.findIndex((clip) => clip.id === clipId)
    const clip = index >= 0 ? track.clips[index] : undefined
    if (clip) return { track, clip, index }
  }
  return null
}

// --------------------------------------------------------------------------
// Invariants — contract §4.3
// --------------------------------------------------------------------------

/**
 * The subset of §4.3 the client can check on its own.
 *
 * Invariants 4 and 5 need the asset's duration and ownership, which only the
 * server knows, so they are not here. This is **not** a substitute for the
 * server's validation — it exists so the editor can refuse to build something
 * invalid rather than discover it when the autosave comes back 422, two
 * seconds and several edits later.
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
    if (count > 1) problems.push(`${count} ${kind} tracks; phase 1 allows one`)
  }
  return problems
}

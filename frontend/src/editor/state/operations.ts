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
  type TextClip,
  type TextTrack,
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

/**
 * Any clip on **any** track, media or text.
 *
 * `findMedia` above searches video and audio only, which is right for the
 * operations that need a `sourceInMs` or a `speed` to mean anything — a volume,
 * a colour grade, a transition. It was wrong for the ones that only need a
 * position: **moving, trimming, splitting and duplicating a title or a caption
 * all silently did nothing**, because the lookup they went through could not
 * see the text track at all. The gesture ran, the pointer moved, and the
 * operation returned without touching the document.
 *
 * A discriminated result rather than a widened one, because the two kinds
 * genuinely differ: a media clip has media behind it and a text clip does not,
 * and the trims below have to know which they are holding.
 */
type FoundClip =
  | { kind: 'media'; track: Draft<MediaTrack>; index: number; clip: Draft<MediaClip> }
  | { kind: 'text'; track: Draft<TextTrack>; index: number; clip: Draft<TextClip> }

function findAnyClip(document: Draftable, clipId: string): FoundClip | null {
  for (const track of document.tracks) {
    const index = track.clips.findIndex((clip) => clip.id === clipId)
    if (index < 0) continue
    if (isMediaTrack(track as Track)) {
      const media = track as Draft<MediaTrack>
      return { kind: 'media', track: media, index, clip: media.clips[index]! }
    }
    const text = track as Draft<TextTrack>
    return { kind: 'text', track: text, index, clip: text.clips[index]! }
  }
  return null
}

/**
 * The shortest a clip of each kind may be trimmed to.
 *
 * A caption is one word and can legitimately be very short; a media clip that
 * brief is a frame of noise nobody meant to keep.
 */
function minDurationFor(found: FoundClip): number {
  return found.kind === 'text' ? MIN_CAPTION_MS : MIN_CLIP_MS
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
/** Invariant 2: ascending `startMs`, on any track. */
function reorder(track: { clips: { startMs: number }[] }): void {
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
  const found = findAnyClip(document, clipId)
  if (!found) return null

  const { index, clip } = found
  const at = Math.round(timelineMs)
  const elapsed = at - clip.startMs
  const remaining = clipEndMs(clip) - at
  const minimum = minDurationFor(found)
  if (elapsed < minimum || remaining < minimum) return null

  if (found.kind === 'text') {
    // Both halves keep the words. Splitting the *text* at a character would be
    // a guess about where the sentence divides; splitting the timing is what
    // the gesture on the timeline actually means, and the user can retype
    // either half afterwards.
    const rightTextId = newClipId()
    found.clip.durationMs = elapsed
    found.track.clips.splice(index + 1, 0, {
      ...found.clip,
      id: rightTextId,
      startMs: at,
      durationMs: remaining,
      position: found.clip.position ? { ...found.clip.position } : null,
    } as Draft<TextClip>)
    return rightTextId
  }

  const source = found.clip
  const rightId = newClipId()
  const right: Draft<MediaClip> = {
    ...source,
    id: rightId,
    startMs: at,
    durationMs: remaining,
    sourceInMs: source.sourceInMs + Math.round(elapsed * source.speed),
    // The two halves meet at a cut. A transition belongs to the outside edges
    // of the pair — duplicating it onto the new join would invent a crossfade
    // the user never asked for.
    transitionIn: null,
    transitionOut: source.transitionOut ?? null,
    // Effects follow the picture, so both halves keep the grade. Immer's draft
    // is structurally shared, so this copy is cheap.
    effects: source.effects.map((effect) => ({ ...effect })),
  }

  found.clip.durationMs = elapsed
  found.clip.transitionOut = null
  found.track.clips.splice(index + 1, 0, right)
  // Both halves are shorter than the clip they came from, so a transition on
  // either outside edge may no longer fit under invariant 7.
  clampTransitions(found.track)
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
  const found = findAnyClip(document, clipId)
  if (!found) return
  const { track, index, clip } = found

  const previous = track.clips[index - 1]
  const floor = Math.max(
    0,
    bounds.minStartMs ?? 0,
    previous ? clipEndMs(previous) : 0,
    // Cannot pull the head back past the start of the media. A text clip has
    // no media, so nothing but its neighbour stops it.
    found.kind === 'media'
      ? found.clip.startMs - Math.floor(found.clip.sourceInMs / found.clip.speed)
      : 0,
  )
  const ceiling = clipEndMs(clip) - minDurationFor(found)
  const target = Math.min(ceiling, Math.max(floor, Math.round(newStartMs)))

  const delta = target - clip.startMs
  if (delta === 0) return
  clip.startMs = target
  clip.durationMs -= delta

  if (found.kind === 'media') {
    // `startMs` and `sourceInMs` move together, or the picture slides.
    found.clip.sourceInMs = Math.max(
      0,
      found.clip.sourceInMs + Math.round(delta * found.clip.speed),
    )
    clampTransitions(found.track)
  }
}

/**
 * The furthest a clip's right edge may go before it reads past its media —
 * invariant 4, as far as the client can see it.
 *
 * At 2x a clip consumes source twice as fast, so the headroom left in the file
 * buys half as much timeline. Exported because the *preview* during a trim has
 * to agree with the commit at the end of it: an edge that follows the pointer
 * and then snaps back on release looks like the editor lost the gesture.
 */
export function maxTrimEndMs(
  clip: { startMs: number; sourceInMs: number; speed: number },
  maxSourceMs: number,
): number {
  const available = Math.max(0, maxSourceMs - clip.sourceInMs)
  return clip.startMs + Math.floor(available / clip.speed)
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
  const found = findAnyClip(document, clipId)
  if (!found) return
  const { track, index, clip } = found

  const next = track.clips[index + 1]
  let ceiling = next ? next.startMs : Number.MAX_SAFE_INTEGER
  // Only media can read past the end of something. A title is as long as the
  // user says it is.
  if (found.kind === 'media' && bounds.maxSourceMs !== undefined) {
    ceiling = Math.min(ceiling, maxTrimEndMs(found.clip, bounds.maxSourceMs))
  }
  const floor = clip.startMs + minDurationFor(found)
  const target = Math.min(ceiling, Math.max(floor, Math.round(newEndMs)))
  clip.durationMs = target - clip.startMs
  if (found.kind === 'media') clampTransitions(found.track)
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
  const found = findAnyClip(document, clipId)
  if (!found) return
  const { track, index, clip } = found

  // Identical arithmetic for a title and for a video clip: invariant 1 applies
  // per track whatever the track holds, and a position is a position.
  const previous = track.clips[index - 1]
  const next = track.clips[index + 1]
  const floor = previous ? clipEndMs(previous) : 0
  const ceiling = next ? next.startMs - clip.durationMs : Number.MAX_SAFE_INTEGER
  clip.startMs = Math.max(floor, Math.min(ceiling, Math.max(0, Math.round(startMs))))
  reorder(track)
  // Only media carries transitions, and moving can change which clip is the
  // neighbour the bound is measured against.
  if (found.kind === 'media') clampTransitions(found.track)
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
  const found = findAnyClip(document, clipId)
  if (!found) return null
  const { track, index, clip } = found

  const next = track.clips[index + 1]
  const gap = next ? next.startMs - clipEndMs(clip) : Number.MAX_SAFE_INTEGER
  const startMs =
    gap >= clip.durationMs
      ? clipEndMs(clip)
      : track.clips.reduce((end, each) => Math.max(end, clipEndMs(each)), 0)

  const id = newClipId()
  if (found.kind === 'media') {
    found.track.clips.push({
      ...found.clip,
      id,
      startMs,
      effects: found.clip.effects.map((effect) => ({ ...effect })),
    } as Draft<MediaClip>)
    reorder(found.track)
    clampTransitions(found.track)
    return id
  }

  found.track.clips.push({
    ...found.clip,
    id,
    startMs,
    // The copy is the user's now, not the tool's: a duplicated caption that
    // kept its `sourceJobId` would claim to have come from a transcription it
    // was never part of.
    sourceJobId: null,
    position: found.clip.position ? { ...found.clip.position } : null,
  } as Draft<TextClip>)
  reorder(found.track)
  return id
}

export function removeClips(document: Draftable, clipIds: Iterable<string>): void {
  const doomed = new Set(clipIds)
  if (doomed.size === 0) return
  for (const track of document.tracks) {
    track.clips = track.clips.filter((clip) => !doomed.has(clip.id)) as typeof track.clips
  }
  // Deleting a clip hands its neighbours a new partner, which may be shorter.
  for (const track of mediaTracks(document)) clampTransitions(track)
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

  const durationMs = clamp(
    Math.round(transition.durationMs),
    0,
    transitionLimitMs(track.clips, index, side),
  )
  const value: Draft<Transition> = { type: transition.type, durationMs }
  if (side === 'in') clip.transitionIn = value
  else clip.transitionOut = value
}

/** Half the shorter of the two clips a transition joins — invariant 7's bound. */
function transitionLimitMs(
  clips: readonly Draft<MediaClip>[],
  index: number,
  side: 'in' | 'out',
): number {
  const clip = clips[index]
  if (!clip) return 0
  const neighbour = side === 'in' ? clips[index - 1] : clips[index + 1]
  const shortest = Math.min(clip.durationMs, neighbour?.durationMs ?? clip.durationMs)
  return Math.floor(shortest / 2)
}

/**
 * Bring every transition on a track back inside invariant 7.
 *
 * **A transition is clamped when it is set, and that is not enough**: the bound
 * depends on two clip *durations*, so trimming a clip after the fact — or
 * splitting it, or moving a neighbour away — can put a transition that was
 * legal over the line without touching it. The server checks the invariant on
 * every save, so the result is a `422` two seconds and several edits later,
 * naming a clip the user was not editing, with autosave then stuck. Anything
 * that changes a duration or a neighbour calls this.
 *
 * One pass over the track, and it writes only where the value actually changes,
 * so a no-op produces no Immer patch and therefore no history entry.
 */
export function clampTransitions(track: Draft<MediaTrack>): void {
  track.clips.forEach((clip, index) => {
    for (const side of ['in', 'out'] as const) {
      const transition = side === 'in' ? clip.transitionIn : clip.transitionOut
      if (!transition) continue
      const limit = transitionLimitMs(track.clips, index, side)
      if (transition.durationMs <= limit) continue
      if (limit <= 0) {
        // Nothing left to dissolve across. A zero-length transition is stored
        // as no transition at all, the same rule the inspector's "cut" follows.
        if (side === 'in') clip.transitionIn = null
        else clip.transitionOut = null
      } else {
        transition.durationMs = limit
      }
    }
  })
}

// --------------------------------------------------------------------------
// The text track
// --------------------------------------------------------------------------

/** The style every typed title starts from. Captions bring their own in M4. */
export const DEFAULT_TITLE_STYLE_ID = 'plain_bold'

//: A caption is one word, and a word can be very short. 80 ms is about the
//: shortest a viewer can read anything at all, and it keeps a fast passage from
//: producing clips too small to click.
export const MIN_CAPTION_MS = 80

//: The caption catalogue's default. ⚠️ Only this one has been designed — the
//: checklist asks for three (PHASE1-TASKS M4).
export const DEFAULT_CAPTION_STYLE_ID = 'caption_bold'

export function ensureTextTrack(document: Draftable): Draft<TextTrack> {
  const existing = document.tracks.find((track) => track.kind === 'text')
  if (existing) return existing as Draft<TextTrack>
  const track = {
    id: newTrackId('text'),
    kind: 'text',
    index: 0,
    muted: false,
    locked: false,
    clips: [],
  } as Draft<TextTrack>
  document.tracks.push(track)
  return track
}

/**
 * Add a typed title — contract §4.2, `kind: "title"`.
 *
 * Placed at the playhead rather than appended, because a title is positioned
 * against the picture underneath it and the end of the track is almost never
 * where the user is looking. If the playhead sits inside an existing title the
 * new one lands after it, since invariant 1 forbids the overlap.
 */
export function appendTitle(
  document: Draftable,
  input: { text: string; startMs: number; durationMs?: number },
): string {
  const track = ensureTextTrack(document)
  const id = newClipId()
  const durationMs = Math.max(MIN_CLIP_MS, Math.round(input.durationMs ?? 3_000))

  let startMs = Math.max(0, Math.round(input.startMs))
  for (const clip of track.clips) {
    if (startMs < clip.startMs + clip.durationMs && startMs + durationMs > clip.startMs) {
      startMs = clip.startMs + clip.durationMs
    }
  }

  track.clips.push({
    id,
    kind: 'title',
    startMs,
    durationMs,
    text: input.text,
    styleId: DEFAULT_TITLE_STYLE_ID,
    // Normalised, never pixels: this is what lets a 480p preview and a 1080p
    // export put the words in the same place (contract §4.3).
    position: { x: 0.5, y: 0.82, anchor: 'center' },
    emphasis: 0,
  })
  track.clips.sort((a, b) => a.startMs - b.startMs)
  return id
}

/**
 * Lay a caption run onto the text track — **one operation for all of it**.
 *
 * A minute of speech is around 150 words and therefore 150 clips. Adding them
 * one at a time would put 150 entries in the undo stack, and undoing a captions
 * run the user did not like would mean 150 presses of ⌘Z. The store commits
 * this once, so it is one entry (PHASE1-TASKS M4: *"one undo step for 1,800
 * clips"*).
 *
 * Existing captions in the same span are replaced rather than added to. Running
 * the tool twice is what a user does after correcting the audio or picking a
 * different language, and doubling every word is never what they meant.
 */
export function applyCaptions(
  document: Draftable,
  input: {
    words: readonly {
      text: string
      startMs: number
      durationMs: number
      emphasis: number
      confidence: number
    }[]
    /** The span the run covers, so a re-run replaces rather than duplicates. */
    fromMs: number
    toMs: number
    styleId?: string
    /** Which job produced these — contract §4.2's `sourceJobId`. It is what
     * lets the interface say "from Captions" and offer to re-run, and what a
     * later session uses to find the confidences again. */
    sourceJobId?: string | null
  },
): string[] {
  const track = ensureTextTrack(document)
  const styleId = input.styleId ?? DEFAULT_CAPTION_STYLE_ID

  // Only captions are cleared. A title the user typed is theirs, and a tool
  // silently deleting hand-written text would be unforgivable.
  track.clips = track.clips.filter(
    (clip) =>
      clip.kind !== 'caption' ||
      clip.startMs + clip.durationMs <= input.fromMs ||
      clip.startMs >= input.toMs,
  ) as typeof track.clips

  const ids: string[] = []
  for (const word of input.words) {
    const id = newClipId()
    ids.push(id)
    track.clips.push({
      id,
      kind: 'caption',
      startMs: Math.max(0, Math.round(word.startMs)),
      durationMs: Math.max(MIN_CAPTION_MS, Math.round(word.durationMs)),
      text: word.text,
      styleId,
      position: { x: 0.5, y: 0.82, anchor: 'center' },
      emphasis: word.emphasis,
      sourceJobId: input.sourceJobId ?? null,
    })
  }

  track.clips.sort((a, b) => a.startMs - b.startMs)
  return ids
}

/**
 * Apply a smart-trim result: split the clip and close the gaps.
 *
 * The removals arrive as ranges; what the user expects is the clip shortened
 * with the remaining pieces butted together, so everything after them moves
 * left by what was cut. That ripple is confined to **this clip's own track** —
 * phase 1 has no magnetic timeline, and shifting every track would move a music
 * bed that was never in sync with the dialogue anyway.
 *
 * ⚠️ Captions already on the text track do **not** follow. Trim first, caption
 * second; the interface says so before it runs.
 */
export function applySmartTrim(
  document: Draftable,
  clipId: string,
  removals: readonly { startMs: number; endMs: number }[],
): string[] {
  const found = findMedia(document, clipId)
  if (!found || removals.length === 0) return []
  const { track, index, clip } = found

  const clipStart = clip.startMs
  const clipEnd = clip.startMs + clip.durationMs

  // What survives, in timeline time, before anything moves.
  const kept: { startMs: number; endMs: number }[] = []
  let cursor = clipStart
  for (const range of [...removals].sort((a, b) => a.startMs - b.startMs)) {
    const from = Math.max(clipStart, Math.round(range.startMs))
    const to = Math.min(clipEnd, Math.round(range.endMs))
    if (to <= from) continue
    if (from - cursor >= MIN_CLIP_MS) kept.push({ startMs: cursor, endMs: from })
    cursor = Math.max(cursor, to)
  }
  if (clipEnd - cursor >= MIN_CLIP_MS) kept.push({ startMs: cursor, endMs: clipEnd })

  // Everything removed would leave no clip at all. Deleting it outright is a
  // worse surprise than leaving the shortest legal piece.
  if (kept.length === 0) return []

  const pieces: Draft<MediaClip>[] = []
  const ids: string[] = []
  let placedAt = clipStart
  for (const [pieceIndex, segment] of kept.entries()) {
    const durationMs = segment.endMs - segment.startMs
    // Each piece keeps reading from where it was: the source offset is the
    // elapsed *timeline* time it started at, times speed.
    const sourceInMs =
      clip.sourceInMs + Math.round((segment.startMs - clipStart) * clip.speed)
    const id = pieceIndex === 0 ? clip.id : newClipId()
    ids.push(id)
    pieces.push({
      ...clip,
      id,
      startMs: placedAt,
      durationMs,
      sourceInMs,
      // Only the outside edges keep their transitions: an internal cut is a
      // cut, and a dissolve there would be a dissolve to the same footage.
      transitionIn: pieceIndex === 0 ? clip.transitionIn : null,
      transitionOut: pieceIndex === kept.length - 1 ? clip.transitionOut : null,
    } as Draft<MediaClip>)
    placedAt += durationMs
  }

  const shiftMs = clipEnd - placedAt
  const after = track.clips.slice(index + 1).map((later) => ({
    ...later,
    startMs: Math.max(0, later.startMs - shiftMs),
  })) as typeof track.clips

  track.clips = [...track.clips.slice(0, index), ...pieces, ...after] as typeof track.clips
  reorder(track)
  clampTransitions(track)
  return ids
}

/**
 * Write a colour-analysis result onto a clip.
 *
 * One `effects` entry, replacing any grade already there — a clip has one look,
 * and stacking two LUTs produces a picture neither of them describes. The
 * browser applies it immediately from the same `.cube` file the renderer will
 * use at export, which is what makes the preview and the output agree
 * (contract §4.4).
 */
export function applyColorGrade(
  document: Draftable,
  clipId: string,
  grade: { lut: string; strength: number; sourceJobId?: string | null },
): void {
  const found = findMedia(document, clipId)
  if (!found) return
  const { clip } = found

  const others = clip.effects.filter((effect) => effect.type !== 'color_grade')
  clip.effects = [
    ...others,
    {
      type: 'color_grade',
      lut: grade.lut,
      strength: clamp(grade.strength, 0, 1),
      sourceJobId: grade.sourceJobId ?? null,
    },
  ] as typeof clip.effects
}

/** Edit a title's words. The whole reason captions are clips and not a burn-in. */
export function setText(document: Draftable, clipId: string, text: string): void {
  for (const track of document.tracks) {
    if (track.kind !== 'text') continue
    const clip = (track as Draft<TextTrack>).clips.find((each) => each.id === clipId)
    if (clip) {
      clip.text = text.slice(0, 2_000)
      return
    }
  }
}

// --------------------------------------------------------------------------
// Track state
// --------------------------------------------------------------------------

/**
 * Mute or unmute a lane.
 *
 * Editor state that the renderer honours (contract §4.2), so it lives in the
 * document and is undoable like anything else — a mute the user cannot take
 * back with ⌘Z is a surprise.
 */
export function setTrackMuted(document: Draftable, trackId: string, muted: boolean): void {
  const track = document.tracks.find((each) => each.id === trackId)
  if (track) track.muted = muted
}

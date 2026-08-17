'use client'

/**
 * Editor state.
 *
 * Two kinds of state, kept apart (docs/04-frontend-architecture.md §2):
 * **server state** — assets, the account, credits — belongs to TanStack Query;
 * **editor state** — the open timeline, selection, playhead, zoom — belongs
 * here. Mixing them is the most common way this kind of application goes
 * wrong: the timeline ends up in a query cache and every clip drag triggers a
 * refetch. The timeline is never server state.
 *
 * ⚠️ **M2 does not persist.** Nothing here saves, and closing the tab loses the
 * arrangement — that is the milestone boundary, not an oversight. M3 is
 * literally titled *"Editing that survives a reload"* and adds
 * `commit(label, recipe)` over `produceWithPatches`, undo/redo from Immer
 * patches, `version`/`isDirty`, and autosave. The shape below is chosen so
 * that lands on top rather than replacing it.
 */

import { create } from 'zustand'

import {
  clipAt,
  emptyTimeline,
  timelineDurationMs,
  type MediaClip,
  type TimelineDocument,
} from '@/editor/state/timeline-document'
import { DEFAULT_ZOOM, clampZoom, type Zoom } from '@/editor/timeline/scale'

const VIDEO_TRACK_ID = 'trk_video'

export interface EditorState {
  timeline: TimelineDocument
  /** Integer milliseconds, like every other position. */
  playheadMs: number
  zoom: Zoom
  selectedClipId: string | null
  isPlaying: boolean

  addClip: (input: { assetId: string; durationMs: number }) => string
  removeClip: (clipId: string) => void
  moveClip: (clipId: string, startMs: number) => void
  select: (clipId: string | null) => void
  setPlayhead: (ms: number) => void
  setZoom: (zoom: Zoom) => void
  setPlaying: (playing: boolean) => void
  reset: () => void
}

function videoTrack(timeline: TimelineDocument) {
  return timeline.tracks.find((track) => track.kind === 'video')
}

export const useEditor = create<EditorState>((set) => ({
  timeline: emptyTimeline(),
  playheadMs: 0,
  zoom: DEFAULT_ZOOM,
  selectedClipId: null,
  isPlaying: false,

  addClip: ({ assetId, durationMs }) => {
    const id = `clp_${crypto.randomUUID().slice(0, 8)}`
    set((state) => {
      const timeline: TimelineDocument = {
        ...state.timeline,
        tracks: [...state.timeline.tracks],
      }
      let track = videoTrack(timeline)
      if (!track) {
        track = { id: VIDEO_TRACK_ID, kind: 'video', index: 0, muted: false, locked: false, clips: [] }
        timeline.tracks.push(track)
      }

      // Appended after whatever is already there. Clips within a track never
      // overlap (§4.3 invariant 1), so the end of the track is the only
      // position that is always legal without moving anything else.
      const startMs = timelineDurationMs(timeline)
      const clip: MediaClip = {
        id,
        assetId,
        startMs,
        durationMs,
        sourceInMs: 0,
        speed: 1,
        volume: 1,
        audioFadeInMs: 0,
        audioFadeOutMs: 0,
      }
      const index = timeline.tracks.indexOf(track)
      timeline.tracks[index] = { ...track, clips: [...track.clips, clip] }
      return { timeline, selectedClipId: id }
    })
    return id
  },

  removeClip: (clipId) =>
    set((state) => ({
      timeline: {
        ...state.timeline,
        tracks: state.timeline.tracks.map((track) => ({
          ...track,
          clips: track.clips.filter((clip) => clip.id !== clipId),
        })),
      },
      selectedClipId: state.selectedClipId === clipId ? null : state.selectedClipId,
    })),

  moveClip: (clipId, startMs) =>
    set((state) => ({
      timeline: {
        ...state.timeline,
        tracks: state.timeline.tracks.map((track) => ({
          ...track,
          clips: track.clips
            .map((clip) =>
              // Never negative: a clip before time zero cannot be rendered and
              // cannot be exported.
              clip.id === clipId ? { ...clip, startMs: Math.max(0, Math.round(startMs)) } : clip,
            )
            // Clips within a track are ordered by ascending startMs
            // (§4.3 invariant 2), so the order is restored on every move
            // rather than repaired at save time.
            .sort((a, b) => a.startMs - b.startMs),
        })),
      },
    })),

  select: (clipId) => set({ selectedClipId: clipId }),

  setPlayhead: (ms) => set({ playheadMs: Math.max(0, Math.round(ms)) }),

  setZoom: (zoom) => set({ zoom: clampZoom(zoom) }),

  setPlaying: (playing) => set({ isPlaying: playing }),

  reset: () =>
    set({
      timeline: emptyTimeline(),
      playheadMs: 0,
      selectedClipId: null,
      isPlaying: false,
    }),
}))

// --------------------------------------------------------------------------
// Selectors. Memoised at the call site by Zustand's equality check; nothing
// derived is ever stored in the document.
// --------------------------------------------------------------------------

export function selectDurationMs(state: EditorState): number {
  return timelineDurationMs(state.timeline)
}

export function selectClipUnderPlayhead(state: EditorState): MediaClip | null {
  const track = videoTrack(state.timeline)
  return track ? clipAt(track, state.playheadMs) : null
}

/**
 * One shared empty array, never a fresh `[]`.
 *
 * Zustand compares what a selector returns by reference to decide whether to
 * re-render. `?? []` builds a new array on every call, so the value is never
 * equal to the last one, the component re-renders, the selector runs again —
 * and React fails with "Maximum update depth exceeded" the moment the
 * timeline has no video track, which is its state on first paint.
 *
 * Found by the end-to-end run, not by any unit test: the selector is correct
 * in isolation and only misbehaves once a React subscription is reading it.
 */
const NO_CLIPS: readonly MediaClip[] = Object.freeze([])

export function selectClips(state: EditorState): readonly MediaClip[] {
  return videoTrack(state.timeline)?.clips ?? NO_CLIPS
}

/** Exposed for the end-to-end harness, which drives the page from outside. */
export { VIDEO_TRACK_ID }

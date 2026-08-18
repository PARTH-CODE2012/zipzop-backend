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
 * Three rules this store exists to enforce:
 *
 * 1. **Every document change goes through `commit`.** That is what records the
 *    Immer patches undo is built from, and what marks the document dirty for
 *    autosave. A `set()` that touches `timeline` directly is a bug.
 * 2. **A drag does not commit.** Pointer movement writes to `drag`, which is
 *    local preview state outside the document; the drop commits once. Per-move
 *    commits would put two hundred entries in the undo stack for one gesture
 *    and queue an autosave for each.
 * 3. **Nothing derived is stored.** Duration, the selected clip, the clip
 *    under the playhead are all selectors.
 */

import { create } from 'zustand'

import {
  canRedo,
  canUndo,
  commit as applyCommit,
  emptyHistory,
  redo as applyRedo,
  undo as applyUndo,
  type History,
} from '@/editor/state/history'
import * as ops from '@/editor/state/operations'
import {
  clipAt,
  emptyTimeline,
  locateClip,
  timelineDurationMs,
  trackOfKind,
  type MediaClip,
  type TimelineDocument,
  type Transition,
} from '@/editor/state/timeline-document'
import { DEFAULT_ZOOM, clampZoom, type Zoom } from '@/editor/timeline/scale'

export const VIDEO_TRACK_ID = 'trk_video'

/** What a pointer is currently dragging. Never in the document. */
export interface DragState {
  kind: 'move' | 'trim-start' | 'trim-end'
  clipId: string
  /** The position the gesture is proposing, already snapped. */
  previewMs: number
}

export interface EditorState {
  // ------------------------------------------------------------- document
  projectId: string | null
  timeline: TimelineDocument
  /** The version this document was loaded or last saved at (contract §5). */
  version: number
  /** Set by `commit`, cleared by `markSaved`. Autosave reads it. */
  isDirty: boolean
  history: History

  // -------------------------------------------------------------- editing
  selection: ReadonlySet<string>
  playheadMs: number
  zoom: Zoom
  isPlaying: boolean
  drag: DragState | null

  // ------------------------------------------------------------- document
  load: (input: { projectId: string; timeline: TimelineDocument; version: number }) => void
  commit: (label: string, recipe: (draft: TimelineDocument) => void) => boolean
  undo: () => void
  redo: () => void
  markSaved: (version: number, savedTimeline?: TimelineDocument) => void
  reset: () => void

  // ------------------------------------------------------------- editing
  addClip: (input: { assetId: string; durationMs: number }) => string
  splitAtPlayhead: () => string | null
  moveClip: (clipId: string, startMs: number) => void
  trimStart: (clipId: string, startMs: number) => void
  trimEnd: (clipId: string, endMs: number, bounds?: { maxSourceMs?: number }) => void
  duplicateSelection: () => void
  deleteSelection: () => void
  setClipProperties: (clipId: string, properties: ops.ClipProperties) => void
  setTransition: (clipId: string, side: 'in' | 'out', transition: Transition | null) => void

  // ------------------------------------------------------------ selection
  select: (clipId: string | null, options?: { additive?: boolean }) => void
  selectMany: (clipIds: Iterable<string>) => void

  // ------------------------------------------------------------- transport
  setPlayhead: (ms: number) => void
  setZoom: (zoom: Zoom) => void
  setPlaying: (playing: boolean) => void

  // ----------------------------------------------------------------- drag
  beginDrag: (drag: DragState) => void
  updateDrag: (previewMs: number) => void
  endDrag: () => void
  cancelDrag: () => void
}

const NO_SELECTION: ReadonlySet<string> = Object.freeze(new Set<string>())

export const useEditor = create<EditorState>((set, get) => ({
  projectId: null,
  timeline: emptyTimeline(),
  version: 0,
  isDirty: false,
  history: emptyHistory(),

  selection: NO_SELECTION,
  playheadMs: 0,
  zoom: DEFAULT_ZOOM,
  isPlaying: false,
  drag: null,

  // ------------------------------------------------------------- document

  load: ({ projectId, timeline, version }) =>
    set({
      projectId,
      timeline,
      version,
      // A freshly loaded document is not dirty and has no history. Keeping the
      // previous project's undo stack would let ⌘Z apply patches recorded
      // against a document that is no longer open.
      isDirty: false,
      history: emptyHistory(),
      selection: NO_SELECTION,
      playheadMs: 0,
      drag: null,
    }),

  commit: (label, recipe) => {
    const state = get()
    const result = applyCommit(state.timeline, state.history, label, recipe)
    if (!result.changed) return false
    set({ timeline: result.state, history: result.history, isDirty: true })
    return true
  },

  undo: () => {
    const state = get()
    if (state.drag) return // A gesture in flight owns the document's shape.
    const result = applyUndo(state.timeline, state.history)
    if (!result.changed) return
    set({ timeline: result.state, history: result.history, isDirty: true })
  },

  redo: () => {
    const state = get()
    if (state.drag) return
    const result = applyRedo(state.timeline, state.history)
    if (!result.changed) return
    set({ timeline: result.state, history: result.history, isDirty: true })
  },

  /**
   * The save landed.
   *
   * `savedTimeline` is the snapshot the request actually carried, and it has to
   * be passed in: an edit made during the few hundred milliseconds the save was
   * in flight is **not** in that snapshot, so clearing `isDirty` unconditionally
   * strands it. Nothing would send it until the next change, and closing the tab
   * in between loses it.
   *
   * Compared by reference, which is exactly right here — every commit produces a
   * new document object, so a different reference means an edit landed.
   */
  markSaved: (version, savedTimeline) =>
    set((state) => ({
      version,
      isDirty: savedTimeline !== undefined ? state.timeline !== savedTimeline : false,
    })),

  reset: () =>
    set({
      projectId: null,
      timeline: emptyTimeline(),
      version: 0,
      isDirty: false,
      history: emptyHistory(),
      selection: NO_SELECTION,
      playheadMs: 0,
      isPlaying: false,
      drag: null,
    }),

  // -------------------------------------------------------------- editing

  addClip: ({ assetId, durationMs }) => {
    let id = ''
    get().commit('Add clip', (draft) => {
      id = ops.appendClip(draft, { assetId, durationMs })
    })
    if (id) set({ selection: new Set([id]) })
    return id
  },

  splitAtPlayhead: () => {
    const { timeline, playheadMs } = get()
    // Whatever the playhead is inside, selected or not. Splitting a clip the
    // user cannot see the playhead crossing would be a surprise, so the
    // selection deliberately does not widen this.
    const track = trackOfKind(timeline, 'video')
    const target = track ? clipAt(track, playheadMs)?.id : undefined
    if (!target) return null

    let created: string | null = null
    get().commit('Split clip', (draft) => {
      created = ops.splitAt(draft, target, playheadMs)
    })
    if (created) set({ selection: new Set([created]) })
    return created
  },

  moveClip: (clipId, startMs) => {
    get().commit('Move clip', (draft) => ops.moveClip(draft, clipId, startMs))
  },

  trimStart: (clipId, startMs) => {
    get().commit('Trim clip', (draft) => ops.trimStart(draft, clipId, startMs))
  },

  trimEnd: (clipId, endMs, bounds) => {
    get().commit('Trim clip', (draft) => ops.trimEnd(draft, clipId, endMs, bounds ?? {}))
  },

  duplicateSelection: () => {
    const ids = [...get().selection]
    if (ids.length === 0) return
    const created: string[] = []
    get().commit(ids.length > 1 ? 'Duplicate clips' : 'Duplicate clip', (draft) => {
      for (const id of ids) {
        const copy = ops.duplicateClip(draft, id)
        if (copy) created.push(copy)
      }
    })
    if (created.length) set({ selection: new Set(created) })
  },

  deleteSelection: () => {
    const ids = [...get().selection]
    if (ids.length === 0) return
    const changed = get().commit(ids.length > 1 ? 'Delete clips' : 'Delete clip', (draft) =>
      ops.removeClips(draft, ids),
    )
    if (changed) set({ selection: NO_SELECTION })
  },

  setClipProperties: (clipId, properties) => {
    get().commit('Change clip', (draft) => ops.setClipProperties(draft, clipId, properties))
  },

  setTransition: (clipId, side, transition) => {
    get().commit('Change transition', (draft) =>
      ops.setTransition(draft, clipId, side, transition),
    )
  },

  // ------------------------------------------------------------ selection

  select: (clipId, options) =>
    set((state) => {
      if (clipId === null) return { selection: NO_SELECTION }
      if (!options?.additive) return { selection: new Set([clipId]) }
      const next = new Set(state.selection)
      // Shift-click on something already selected removes it, which is what
      // every other editor does and what makes a mis-click recoverable.
      if (next.has(clipId)) next.delete(clipId)
      else next.add(clipId)
      return { selection: next.size ? next : NO_SELECTION }
    }),

  selectMany: (clipIds) => {
    const next = new Set(clipIds)
    set({ selection: next.size ? next : NO_SELECTION })
  },

  // ------------------------------------------------------------- transport

  setPlayhead: (ms) => set({ playheadMs: Math.max(0, Math.round(ms)) }),
  setZoom: (zoom) => set({ zoom: clampZoom(zoom) }),
  setPlaying: (playing) => set({ isPlaying: playing }),

  // ----------------------------------------------------------------- drag

  beginDrag: (drag) => set({ drag, selection: new Set([drag.clipId]) }),

  /** Pointer movement. **No commit** — see rule 2 at the top of the file. */
  updateDrag: (previewMs) =>
    set((state) => (state.drag ? { drag: { ...state.drag, previewMs } } : {})),

  /** The drop. One commit for the whole gesture. */
  endDrag: () => {
    const { drag } = get()
    if (!drag) return
    set({ drag: null })
    if (drag.kind === 'move') get().moveClip(drag.clipId, drag.previewMs)
    else if (drag.kind === 'trim-start') get().trimStart(drag.clipId, drag.previewMs)
    else get().trimEnd(drag.clipId, drag.previewMs)
  },

  /** Escape during a drag. The document was never touched, so there is nothing
   * to undo — which is the point of staging the gesture outside it. */
  cancelDrag: () => set({ drag: null }),
}))

// --------------------------------------------------------------------------
// Selectors. Derived, never stored.
// --------------------------------------------------------------------------

export function selectDurationMs(state: EditorState): number {
  return timelineDurationMs(state.timeline)
}

/**
 * One shared empty array, never a fresh `[]`.
 *
 * Zustand compares what a selector returns by reference to decide whether to
 * re-render. `?? []` builds a new array on every call, so the value is never
 * equal to the last one, the component re-renders, the selector runs again —
 * and React fails with "Maximum update depth exceeded" the moment the timeline
 * has no video track, which is its state on first paint.
 *
 * Found by the M2 end-to-end run, not by any unit test: the selector is
 * correct in isolation and only misbehaves once React is subscribed to it.
 */
const NO_CLIPS: readonly MediaClip[] = Object.freeze([])

export function selectClips(state: EditorState): readonly MediaClip[] {
  return trackOfKind(state.timeline, 'video')?.clips ?? NO_CLIPS
}

export function selectClipUnderPlayhead(state: EditorState): MediaClip | null {
  const track = trackOfKind(state.timeline, 'video')
  return track ? clipAt(track, state.playheadMs) : null
}

/** The one clip an inspector shows. Null when nothing, or more than one, is
 * selected — a panel that silently edits the first of six is worse than one
 * that says "6 clips selected". */
export function selectSingleClipId(state: EditorState): string | null {
  return state.selection.size === 1 ? [...state.selection][0]! : null
}

export function selectSelectedClip(state: EditorState): MediaClip | null {
  const id = selectSingleClipId(state)
  if (!id) return null
  const found = locateClip(state.timeline, id)
  return found && 'assetId' in found.clip ? found.clip : null
}

export function selectCanUndo(state: EditorState): boolean {
  return canUndo(state.history)
}

export function selectCanRedo(state: EditorState): boolean {
  return canRedo(state.history)
}

/**
 * Where a clip should be drawn, and how wide.
 *
 * Its committed position, or the gesture's preview while one is in flight — the
 * document is not touched until the drop, so this is the only place the two are
 * reconciled. All three drag kinds are handled: a trim that fell back to the
 * committed bounds would leave the edge frozen under the pointer.
 */
export function selectClipBoundsMs(
  state: EditorState,
  clip: MediaClip,
): { startMs: number; durationMs: number } {
  const { drag } = state
  if (drag?.clipId !== clip.id) return { startMs: clip.startMs, durationMs: clip.durationMs }

  const end = clip.startMs + clip.durationMs
  switch (drag.kind) {
    case 'move':
      return { startMs: drag.previewMs, durationMs: clip.durationMs }
    case 'trim-start':
      return { startMs: drag.previewMs, durationMs: Math.max(1, end - drag.previewMs) }
    case 'trim-end':
      return { startMs: clip.startMs, durationMs: Math.max(1, drag.previewMs - clip.startMs) }
  }
}

export function selectClipStartMs(state: EditorState, clip: MediaClip): number {
  return selectClipBoundsMs(state, clip).startMs
}

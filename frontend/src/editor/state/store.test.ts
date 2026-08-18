/**
 * The store, driven the way the interface drives it.
 *
 * The properties worth protecting are the three stated at the top of
 * `store.ts`: every document change goes through `commit`, a drag does not
 * commit until it drops, and nothing derived is stored.
 */

import { beforeEach, describe, expect, it } from 'vitest'

import {
  selectCanRedo,
  selectCanUndo,
  selectClipBoundsMs,
  selectClipStartMs,
  selectClips,
  selectDurationMs,
  selectSingleClipId,
  useEditor,
} from './store'
import { emptyTimeline, type TimelineDocument } from './timeline-document'

const state = () => useEditor.getState()

function loaded(timeline: TimelineDocument = emptyTimeline(), version = 3) {
  state().load({ projectId: 'prj_1', timeline, version })
}

beforeEach(() => {
  state().reset()
})

describe('commit', () => {
  it('marks the document dirty and records an undo step', () => {
    loaded()
    state().addClip({ assetId: 'ast_1', durationMs: 4_000 })

    expect(state().isDirty).toBe(true)
    expect(selectCanUndo(state())).toBe(true)
    expect(selectClips(state())).toHaveLength(1)
  })

  it('does nothing when the recipe changes nothing', () => {
    loaded()
    const id = state().addClip({ assetId: 'ast_1', durationMs: 4_000 })
    state().markSaved(4)

    // Moving a clip to where it already is.
    state().moveClip(id, 0)
    expect(state().isDirty).toBe(false)
    expect(state().history.past).toHaveLength(1)
  })

  it('undo and redo walk the document back and forward', () => {
    loaded()
    state().addClip({ assetId: 'ast_1', durationMs: 4_000 })
    state().addClip({ assetId: 'ast_2', durationMs: 2_000 })
    expect(selectDurationMs(state())).toBe(6_000)

    state().undo()
    expect(selectClips(state())).toHaveLength(1)
    expect(selectCanRedo(state())).toBe(true)

    state().redo()
    expect(selectClips(state())).toHaveLength(2)
    expect(selectDurationMs(state())).toBe(6_000)
  })
})

describe('load and save bookkeeping', () => {
  it('a freshly loaded project is clean and has no history', () => {
    loaded()
    state().addClip({ assetId: 'ast_1', durationMs: 1_000 })

    loaded(emptyTimeline(), 12)
    expect(state().isDirty).toBe(false)
    expect(state().version).toBe(12)
    // Keeping the old stack would let undo apply patches recorded against a
    // document that is no longer open.
    expect(selectCanUndo(state())).toBe(false)
  })

  it('keeps the document dirty when an edit lands while a save is in flight', () => {
    // The save carries a snapshot. An edit made during the few hundred
    // milliseconds it is in flight is not in that snapshot, so clearing the
    // dirty flag when it returns would strand that edit: nothing would send it
    // until the *next* change, and closing the tab in between loses it.
    loaded()
    state().addClip({ assetId: 'ast_1', durationMs: 1_000 })
    const inFlight = state().timeline

    state().addClip({ assetId: 'ast_2', durationMs: 1_000 })
    state().markSaved(4, inFlight)

    expect(state().version).toBe(4)
    expect(state().isDirty).toBe(true)
  })

  it('markSaved clears dirty and takes the new version', () => {
    loaded()
    state().addClip({ assetId: 'ast_1', durationMs: 1_000 })
    state().markSaved(4, state().timeline)

    expect(state().version).toBe(4)
    expect(state().isDirty).toBe(false)
  })
})

describe('dragging', () => {
  it('does not touch the document until the drop', () => {
    loaded()
    // One clip, so the move has somewhere to go. With a neighbour butted
    // against it the drag would be clamped back to where it started, which is
    // correct and is covered in operations.test.ts.
    const id = state().addClip({ assetId: 'ast_1', durationMs: 2_000 })
    state().markSaved(4)
    const stepsBefore = state().history.past.length

    state().beginDrag({ kind: 'move', clipId: id, previewMs: 0 })
    for (let ms = 0; ms <= 500; ms += 50) state().updateDrag(ms)

    // Two hundred pointer moves, no commits, nothing dirty.
    expect(state().history.past).toHaveLength(stepsBefore)
    expect(state().isDirty).toBe(false)
    // But the interface can already draw it in the new place.
    const clip = selectClips(state()).find((each) => each.id === id)!
    expect(selectClipStartMs(state(), clip)).toBe(500)

    state().endDrag()
    expect(state().history.past).toHaveLength(stepsBefore + 1)
    expect(state().isDirty).toBe(true)
  })

  it('previews all three gesture kinds without touching the document', () => {
    loaded()
    const id = state().addClip({ assetId: 'ast_1', durationMs: 4_000 })
    const clip = () => selectClips(state()).find((each) => each.id === id)!

    state().beginDrag({ kind: 'trim-start', clipId: id, previewMs: 1_000 })
    expect(selectClipBoundsMs(state(), clip())).toEqual({ startMs: 1_000, durationMs: 3_000 })

    state().beginDrag({ kind: 'trim-end', clipId: id, previewMs: 2_500 })
    expect(selectClipBoundsMs(state(), clip())).toEqual({ startMs: 0, durationMs: 2_500 })

    // Still nothing committed: a trim whose preview fell back to the committed
    // bounds would leave the edge frozen under the pointer.
    expect(clip().durationMs).toBe(4_000)
    state().cancelDrag()
  })

  it('cancelling leaves nothing to undo', () => {
    loaded()
    const id = state().addClip({ assetId: 'ast_1', durationMs: 2_000 })
    state().markSaved(4)

    state().beginDrag({ kind: 'move', clipId: id, previewMs: 0 })
    state().updateDrag(900)
    state().cancelDrag()

    expect(state().isDirty).toBe(false)
    expect(selectClips(state())[0]?.startMs).toBe(0)
  })

  it('refuses to undo mid-gesture', () => {
    loaded()
    const id = state().addClip({ assetId: 'ast_1', durationMs: 2_000 })
    state().beginDrag({ kind: 'move', clipId: id, previewMs: 0 })
    state().undo()
    expect(selectClips(state())).toHaveLength(1)
  })
})

describe('selection', () => {
  it('replaces by default and toggles when additive', () => {
    loaded()
    const first = state().addClip({ assetId: 'ast_1', durationMs: 1_000 })
    const second = state().addClip({ assetId: 'ast_2', durationMs: 1_000 })

    state().select(first)
    expect(selectSingleClipId(state())).toBe(first)

    state().select(second, { additive: true })
    expect(state().selection.size).toBe(2)
    // An inspector shows nothing rather than silently editing one of two.
    expect(selectSingleClipId(state())).toBeNull()

    state().select(second, { additive: true })
    expect(selectSingleClipId(state())).toBe(first)
  })

  it('deletes everything selected in one undo step', () => {
    loaded()
    const first = state().addClip({ assetId: 'ast_1', durationMs: 1_000 })
    const second = state().addClip({ assetId: 'ast_2', durationMs: 1_000 })
    const stepsBefore = state().history.past.length

    state().selectMany([first, second])
    state().deleteSelection()

    expect(selectClips(state())).toHaveLength(0)
    expect(state().history.past).toHaveLength(stepsBefore + 1)
    state().undo()
    expect(selectClips(state())).toHaveLength(2)
  })
})

describe('splitAtPlayhead', () => {
  it('splits whatever the playhead is inside and selects the new piece', () => {
    loaded()
    state().addClip({ assetId: 'ast_1', durationMs: 10_000 })
    state().setPlayhead(4_000)

    const created = state().splitAtPlayhead()
    expect(created).not.toBeNull()
    expect(selectClips(state())).toHaveLength(2)
    expect(selectSingleClipId(state())).toBe(created)
  })

  it('does nothing when the playhead is in a gap', () => {
    loaded()
    state().addClip({ assetId: 'ast_1', durationMs: 2_000 })
    state().setPlayhead(9_000)

    expect(state().splitAtPlayhead()).toBeNull()
    expect(selectClips(state())).toHaveLength(1)
  })
})

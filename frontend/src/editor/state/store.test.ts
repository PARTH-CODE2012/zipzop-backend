/**
 * The store, driven the way the interface drives it.
 *
 * The properties worth protecting are the three stated at the top of
 * `store.ts`: every document change goes through `commit`, a drag does not
 * commit until it drops, and nothing derived is stored.
 */

import { beforeEach, describe, expect, it } from 'vitest'

import {
  clipBoundsMs,
  selectAllClips,
  selectCanRedo,
  selectCanUndo,
  selectClipStartMs,
  selectClips,
  selectDurationMs,
  selectLanes,
  selectSelectedAnyClip,
  selectSingleClipId,
  trimEndCeilingMs,
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

describe('selector stability', () => {
  /**
   * The property Zustand actually requires, and the one this project has now
   * got wrong twice.
   *
   * `useEditor` compares what a selector returns by reference. A selector that
   * builds a fresh array or object on every call is never equal to its own
   * previous result: the component re-renders, the selector runs again, and
   * React stops the page with "Maximum update depth exceeded" or "The result of
   * getSnapshot should be cached to avoid an infinite loop".
   *
   * M2 hit it with a `?? []`. M3 hit it again with a `.sort()` and a
   * `.flatMap()`. Neither was caught by a unit test, because both selectors are
   * perfectly correct in isolation — the fault only exists once React
   * subscribes. So the test is about identity, not about values.
   */
  const subscriptionSelectors = {
    selectClips,
    selectLanes,
    selectAllClips,
    selectSelectedAnyClip,
  } as const

  it('returns the same reference until the document changes', () => {
    loaded()
    state().addClip({ assetId: 'ast_1', durationMs: 4_000 })
    state().addMusicClip({ assetId: 'ast_2', durationMs: 4_000 })
    state().addTitle('Hello')

    for (const [name, selector] of Object.entries(subscriptionSelectors)) {
      const first = selector(state())
      const second = selector(state())
      expect(second, `${name} built a new value for an unchanged document`).toBe(first)
    }
  })

  it('returns a different reference once it does', () => {
    // The other half: a cache that never invalidates would be just as broken,
    // and silently — the timeline would simply stop redrawing.
    loaded()
    state().addClip({ assetId: 'ast_1', durationMs: 1_000 })
    const before = Object.fromEntries(
      Object.entries(subscriptionSelectors).map(([name, fn]) => [name, fn(state())]),
    )

    state().addClip({ assetId: 'ast_2', durationMs: 1_000 })

    expect(selectClips(state())).not.toBe(before.selectClips)
    expect(selectAllClips(state())).not.toBe(before.selectAllClips)
  })

  it('holds across a playhead move, which changes state but not the document', () => {
    // The common case: something unrelated to the timeline updates the store on
    // every animation frame during playback.
    loaded()
    state().addClip({ assetId: 'ast_1', durationMs: 4_000 })
    const lanes = selectLanes(state())

    state().setPlayhead(1_234)
    state().select(null)

    expect(selectLanes(state())).toBe(lanes)
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
    expect(clipBoundsMs(state().drag, clip())).toEqual({ startMs: 1_000, durationMs: 3_000 })

    state().beginDrag({ kind: 'trim-end', clipId: id, previewMs: 2_500 })
    expect(clipBoundsMs(state().drag, clip())).toEqual({ startMs: 0, durationMs: 2_500 })

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

/**
 * The one bound the document cannot express.
 *
 * Invariant 4 — a clip may not read past the end of its media — needs the
 * asset's own length, which the timeline deliberately does not carry. Dropping
 * a trim-end gesture without it produced a document the server rejects with a
 * `422` on the next autosave: the editor then shows "Could not save", stops
 * retrying, and everything since the last good save is lost on reload. Nothing
 * in the interface says which clip is at fault.
 */
describe('trimming against the media, not just the neighbours', () => {
  beforeEach(() => {
    useEditor.getState().setAssetDurations({ ast_1: 10_000 })
  })

  it('a dropped trim-end stops at the end of the file', () => {
    loaded()
    const id = state().addClip({ assetId: 'ast_1', durationMs: 10_000 })

    state().beginDrag({ kind: 'trim-end', clipId: id, previewMs: 10_000 })
    state().updateDrag(40_000) // dragged far past the end of the media
    state().endDrag()

    expect(selectClips(state())[0]?.durationMs).toBe(10_000)
  })

  it('counts speed, because 2x eats the file twice as fast', () => {
    loaded()
    const id = state().addClip({ assetId: 'ast_1', durationMs: 4_000 })
    state().setClipProperties(id, { speed: 2 })

    state().beginDrag({ kind: 'trim-end', clipId: id, previewMs: 4_000 })
    state().updateDrag(40_000)
    state().endDrag()

    // Ten seconds of media at 2x is five seconds of timeline.
    expect(selectClips(state())[0]?.durationMs).toBe(5_000)
  })

  it('still stops at the next clip when that comes first', () => {
    loaded()
    const first = state().addClip({ assetId: 'ast_1', durationMs: 2_000 })
    state().addClip({ assetId: 'ast_1', durationMs: 2_000 })

    state().beginDrag({ kind: 'trim-end', clipId: first, previewMs: 2_000 })
    state().updateDrag(9_000)
    state().endDrag()

    expect(selectClips(state())[0]?.durationMs).toBe(2_000)
  })

  it('trims freely when the media length is not known yet', () => {
    // The media list has not come back, or the asset is not in it. The server
    // is still the authority; the editor must not invent a limit of its own.
    loaded()
    useEditor.getState().setAssetDurations({})
    const id = state().addClip({ assetId: 'ast_1', durationMs: 4_000 })

    state().beginDrag({ kind: 'trim-end', clipId: id, previewMs: 4_000 })
    state().updateDrag(9_000)
    state().endDrag()

    expect(selectClips(state())[0]?.durationMs).toBe(9_000)
  })

  it('records nothing when the gesture ends where it started', () => {
    // The clamp now runs on every trim, including one that changes nothing.
    // A pass that wrote the same values back would produce Immer patches, an
    // undo step for a drag the user cancelled by returning, and an autosave.
    loaded()
    const id = state().addClip({ assetId: 'ast_1', durationMs: 10_000 })
    state().markSaved(4)
    const steps = state().history.past.length

    state().beginDrag({ kind: 'trim-end', clipId: id, previewMs: 10_000 })
    state().updateDrag(10_000)
    state().endDrag()

    expect(state().history.past).toHaveLength(steps)
    expect(state().isDirty).toBe(false)
  })

  it('reports the ceiling the preview has to respect', () => {
    loaded()
    const id = state().addClip({ assetId: 'ast_1', durationMs: 10_000 })
    expect(trimEndCeilingMs(state(), id)).toBe(10_000)
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

describe('the audio and text tracks', () => {
  it('puts music on its own lane, not the video one', () => {
    loaded()
    state().addClip({ assetId: 'ast_v', durationMs: 4_000 })
    state().addMusicClip({ assetId: 'ast_a', durationMs: 30_000 })

    const kinds = state().timeline.tracks.map((track) => track.kind)
    expect(kinds).toEqual(['video', 'audio'])
    expect(selectDurationMs(state())).toBe(30_000)
  })

  it('places a title at the playhead rather than at the end', () => {
    // A title is positioned against the picture underneath it, and the end of
    // the track is never where the user is looking.
    loaded()
    state().addClip({ assetId: 'ast_v', durationMs: 20_000 })
    state().setPlayhead(7_000)
    const id = state().addTitle('Hello')

    const text = state().timeline.tracks.find((track) => track.kind === 'text')
    expect(text?.clips[0]).toMatchObject({ id, startMs: 7_000, kind: 'title', text: 'Hello' })
  })

  it('pushes a second title clear of the first instead of overlapping it', () => {
    loaded()
    state().setPlayhead(1_000)
    state().addTitle('One')
    state().setPlayhead(2_000)
    state().addTitle('Two')

    const clips = state().timeline.tracks.find((t) => t.kind === 'text')?.clips ?? []
    expect(clips.map((c) => c.startMs)).toEqual([1_000, 4_000])
  })

  it('edits a title text and undoes it', () => {
    loaded()
    const id = state().addTitle('Draft')
    state().setText(id, 'Final')
    const clips = () => state().timeline.tracks.find((t) => t.kind === 'text')?.clips ?? []
    expect(clips()[0]?.text).toBe('Final')

    state().undo()
    expect(clips()[0]?.text).toBe('Draft')
  })

  it('muting a lane is an undoable edit, not view state', () => {
    // The renderer honours `muted` (contract §4.2), so it belongs in the
    // document — a mute the user cannot take back with undo is a surprise.
    loaded()
    state().addMusicClip({ assetId: 'ast_a', durationMs: 5_000 })
    const track = state().timeline.tracks.find((t) => t.kind === 'audio')!

    state().setTrackMuted(track.id, true)
    expect(state().timeline.tracks.find((t) => t.kind === 'audio')?.muted).toBe(true)

    state().undo()
    expect(state().timeline.tracks.find((t) => t.kind === 'audio')?.muted).toBe(false)
  })
})

/**
 * The AI tools land their results as ordinary edits. That is the whole design:
 * an undoable commit, not a special mode, so a suggestion the user dislikes
 * costs one press of ⌘Z rather than a dialog asking whether they are sure.
 */
describe('applying a tool result', () => {
  it('adds a whole caption run as ONE undo step', () => {
    // 1,800 clips at one commit each would be 1,800 presses of ⌘Z to undo a
    // captions run somebody did not want.
    loaded()
    const clipId = state().addClip({ assetId: 'ast_1', durationMs: 60_000 })
    state().markSaved(4)
    const steps = state().history.past.length

    state().applyCaptions({
      clipId,
      words: Array.from({ length: 200 }, (_, index) => ({
        text: `word${index}`,
        startMs: index * 200,
        durationMs: 180,
        emphasis: 0,
        confidence: 0.9,
      })),
    })

    expect(state().history.past).toHaveLength(steps + 1)
    const text = selectLanes(state()).find((track) => track.kind === 'text')
    expect(text?.clips).toHaveLength(200)

    state().undo()
    const afterUndo = selectLanes(state()).find((track) => track.kind === 'text')
    expect(afterUndo?.clips ?? []).toHaveLength(0)
  })

  it('re-running captions replaces the previous words rather than doubling them', () => {
    loaded()
    const clipId = state().addClip({ assetId: 'ast_1', durationMs: 10_000 })
    const words = [
      { text: 'first', startMs: 0, durationMs: 400, emphasis: 0, confidence: 0.9 },
    ]

    state().applyCaptions({ clipId, words })
    state().applyCaptions({
      clipId,
      words: [{ text: 'second', startMs: 0, durationMs: 400, emphasis: 0, confidence: 0.9 }],
    })

    const text = selectLanes(state()).find((track) => track.kind === 'text')
    expect(text?.clips).toHaveLength(1)
    expect((text?.clips[0] as { text: string }).text).toBe('second')
  })

  it('leaves a hand-typed title alone when captions land on top of it', () => {
    // A tool silently deleting text somebody wrote would be unforgivable.
    loaded()
    const clipId = state().addClip({ assetId: 'ast_1', durationMs: 10_000 })
    state().addTitle('Chapter one')

    state().applyCaptions({
      clipId,
      words: [{ text: 'hello', startMs: 0, durationMs: 400, emphasis: 0, confidence: 0.9 }],
    })

    const text = selectLanes(state()).find((track) => track.kind === 'text')
    const kinds = (text?.clips ?? []).map((clip) => (clip as { kind: string }).kind)
    expect(kinds).toContain('title')
    expect(kinds).toContain('caption')
  })

  it('a smart trim shortens the clip and closes the gap behind it', () => {
    loaded()
    const first = state().addClip({ assetId: 'ast_1', durationMs: 10_000 })
    const second = state().addClip({ assetId: 'ast_1', durationMs: 5_000 })
    state().markSaved(4)

    // Cut one second out of the middle of the first clip.
    state().applySmartTrim(first, [{ startMs: 4_000, endMs: 5_000 }])

    const clips = selectClips(state())
    // Two pieces from the first clip, then the second clip pulled left.
    expect(clips).toHaveLength(3)
    expect(clips[0]?.startMs).toBe(0)
    expect(clips[0]?.durationMs).toBe(4_000)
    expect(clips[1]?.startMs).toBe(4_000)
    expect(clips[1]?.durationMs).toBe(5_000)
    // The second piece reads from *after* the removed second.
    expect(clips[1]?.sourceInMs).toBe(5_000)
    // The following clip moved left by exactly what was cut - no gap.
    expect(clips[2]?.id).toBe(second)
    expect(clips[2]?.startMs).toBe(9_000)
  })

  it('a smart trim is one undo step for every cut it makes', () => {
    loaded()
    const clipId = state().addClip({ assetId: 'ast_1', durationMs: 20_000 })
    state().markSaved(4)
    const steps = state().history.past.length

    state().applySmartTrim(clipId, [
      { startMs: 2_000, endMs: 3_000 },
      { startMs: 6_000, endMs: 6_500 },
      { startMs: 11_000, endMs: 12_000 },
    ])

    expect(state().history.past).toHaveLength(steps + 1)
    expect(selectClips(state())).toHaveLength(4)

    state().undo()
    expect(selectClips(state())).toHaveLength(1)
    expect(selectClips(state())[0]?.durationMs).toBe(20_000)
  })

  it('refuses to remove a clip entirely', () => {
    // Deleting the clip is a worse surprise than leaving a short piece.
    loaded()
    const clipId = state().addClip({ assetId: 'ast_1', durationMs: 5_000 })
    state().applySmartTrim(clipId, [{ startMs: 0, endMs: 5_000 }])

    expect(selectClips(state())).toHaveLength(1)
  })

  it('a colour grade is one effects entry, replacing any grade already there', () => {
    // A clip has one look; stacking two LUTs produces a picture neither of them
    // describes.
    loaded()
    const clipId = state().addClip({ assetId: 'ast_1', durationMs: 5_000 })

    state().applyColorGrade(clipId, { lut: 'cinematic_warm', strength: 0.75 })
    state().applyColorGrade(clipId, { lut: 'cyberpunk', strength: 0.9, sourceJobId: 'job_1' })

    const effects = selectClips(state())[0]?.effects ?? []
    expect(effects).toHaveLength(1)
    expect(effects[0]).toMatchObject({
      type: 'color_grade',
      lut: 'cyberpunk',
      strength: 0.9,
      sourceJobId: 'job_1',
    })
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

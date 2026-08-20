/**
 * The editing operations.
 *
 * These are where the editor's correctness lives, and they are pure, so this
 * file can be exhaustive without a browser. The tests worth reading are the
 * ones about `speed`: it is the field that makes split and trim arithmetic
 * non-obvious, and getting it wrong produces an edit that looks right on the
 * timeline and jumps in the picture.
 */

import { produce } from 'immer'
import { describe, expect, it } from 'vitest'

import {
  MIN_CLIP_MS,
  appendClip,
  duplicateClip,
  moveClip,
  removeClips,
  setClipProperties,
  setTransition,
  splitAt,
  trimEnd,
  trimStart,
} from './operations'
import {
  clipEndMs,
  trackOfKind,
  violatedInvariants,
  type MediaClip,
  type TimelineDocument,
} from './timeline-document'

function clip(over: Partial<MediaClip> = {}): MediaClip {
  return {
    id: 'clp_a',
    assetId: 'ast_1',
    startMs: 0,
    durationMs: 10_000,
    sourceInMs: 0,
    speed: 1,
    volume: 1,
    audioFadeInMs: 0,
    audioFadeOutMs: 0,
    effects: [],
    ...over,
  }
}

function timeline(...clips: MediaClip[]): TimelineDocument {
  return {
    schemaVersion: 1,
    tracks: [
      { id: 'trk_video', kind: 'video', index: 0, muted: false, locked: false, clips },
    ],
  }
}

const clips = (document: TimelineDocument) => trackOfKind(document, 'video')?.clips ?? []
const byId = (document: TimelineDocument, id: string) => clips(document).find((c) => c.id === id)

/** Run a recipe and assert the result is still a document the server accepts. */
function edit(
  document: TimelineDocument,
  recipe: (draft: TimelineDocument) => void,
): TimelineDocument {
  const next = produce(document, recipe)
  expect(violatedInvariants(next)).toEqual([])
  return next
}

// --------------------------------------------------------------------------

describe('appendClip', () => {
  it('lands after whatever is already on the track', () => {
    const document = edit(timeline(clip({ durationMs: 4_000 })), (draft) => {
      appendClip(draft, { assetId: 'ast_2', durationMs: 3_000 })
    })
    expect(clips(document)[1]?.startMs).toBe(4_000)
  })

  it('creates the track when there is none', () => {
    const document = edit({ schemaVersion: 1, tracks: [] }, (draft) => {
      appendClip(draft, { assetId: 'ast_1', durationMs: 1_000 })
    })
    expect(trackOfKind(document, 'video')?.clips).toHaveLength(1)
  })
})

describe('splitAt', () => {
  it('divides the clip and advances the source of the right-hand piece', () => {
    const document = edit(timeline(clip({ durationMs: 10_000, sourceInMs: 2_000 })), (draft) => {
      splitAt(draft, 'clp_a', 4_000)
    })

    const [left, right] = clips(document)
    expect(left?.durationMs).toBe(4_000)
    expect(left?.sourceInMs).toBe(2_000)
    expect(right?.startMs).toBe(4_000)
    expect(right?.durationMs).toBe(6_000)
    expect(right?.sourceInMs).toBe(6_000)
  })

  it('counts speed against the source', () => {
    // Four seconds of timeline at 2x consumed eight seconds of media, so the
    // right-hand piece starts eight seconds in — not four.
    const document = edit(
      timeline(clip({ durationMs: 10_000, sourceInMs: 0, speed: 2 })),
      (draft) => {
        splitAt(draft, 'clp_a', 4_000)
      },
    )
    expect(clips(document)[1]?.sourceInMs).toBe(8_000)
  })

  it('puts a cut at the new join and keeps the outer transitions', () => {
    const document = edit(
      timeline(
        clip({
          transitionIn: { type: 'fade', durationMs: 300 },
          transitionOut: { type: 'dissolve', durationMs: 400 },
        }),
      ),
      (draft) => {
        splitAt(draft, 'clp_a', 5_000)
      },
    )

    const [left, right] = clips(document)
    expect(left?.transitionIn).toEqual({ type: 'fade', durationMs: 300 })
    expect(left?.transitionOut).toBeNull()
    expect(right?.transitionIn).toBeNull()
    expect(right?.transitionOut).toEqual({ type: 'dissolve', durationMs: 400 })
  })

  it('refuses a split that would leave a sliver', () => {
    const document = timeline(clip({ durationMs: 10_000 }))
    const next = produce(document, (draft) => {
      expect(splitAt(draft, 'clp_a', MIN_CLIP_MS - 1)).toBeNull()
    })
    expect(clips(next)).toHaveLength(1)
  })

  it('gives the two halves different ids', () => {
    const document = edit(timeline(clip()), (draft) => {
      splitAt(draft, 'clp_a', 5_000)
    })
    const [left, right] = clips(document)
    expect(left?.id).not.toBe(right?.id)
  })
})

describe('trimStart', () => {
  it('moves the start and the source together', () => {
    const document = edit(timeline(clip({ durationMs: 10_000, sourceInMs: 1_000 })), (draft) => {
      trimStart(draft, 'clp_a', 2_000)
    })

    const trimmed = byId(document, 'clp_a')
    expect(trimmed?.startMs).toBe(2_000)
    expect(trimmed?.durationMs).toBe(8_000)
    // The head is two seconds later on the timeline *and* two seconds later
    // inside the media. Moving only one of them slides the picture.
    expect(trimmed?.sourceInMs).toBe(3_000)
    expect(clipEndMs(trimmed!)).toBe(10_000)
  })

  it('cannot pull back past the start of the media', () => {
    const document = edit(
      timeline(clip({ startMs: 5_000, durationMs: 5_000, sourceInMs: 1_000 })),
      (draft) => {
        trimStart(draft, 'clp_a', 0)
      },
    )
    const trimmed = byId(document, 'clp_a')
    expect(trimmed?.sourceInMs).toBe(0)
    expect(trimmed?.startMs).toBe(4_000)
  })

  it('stops at the clip before it', () => {
    const document = edit(
      timeline(
        clip({ id: 'clp_a', startMs: 0, durationMs: 3_000 }),
        clip({ id: 'clp_b', startMs: 3_000, durationMs: 5_000, sourceInMs: 5_000 }),
      ),
      (draft) => {
        trimStart(draft, 'clp_b', 1_000)
      },
    )
    expect(byId(document, 'clp_b')?.startMs).toBe(3_000)
  })
})

describe('trimEnd', () => {
  it('changes only the duration', () => {
    const document = edit(timeline(clip({ sourceInMs: 1_000 })), (draft) => {
      trimEnd(draft, 'clp_a', 6_000)
    })
    const trimmed = byId(document, 'clp_a')
    expect(trimmed?.durationMs).toBe(6_000)
    expect(trimmed?.sourceInMs).toBe(1_000)
  })

  it('stops at the clip after it', () => {
    const document = edit(
      timeline(
        clip({ id: 'clp_a', startMs: 0, durationMs: 3_000 }),
        clip({ id: 'clp_b', startMs: 4_000, durationMs: 2_000 }),
      ),
      (draft) => {
        trimEnd(draft, 'clp_a', 9_000)
      },
    )
    expect(clipEndMs(byId(document, 'clp_a')!)).toBe(4_000)
  })

  it('stops at the end of the media, counting speed', () => {
    // Invariant 4 as far as the client can see it. 5s of media left, played at
    // 2x, is 2.5s of timeline.
    const document = edit(
      timeline(clip({ durationMs: 1_000, sourceInMs: 5_000, speed: 2 })),
      (draft) => {
        trimEnd(draft, 'clp_a', 60_000, { maxSourceMs: 10_000 })
      },
    )
    expect(byId(document, 'clp_a')?.durationMs).toBe(2_500)
  })
})

describe('moveClip', () => {
  it('clamps between its neighbours rather than pushing them', () => {
    // Phase 1 has no magnetic timeline and no ripple edits, so the neighbours
    // are the wall.
    const document = edit(
      timeline(
        clip({ id: 'clp_a', startMs: 0, durationMs: 2_000 }),
        clip({ id: 'clp_b', startMs: 4_000, durationMs: 2_000 }),
        clip({ id: 'clp_c', startMs: 8_000, durationMs: 2_000 }),
      ),
      (draft) => {
        moveClip(draft, 'clp_b', 0)
      },
    )
    expect(byId(document, 'clp_b')?.startMs).toBe(2_000)
    expect(byId(document, 'clp_a')?.startMs).toBe(0)
  })

  it('never goes before zero', () => {
    const document = edit(timeline(clip({ startMs: 3_000, durationMs: 1_000 })), (draft) => {
      moveClip(draft, 'clp_a', -5_000)
    })
    expect(byId(document, 'clp_a')?.startMs).toBe(0)
  })
})

describe('duplicateClip', () => {
  it('lands immediately after the original when there is room', () => {
    const document = edit(timeline(clip({ durationMs: 2_000 })), (draft) => {
      duplicateClip(draft, 'clp_a')
    })
    expect(clips(document)[1]?.startMs).toBe(2_000)
  })

  it('goes to the end of the track when the gap is too small', () => {
    const document = edit(
      timeline(
        clip({ id: 'clp_a', startMs: 0, durationMs: 4_000 }),
        clip({ id: 'clp_b', startMs: 5_000, durationMs: 2_000 }),
      ),
      (draft) => {
        duplicateClip(draft, 'clp_a')
      },
    )
    expect(clips(document)).toHaveLength(3)
    expect(clips(document).at(-1)?.startMs).toBe(7_000)
  })
})

describe('removeClips', () => {
  it('removes several at once and keeps the empty track', () => {
    const document = edit(
      timeline(
        clip({ id: 'clp_a', startMs: 0, durationMs: 1_000 }),
        clip({ id: 'clp_b', startMs: 2_000, durationMs: 1_000 }),
      ),
      (draft) => {
        removeClips(draft, ['clp_a', 'clp_b'])
      },
    )
    expect(clips(document)).toHaveLength(0)
    // The track survives, and with it the user's mute and lock settings.
    expect(document.tracks).toHaveLength(1)
  })
})

describe('setClipProperties', () => {
  it('clamps to the ranges the contract allows', () => {
    const document = edit(timeline(clip()), (draft) => {
      setClipProperties(draft, 'clp_a', { volume: 9, speed: 100 })
    })
    expect(byId(document, 'clp_a')?.volume).toBe(2)
    expect(byId(document, 'clp_a')?.speed).toBe(4)
  })

  it('builds the transform on first use', () => {
    const document = edit(timeline(clip()), (draft) => {
      setClipProperties(draft, 'clp_a', { rotation: 90, flipH: true })
    })
    expect(byId(document, 'clp_a')?.transform).toMatchObject({ rotation: 90, flipH: true })
  })
})

describe('setTransition', () => {
  it('clamps to half the shorter clip it joins', () => {
    const document = edit(
      timeline(
        clip({ id: 'clp_a', startMs: 0, durationMs: 1_000 }),
        clip({ id: 'clp_b', startMs: 1_000, durationMs: 8_000 }),
      ),
      (draft) => {
        setTransition(draft, 'clp_a', 'out', { type: 'dissolve', durationMs: 5_000 })
      },
    )
    expect(byId(document, 'clp_a')?.transitionOut).toEqual({ type: 'dissolve', durationMs: 500 })
  })

  it('treats a cut as no transition at all', () => {
    const document = edit(
      timeline(clip({ transitionOut: { type: 'fade', durationMs: 200 } })),
      (draft) => {
        setTransition(draft, 'clp_a', 'out', { type: 'cut', durationMs: 0 })
      },
    )
    expect(byId(document, 'clp_a')?.transitionOut).toBeNull()
  })
})

/**
 * Invariant 7 depends on two clip *durations*, so it can be broken by an edit
 * that never touches the transition. The server checks it on every save, and
 * the report comes back as a `422` on an autosave two seconds later, naming a
 * clip the user is no longer looking at — with autosave then stuck until
 * something undoes it. Each of these is one of the edits that used to do it.
 */
describe('transitions stay inside invariant 7 after the clips move', () => {
  const pair = () =>
    timeline(
      clip({
        id: 'clp_a',
        startMs: 0,
        durationMs: 5_000,
        transitionOut: { type: 'dissolve', durationMs: 400 },
      }),
      clip({ id: 'clp_b', startMs: 5_000, durationMs: 5_000 }),
    )

  it('re-clamps when the clip is trimmed shorter from the right', () => {
    const document = edit(pair(), (draft) => {
      trimEnd(draft, 'clp_a', 500)
    })
    expect(byId(document, 'clp_a')?.durationMs).toBe(500)
    expect(byId(document, 'clp_a')?.transitionOut).toEqual({ type: 'dissolve', durationMs: 250 })
  })

  it('re-clamps when the clip is trimmed shorter from the left', () => {
    const document = edit(pair(), (draft) => {
      trimStart(draft, 'clp_a', 4_400)
    })
    expect(byId(document, 'clp_a')?.transitionOut).toEqual({ type: 'dissolve', durationMs: 300 })
  })

  it('re-clamps when the *neighbour* becomes the shorter one', () => {
    const document = edit(pair(), (draft) => {
      trimEnd(draft, 'clp_b', 5_600)
    })
    expect(byId(document, 'clp_b')?.durationMs).toBe(600)
    expect(byId(document, 'clp_a')?.transitionOut).toEqual({ type: 'dissolve', durationMs: 300 })
  })

  it('re-clamps when splitting leaves two shorter halves', () => {
    const document = edit(pair(), (draft) => {
      splitAt(draft, 'clp_a', 4_500)
    })
    // The right half is 500 ms and keeps the outgoing dissolve.
    const halves = trackOfKind(document, 'video')!.clips
    expect(halves[1]?.durationMs).toBe(500)
    expect(halves[1]?.transitionOut).toEqual({ type: 'dissolve', durationMs: 250 })
  })

  it('re-clamps when deleting a clip hands a shorter neighbour over', () => {
    const document = edit(
      timeline(
        clip({
          id: 'clp_a',
          startMs: 0,
          durationMs: 5_000,
          transitionOut: { type: 'dissolve', durationMs: 400 },
        }),
        clip({ id: 'clp_b', startMs: 5_000, durationMs: 5_000 }),
        clip({ id: 'clp_c', startMs: 10_000, durationMs: 600 }),
      ),
      (draft) => {
        removeClips(draft, ['clp_b'])
      },
    )
    expect(byId(document, 'clp_a')?.transitionOut).toEqual({ type: 'dissolve', durationMs: 300 })
  })

  it('leaves a transition that still fits completely alone', () => {
    const before = pair()
    const document = edit(before, (draft) => {
      trimEnd(draft, 'clp_a', 4_000)
    })
    // 400 <= half of 4000: untouched, and untouched means no Immer patch and
    // therefore no spurious undo step.
    expect(byId(document, 'clp_a')?.transitionOut).toEqual({ type: 'dissolve', durationMs: 400 })
  })
})

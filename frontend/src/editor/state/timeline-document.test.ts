import { describe, expect, it } from 'vitest'

import {
  assetMsToTimelineMs,
  clipAt,
  emptyTimeline,
  snapCandidates,
  sourceOutMs,
  timelineDurationMs,
  timelineMsToAssetMs,
  violatedInvariants,
  type MediaClip,
  type TimelineDocument,
  type Track,
} from './timeline-document'

function clip(over: Partial<MediaClip> = {}): MediaClip {
  return {
    id: 'clp_1',
    assetId: 'ast_1',
    startMs: 0,
    durationMs: 8400,
    sourceInMs: 1200,
    speed: 1,
    volume: 1,
    audioFadeInMs: 0,
    audioFadeOutMs: 0,
    ...over,
  }
}

function track(clips: MediaClip[]): Track {
  return { id: 'trk_video', kind: 'video', index: 0, muted: false, locked: false, clips }
}

function timeline(clips: MediaClip[]): TimelineDocument {
  return { schemaVersion: 1, tracks: [track(clips)] }
}

describe('derived values', () => {
  it('derives sourceOut rather than storing it', () => {
    // contract §4.2 has no sourceOutMs on purpose: storing a derivable value
    // invites the two to disagree, and the renderer cannot tell which is right.
    expect(sourceOutMs(clip())).toBe(1200 + 8400)
  })

  it('accounts for speed when deriving sourceOut', () => {
    // A clip occupying 8.4 s at 2x consumes 16.8 s of its media.
    expect(sourceOutMs(clip({ speed: 2 }))).toBe(1200 + 16_800)
    expect(sourceOutMs(clip({ speed: 0.5 }))).toBe(1200 + 4200)
  })

  it('measures a timeline by its furthest clip end, not its clip count', () => {
    expect(timelineDurationMs(emptyTimeline())).toBe(0)
    expect(
      timelineDurationMs(
        timeline([
          clip({ id: 'a', startMs: 0, durationMs: 1000 }),
          clip({ id: 'b', startMs: 5000, durationMs: 2000 }),
        ]),
      ),
    ).toBe(7000)
  })
})

describe('timeline time to asset time', () => {
  it('applies the in-point', () => {
    // The M1 spike measured this to the millisecond and found a bug in it:
    // a clip that ignored sourceInMs started half a second early.
    const c = clip({ startMs: 0, sourceInMs: 500 })
    expect(timelineMsToAssetMs(c, 0)).toBe(500)
    expect(timelineMsToAssetMs(c, 1000)).toBe(1500)
  })

  it('applies the in-point and the offset together', () => {
    const c = clip({ startMs: 5100, sourceInMs: 300 })
    // The spike's own table: playhead 5100 -> 302 with a 2 ms offset.
    expect(timelineMsToAssetMs(c, 5102)).toBe(302)
    expect(timelineMsToAssetMs(c, 10_000)).toBe(5200)
  })

  it('multiplies by speed', () => {
    const c = clip({ startMs: 1000, sourceInMs: 0, speed: 2 })
    expect(timelineMsToAssetMs(c, 3000)).toBe(4000)
  })

  it('round-trips on a trimmed, sped-up clip', () => {
    // PHASE1-TASKS.md flags this conversion ⚠️ in M4 and asks for exactly this
    // test. Having it here means M4 inherits it rather than rediscovering it.
    const c = clip({ startMs: 5100, sourceInMs: 1200, speed: 1.5 })
    for (const timelineMs of [5100, 6000, 9999, 13_500]) {
      const assetMs = timelineMsToAssetMs(c, timelineMs)
      expect(assetMsToTimelineMs(c, assetMs)).toBe(timelineMs)
    }
  })
})

describe('what is under the playhead', () => {
  it('finds the clip covering a position', () => {
    const lane = track([
      clip({ id: 'a', startMs: 0, durationMs: 1000 }),
      clip({ id: 'b', startMs: 2000, durationMs: 1000 }),
    ])

    expect(clipAt(lane, 500)?.id).toBe('a')
    expect(clipAt(lane, 2500)?.id).toBe('b')
  })

  it('treats a clip end as exclusive', () => {
    // Two clips butted together must not both claim the boundary, or a cut
    // renders both frames.
    const lane = track([
      clip({ id: 'a', startMs: 0, durationMs: 1000 }),
      clip({ id: 'b', startMs: 1000, durationMs: 1000 }),
    ])

    expect(clipAt(lane, 1000)?.id).toBe('b')
    expect(clipAt(lane, 999)?.id).toBe('a')
  })

  it('reads a gap as nothing', () => {
    const lane = track([clip({ startMs: 2000, durationMs: 1000 })])
    expect(clipAt(lane, 500)).toBeNull()
    expect(clipAt(lane, 9000)).toBeNull()
  })
})

describe('snap candidates', () => {
  it('offers zero, every start and every end, sorted and deduplicated', () => {
    const document = timeline([
      clip({ id: 'a', startMs: 0, durationMs: 1000 }),
      clip({ id: 'b', startMs: 1000, durationMs: 500 }),
    ])
    // 0 appears as both the origin and a clip start; 1000 as an end and a
    // start. Both must appear once.
    expect(snapCandidates(document)).toEqual([0, 1000, 1500])
  })
})

describe('the invariants a client can check', () => {
  it('passes a well-formed document', () => {
    expect(
      violatedInvariants(
        timeline([
          clip({ id: 'a', startMs: 0, durationMs: 1000 }),
          clip({ id: 'b', startMs: 1000, durationMs: 1000 }),
        ]),
      ),
    ).toEqual([])
  })

  it('catches overlapping clips', () => {
    const problems = violatedInvariants(
      timeline([
        clip({ id: 'a', startMs: 0, durationMs: 2000 }),
        clip({ id: 'b', startMs: 1000, durationMs: 1000 }),
      ]),
    )
    expect(problems.some((p) => p.includes('overlaps'))).toBe(true)
  })

  it('catches a zero-length clip', () => {
    const problems = violatedInvariants(timeline([clip({ durationMs: 0 })]))
    expect(problems.some((p) => p.includes('no duration'))).toBe(true)
  })

  it('catches a duplicated id anywhere in the document', () => {
    const problems = violatedInvariants(
      timeline([
        clip({ id: 'same', startMs: 0, durationMs: 1000 }),
        clip({ id: 'same', startMs: 1000, durationMs: 1000 }),
      ]),
    )
    expect(problems.some((p) => p.includes('more than once'))).toBe(true)
  })

  it('catches a second track of the same kind', () => {
    // Phase 1 allows one track per kind (§4.3 invariant 8).
    const document = timeline([clip()])
    document.tracks.push({ ...track([]), id: 'trk_2' })
    expect(violatedInvariants(document).some((p) => p.includes('phase 1 allows one'))).toBe(true)
  })
})

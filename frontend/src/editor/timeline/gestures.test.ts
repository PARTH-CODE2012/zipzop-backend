import { describe, expect, it } from 'vitest'

import {
  SNAP_TOLERANCE_PX,
  clipsInWindow,
  marqueeHits,
  msAtLaneX,
  snapCandidatesFor,
  snapMs,
  zoneAt,
} from './gestures'
import type { MediaClip } from '@/editor/state/timeline-document'

const clip = (id: string, startMs: number, durationMs: number): MediaClip => ({
  id,
  assetId: 'ast_1',
  startMs,
  durationMs,
  sourceInMs: 0,
  speed: 1,
  volume: 1,
  audioFadeInMs: 0,
  audioFadeOutMs: 0,
  effects: [],
})

describe('snapMs', () => {
  it('measures its tolerance in pixels, not milliseconds', () => {
    // The same 200 ms gap: reachable when zoomed out, ignored when zoomed in.
    // A tolerance in milliseconds would feel sticky at one zoom and unusable at
    // the other, because what the user judges is distance on screen.
    expect(snapMs(4_800, [5_000], 20)).toBe(5_000)
    expect(snapMs(4_800, [5_000], 400)).toBe(4_800)
  })

  it('takes the nearest candidate when both are in range', () => {
    // 20 px/s puts the 8 px tolerance at 400 ms, so both edges are reachable
    // and the closer one has to win.
    expect(snapMs(4_150, [4_000, 4_200], 20)).toBe(4_200)
    expect(snapMs(4_050, [4_000, 4_200], 20)).toBe(4_000)
  })

  it('does nothing at all when the modifier suppresses it', () => {
    // The only way to place a clip one frame off an edge it wants to stick to.
    expect(snapMs(5_001, [5_000], 100, true)).toBe(5_001)
  })

  it('leaves a position alone when nothing is close', () => {
    expect(snapMs(1_000, [8_000], 40)).toBe(1_000)
  })
})

describe('snapCandidatesFor', () => {
  it('always offers zero and the playhead', () => {
    expect(snapCandidatesFor([], null, 7_000)).toEqual([0, 7_000])
  })

  it('excludes the clip being dragged', () => {
    // Left in, a clip snaps to where it already is and cannot be nudged off its
    // own edge.
    const clips = [clip('clp_a', 0, 1_000), clip('clp_b', 4_000, 1_000)]
    expect(snapCandidatesFor(clips, 'clp_a', 0)).toEqual([0, 4_000, 5_000])
  })
})

describe('clipsInWindow', () => {
  const clips = [clip('a', 0, 1_000), clip('b', 5_000, 1_000), clip('c', 50_000, 1_000)]

  it('keeps what the window touches, including clips that straddle an edge', () => {
    expect(clipsInWindow(clips, 500, 6_000).map((c) => c.id)).toEqual(['a', 'b'])
  })

  it('drops what is far off screen', () => {
    expect(clipsInWindow(clips, 0, 2_000).map((c) => c.id)).toEqual(['a'])
  })

  it('overscans so nothing pops in at the edge during a scroll', () => {
    expect(clipsInWindow(clips, 0, 2_000, 4_000).map((c) => c.id)).toEqual(['a', 'b'])
  })

  it('stays cheap at 500 clips', () => {
    const many = Array.from({ length: 500 }, (_, i) => clip(`c${i}`, i * 1_000, 900))
    expect(clipsInWindow(many, 10_000, 20_000)).toHaveLength(11)
  })
})

describe('marqueeHits', () => {
  const clips = [clip('a', 0, 1_000), clip('b', 5_000, 5_000), clip('c', 20_000, 1_000)]

  it('catches what it touches, not only what it encloses', () => {
    // A lasso across the middle of a long clip means that clip.
    expect(marqueeHits(clips, 6_000, 7_000)).toEqual(['b'])
  })

  it('works dragged right to left', () => {
    expect(marqueeHits(clips, 7_000, 500)).toEqual(['a', 'b'])
  })
})

describe('zoneAt', () => {
  it('gives the edges to trimming and the middle to moving', () => {
    expect(zoneAt(2, 200)).toBe('trim-start')
    expect(zoneAt(100, 200)).toBe('move')
    expect(zoneAt(197, 200)).toBe('trim-end')
  })

  it('shrinks the handles rather than swallowing a narrow clip', () => {
    // Two 8px handles on a 14px clip would leave nowhere to grab it.
    expect(zoneAt(7, 14)).toBe('move')
  })
})

describe('msAtLaneX', () => {
  it('accounts for the scroll and never goes negative', () => {
    expect(msAtLaneX(100, 100, 0)).toBe(1_000)
    expect(msAtLaneX(100, 100, 50)).toBe(1_500)
    expect(msAtLaneX(-500, 100, 0)).toBe(0)
  })
})

describe('SNAP_TOLERANCE_PX', () => {
  it('is a pixel budget a hand can hit', () => {
    expect(SNAP_TOLERANCE_PX).toBeGreaterThan(4)
    expect(SNAP_TOLERANCE_PX).toBeLessThan(16)
  })
})

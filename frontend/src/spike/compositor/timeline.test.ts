import { describe, expect, it } from 'vitest'

import {
  buildSpikeTimeline,
  clipEndMs,
  crossfadeGain,
  isActive,
  resolveFrame,
  sourceTimeMs,
  timelineTimeMs,
  type SpikeMediaClip,
} from './timeline'

function clip(overrides: Partial<SpikeMediaClip> = {}): SpikeMediaClip {
  return {
    id: 'clp_test',
    src: '/test.mp4',
    label: 'T',
    assetDurationMs: 60_000,
    startMs: 0,
    durationMs: 5_000,
    sourceInMs: 0,
    speed: 1,
    effects: [],
    ...overrides,
  }
}

describe('asset time ↔ timeline time', () => {
  it('round-trips at speed 1', () => {
    const c = clip({ startMs: 4_000, sourceInMs: 1_200 })
    expect(sourceTimeMs(c, 4_000)).toBe(1_200)
    expect(timelineTimeMs(c, 1_200)).toBe(4_000)
    expect(timelineTimeMs(c, sourceTimeMs(c, 6_500))).toBe(6_500)
  })

  it('round-trips on a trimmed, sped-up clip', () => {
    // The case docs/04-frontend-architecture.md §7 calls out: get this wrong
    // and captions land slightly out of sync, which reads as a transcription
    // bug rather than an arithmetic one.
    const c = clip({ startMs: 2_000, sourceInMs: 8_000, speed: 2 })

    // One second of timeline consumes two seconds of the asset.
    expect(sourceTimeMs(c, 3_000)).toBe(10_000)
    expect(timelineTimeMs(c, 10_000)).toBe(3_000)

    for (const t of [2_000, 2_333, 4_750, 6_999]) {
      expect(timelineTimeMs(c, sourceTimeMs(c, t))).toBeCloseTo(t, 9)
    }
  })

  it('round-trips on a slowed clip', () => {
    const c = clip({ startMs: 500, sourceInMs: 250, speed: 0.5 })
    expect(sourceTimeMs(c, 1_500)).toBe(750)
    expect(timelineTimeMs(c, 750)).toBe(1_500)
  })
})

describe('isActive', () => {
  const c = clip({ startMs: 1_000, durationMs: 2_000 })

  it('includes its start and excludes its end', () => {
    expect(isActive(c, 999)).toBe(false)
    expect(isActive(c, 1_000)).toBe(true)
    expect(isActive(c, 2_999)).toBe(true)
    // Half-open, so butted clips never both claim the boundary frame.
    expect(isActive(c, 3_000)).toBe(false)
  })
})

describe('resolveFrame', () => {
  it('shows nothing in a gap', () => {
    const a = clip({ id: 'a', startMs: 0, durationMs: 1_000 })
    const b = clip({ id: 'b', startMs: 2_000, durationMs: 1_000 })
    expect(resolveFrame([a, b], 1_500)).toEqual({ base: null, over: null, mix: 0 })
  })

  it('shows one clip when they only butt together', () => {
    const a = clip({ id: 'a', startMs: 0, durationMs: 1_000 })
    const b = clip({ id: 'b', startMs: 1_000, durationMs: 1_000 })

    const before = resolveFrame([a, b], 999)
    expect(before.base?.id).toBe('a')
    expect(before.over).toBeNull()

    const after = resolveFrame([a, b], 1_000)
    expect(after.base?.id).toBe('b')
    expect(after.over).toBeNull()
    expect(after.mix).toBe(0)
  })

  it('ramps the mix across an overlap, base first', () => {
    const a = clip({ id: 'a', startMs: 0, durationMs: 2_000 })
    const b = clip({ id: 'b', startMs: 1_000, durationMs: 2_000 })

    expect(resolveFrame([a, b], 1_000)).toMatchObject({ mix: 0 })
    expect(resolveFrame([a, b], 1_500).mix).toBeCloseTo(0.5, 9)
    expect(resolveFrame([a, b], 1_999).mix).toBeCloseTo(0.999, 9)

    const mid = resolveFrame([a, b], 1_500)
    expect(mid.base?.id).toBe('a')
    expect(mid.over?.id).toBe('b')
  })

  it('does not depend on the order the clips arrive in', () => {
    const a = clip({ id: 'a', startMs: 0, durationMs: 2_000 })
    const b = clip({ id: 'b', startMs: 1_000, durationMs: 2_000 })
    expect(resolveFrame([b, a], 1_500)).toEqual(resolveFrame([a, b], 1_500))
  })
})

describe('crossfadeGain', () => {
  it('is equal-power: the two gains square to one at every point', () => {
    for (const mix of [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1]) {
      const base = crossfadeGain(mix, 'base')
      const over = crossfadeGain(mix, 'over')
      expect(base * base + over * over).toBeCloseTo(1, 9)
    }
  })

  it('is full on the outgoing clip at the start and the incoming one at the end', () => {
    expect(crossfadeGain(0, 'base')).toBeCloseTo(1, 9)
    expect(crossfadeGain(0, 'over')).toBeCloseTo(0, 9)
    expect(crossfadeGain(1, 'base')).toBeCloseTo(0, 9)
    expect(crossfadeGain(1, 'over')).toBeCloseTo(1, 9)
  })

  it('clamps out-of-range input rather than producing negative gain', () => {
    expect(crossfadeGain(-2, 'base')).toBeCloseTo(1, 9)
    expect(crossfadeGain(4, 'over')).toBeCloseTo(1, 9)
  })
})

describe('buildSpikeTimeline', () => {
  for (const mode of ['cut', 'crossfade'] as const) {
    describe(mode, () => {
      const timeline = buildSpikeTimeline(mode)

      it('never reads past the end of its media (invariant 4)', () => {
        for (const c of timeline.video) {
          expect(c.sourceInMs + c.durationMs * c.speed).toBeLessThanOrEqual(c.assetDurationMs)
        }
      })

      it('gives every clip a positive duration and a unique id (invariants 3 and 6)', () => {
        const ids = new Set<string>()
        for (const c of [...timeline.video, ...timeline.text]) {
          expect(c.durationMs).toBeGreaterThan(0)
          expect(ids.has(c.id)).toBe(false)
          ids.add(c.id)
        }
      })

      it('orders clips by ascending start', () => {
        for (const track of [timeline.video, timeline.text]) {
          for (let i = 1; i < track.length; i++) {
            expect(track[i]?.startMs).toBeGreaterThanOrEqual(track[i - 1]?.startMs ?? 0)
          }
        }
      })

      it('never overlaps text clips (invariant 1)', () => {
        for (let i = 1; i < timeline.text.length; i++) {
          const previous = timeline.text[i - 1]
          const current = timeline.text[i]
          if (previous === undefined || current === undefined) continue
          expect(clipEndMs(previous)).toBeLessThanOrEqual(current.startMs)
        }
      })

      it('keeps every caption inside the timeline', () => {
        for (const c of timeline.text) {
          expect(clipEndMs(c)).toBeLessThanOrEqual(timeline.durationMs)
        }
      })

      it('keeps the transition under half the shorter clip (invariant 7)', () => {
        const shortest = Math.min(...timeline.video.map((c) => c.durationMs))
        expect(timeline.transitionMs).toBeLessThanOrEqual(shortest / 2)
      })

      it('runs to the end of its last clip with no gap', () => {
        const [a, b] = timeline.video
        expect(a).toBeDefined()
        expect(b).toBeDefined()
        if (a === undefined || b === undefined) return
        expect(b.startMs).toBeLessThanOrEqual(clipEndMs(a))
        expect(timeline.durationMs).toBe(clipEndMs(b))
      })
    })
  }

  it('overlaps the two clips only in crossfade mode', () => {
    const cut = buildSpikeTimeline('cut')
    const cross = buildSpikeTimeline('crossfade')

    expect(cut.transitionMs).toBe(0)
    expect(resolveFrame(cut.video, cut.video[0]?.durationMs ?? 0).over).toBeNull()

    expect(cross.transitionMs).toBeGreaterThan(0)
    const insideTransition = (cross.video[1]?.startMs ?? 0) + 10
    expect(resolveFrame(cross.video, insideTransition).over).not.toBeNull()
    expect(cross.durationMs).toBe(cut.durationMs - cross.transitionMs)
  })
})

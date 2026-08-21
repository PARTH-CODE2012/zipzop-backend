/**
 * ⚠️ The conversion the checklist flags, tested on the clip that breaks it.
 *
 * Every test here uses a clip that is **trimmed and sped up** — `sourceInMs`
 * 4000, `speed` 1.5 — because a clip that starts at zero and plays at 1× makes
 * asset time and timeline time identical, and a test against that one passes
 * whether the conversion exists or not.
 */

import { describe, expect, it } from 'vitest'

import { placeRemovals, placeWords, removedFraction, LOW_CONFIDENCE } from './results'
import type { CaptionWord, TrimRemoval } from './results'
import type { MediaClip } from '@/editor/state/timeline-document'

/**
 * Shows asset 4000–13000 ms (9 s of media) in 6 s of timeline, starting at 2 s.
 * Nothing about it is round, on purpose.
 */
function clip(over: Partial<MediaClip> = {}): MediaClip {
  return {
    id: 'clp_a',
    assetId: 'ast_1',
    startMs: 2_000,
    durationMs: 6_000,
    sourceInMs: 4_000,
    speed: 1.5,
    volume: 1,
    audioFadeInMs: 0,
    audioFadeOutMs: 0,
    effects: [],
    ...over,
  }
}

const word = (w: string, s: number, e: number, over: Partial<CaptionWord> = {}): CaptionWord => ({
  w,
  s,
  e,
  c: 0.95,
  em: 0,
  ...over,
})

describe('placing caption words', () => {
  it('converts asset time to timeline time through the clip', () => {
    // Asset 5500 is 1500 ms past the clip's in-point; at 1.5x that is 1000 ms
    // of timeline, so it lands at 2000 + 1000 = 3000.
    const [placed] = placeWords([word('hello', 5_500, 6_100)], clip())

    expect(placed?.startMs).toBe(3_000)
    // 600 ms of media at 1.5x is 400 ms of timeline.
    expect(placed?.durationMs).toBe(400)
  })

  it('drops words the clip does not show', () => {
    // The server transcribed the whole file; this clip shows 4000-13000.
    const placed = placeWords(
      [
        word('before', 500, 900),
        word('inside', 6_000, 6_400),
        word('after', 20_000, 20_500),
      ],
      clip(),
    )

    expect(placed.map((p) => p.text)).toEqual(['inside'])
  })

  it('keeps a word that straddles the edge, clipped', () => {
    // Half a word of audio is still a word the viewer hears; dropping it leaves
    // a silent gap exactly where the cut is most noticeable.
    const [placed] = placeWords([word('straddles', 3_600, 4_600)], clip())

    expect(placed?.startMs).toBe(2_000) // the clip's own start
    expect(placed?.durationMs).toBe(400) // only the 600 ms inside, at 1.5x
  })

  it('never lets two words overlap, whatever the speed', () => {
    // Invariant 1 forbids it, so the server would reject the save.
    const placed = placeWords(
      [word('a', 4_000, 4_020), word('b', 4_020, 4_040), word('c', 4_040, 4_060)],
      clip({ speed: 4 }),
    )

    for (let i = 1; i < placed.length; i += 1) {
      const previous = placed[i - 1]!
      expect(placed[i]!.startMs).toBeGreaterThanOrEqual(previous.startMs + previous.durationMs)
    }
  })

  it('flags what the user should check', () => {
    const placed = placeWords(
      [
        word('Anthropic', 5_000, 5_600, { c: 0.42 }),
        word('the', 5_700, 5_900, { c: 0.99 }),
      ],
      clip(),
    )

    expect(placed[0]?.uncertain).toBe(true)
    expect(placed[1]?.uncertain).toBe(false)
    // The contract's own threshold, not one invented here.
    expect(LOW_CONFIDENCE).toBe(0.7)
  })

  it('carries emphasis through unchanged', () => {
    const [placed] = placeWords([word('LOUD', 5_000, 5_400, { em: 0.83 })], clip())
    expect(placed?.emphasis).toBe(0.83)
  })

  it('is identity on an untrimmed clip at 1x', () => {
    const plain = clip({ startMs: 0, sourceInMs: 0, speed: 1, durationMs: 30_000 })
    const [placed] = placeWords([word('hello', 1_234, 1_678)], plain)

    expect(placed?.startMs).toBe(1_234)
    expect(placed?.durationMs).toBe(444)
  })
})

describe('placing smart-trim removals', () => {
  const removal = (startMs: number, endMs: number): TrimRemoval => ({
    startMs,
    endMs,
    reason: 'silence',
    confidence: 0.99,
  })

  it('maps ranges through the same conversion', () => {
    const [placed] = placeRemovals([removal(5_500, 7_000)], clip())

    expect(placed?.startMs).toBe(3_000)
    expect(placed?.endMs).toBe(4_000) // 1500 ms of media at 1.5x
  })

  it('clips a range that runs past the end of the clip', () => {
    const [placed] = placeRemovals([removal(12_000, 30_000)], clip())

    expect(placed?.startMs).toBe(7_333)
    expect(placed?.endMs).toBe(8_000) // the clip ends at 2000 + 6000
  })

  it('drops ranges outside the window entirely', () => {
    expect(placeRemovals([removal(0, 1_000), removal(50_000, 60_000)], clip())).toEqual([])
  })

  it('stays ordered and non-overlapping', () => {
    const placed = placeRemovals(
      [removal(4_500, 5_000), removal(6_000, 6_500), removal(9_000, 10_000)],
      clip(),
    )

    for (let i = 1; i < placed.length; i += 1) {
      expect(placed[i]!.startMs).toBeGreaterThanOrEqual(placed[i - 1]!.endMs)
    }
  })

  it('reports how much of the clip would go', () => {
    // Half the clip's 6 s.
    const placed = placeRemovals([removal(4_000, 8_500)], clip())
    expect(removedFraction(placed, clip())).toBeCloseTo(0.5, 2)
  })
})

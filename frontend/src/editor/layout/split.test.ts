/**
 * The timeline's height, and the two bounds it may not cross.
 *
 * M4.5 item 7. The case worth having a test for is the one that only happens on
 * a short window: the two minimums do not both fit, and the layout has to decide
 * which one gives. Getting that wrong produces an editor with no picture and no
 * visible way to get it back.
 */

import { describe, expect, it } from 'vitest'

import {
  clampTimelineHeight,
  DEFAULT_TIMELINE_PX,
  heightFromDrag,
  MIN_STAGE_PX,
  MIN_TIMELINE_PX,
  readStoredHeight,
  writeStoredHeight,
} from '@/editor/layout/split'

const TALL = 1000

describe('clampTimelineHeight', () => {
  it('passes a reasonable height through', () => {
    expect(clampTimelineHeight(300, TALL)).toBe(300)
  })

  it('will not shrink below a usable timeline', () => {
    expect(clampTimelineHeight(10, TALL)).toBe(MIN_TIMELINE_PX)
  })

  it('will not squeeze the picture out', () => {
    expect(clampTimelineHeight(TALL, TALL)).toBe(TALL - MIN_STAGE_PX)
  })

  it('keeps the picture on screen when the window cannot satisfy both', () => {
    // 300px of viewport cannot hold 140 of timeline and 220 of stage. Honouring
    // the request would collapse the stage to nothing and leave the user with
    // no preview and a divider they can no longer see to drag back.
    const viewport = 300
    expect(MIN_TIMELINE_PX + MIN_STAGE_PX).toBeGreaterThan(viewport)
    expect(clampTimelineHeight(280, viewport)).toBe(MIN_TIMELINE_PX)
  })

  it('rounds, because a fractional pixel height serves nobody', () => {
    expect(clampTimelineHeight(300.6, TALL)).toBe(301)
  })

  it('falls back to the default rather than propagating a broken number', () => {
    expect(clampTimelineHeight(Number.NaN, TALL)).toBe(DEFAULT_TIMELINE_PX)
    expect(clampTimelineHeight(Number.POSITIVE_INFINITY, TALL)).toBe(TALL - MIN_STAGE_PX)
  })
})

describe('heightFromDrag', () => {
  it('grows the timeline when the divider is dragged up', () => {
    // The divider is above the timeline, so up is taller. The sign lives in one
    // place rather than being inverted here and compensated for elsewhere.
    expect(heightFromDrag(200, -50)).toBe(250)
  })

  it('shrinks it when dragged down', () => {
    expect(heightFromDrag(200, 50)).toBe(150)
  })
})

describe('stored height', () => {
  function fakeStorage(initial: Record<string, string> = {}) {
    const map = new Map(Object.entries(initial))
    return {
      getItem: (key: string) => map.get(key) ?? null,
      setItem: (key: string, value: string) => void map.set(key, value),
      read: () => Object.fromEntries(map),
    }
  }

  it('round-trips', () => {
    const storage = fakeStorage()
    writeStoredHeight(storage, 275)
    expect(readStoredHeight(storage)).toBe(275)
  })

  it('is absent rather than zero on a fresh browser', () => {
    expect(readStoredHeight(fakeStorage())).toBeNull()
  })

  it('treats a corrupted value as absent', () => {
    expect(readStoredHeight(fakeStorage({ 'zipzop.timelineHeightPx': 'tall' }))).toBeNull()
  })

  it('survives storage that throws', () => {
    // Private browsing and blocked third-party storage both throw on access
    // rather than returning null, and a missing layout preference is not worth
    // taking the editor down for.
    const hostile = {
      getItem: () => {
        throw new DOMException('denied')
      },
      setItem: () => {
        throw new DOMException('denied')
      },
    }
    expect(readStoredHeight(hostile)).toBeNull()
    expect(() => writeStoredHeight(hostile, 200)).not.toThrow()
  })

  it('does nothing without a storage, rather than reaching for a global', () => {
    expect(readStoredHeight(null)).toBeNull()
    expect(() => writeStoredHeight(null, 200)).not.toThrow()
  })
})

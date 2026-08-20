import { describe, expect, it } from 'vitest'

import {
  DEFAULT_ZOOM,
  MAX_ZOOM,
  MIN_ZOOM,
  clampZoom,
  formatTimecode,
  msToPx,
  pxToMs,
  scrollForAnchoredZoom,
  tickLabel,
  tickStepMs,
  ticksForWindow,
} from './scale'

describe('time and pixels', () => {
  it('converts round-trip without drifting', () => {
    for (const zoom of [MIN_ZOOM, 7, DEFAULT_ZOOM, 133, MAX_ZOOM]) {
      for (const ms of [0, 1, 500, 1000, 8400, 623_480]) {
        expect(pxToMs(msToPx(ms, zoom), zoom)).toBe(ms)
      }
    }
  })

  it('returns an integer number of milliseconds, always', () => {
    // Whatever this produces can become a clip's startMs, and a float there is
    // exactly the drift the project's conventions exist to prevent.
    for (const px of [0.5, 1.3, 17.77, 129.999]) {
      const ms = pxToMs(px, 37)
      expect(Number.isInteger(ms)).toBe(true)
    }
  })

  it('keeps pixels fractional so an edge can sit between two of them', () => {
    // The other direction must NOT round: a clip boundary that snapped to
    // whole pixels would visibly jump while zooming.
    expect(msToPx(1, 40)).toBeCloseTo(0.04, 6)
  })

  it('clamps the zoom to a usable range', () => {
    expect(clampZoom(0)).toBe(MIN_ZOOM)
    expect(clampZoom(10_000)).toBe(MAX_ZOOM)
    expect(clampZoom(50)).toBe(50)
  })

  it('holds a point still while zooming around it', () => {
    // Zooming with the cursor over a clip must leave that clip under the
    // cursor. The scroll offset is what makes that true.
    const anchorMs = 8400
    const anchorPx = 320
    for (const zoom of [40, 200, MAX_ZOOM]) {
      const scroll = scrollForAnchoredZoom(anchorMs, anchorPx, zoom)
      expect(scroll).toBeGreaterThan(0) // not clamped, so the anchor must hold
      expect(msToPx(anchorMs, zoom) - scroll).toBeCloseTo(anchorPx, 6)
    }
  })

  it('stops at the beginning rather than scrolling before zero', () => {
    // Zoomed far out, holding 8.4 s at x=320 would need the timeline to start
    // before 0. It cannot, so the view pins to the start and the anchor
    // drifts — the alternative is a blank gutter to the left of time zero.
    expect(scrollForAnchoredZoom(8400, 320, MIN_ZOOM)).toBe(0)
  })
})

describe('ruler ticks', () => {
  it('picks a step a person reads without arithmetic', () => {
    // Never 8 or 16 seconds, however well they would space out.
    const readable = new Set([
      100, 200, 500, 1_000, 2_000, 5_000, 10_000, 15_000, 30_000, 60_000, 120_000,
      300_000, 600_000, 900_000, 1_800_000, 3_600_000,
    ])
    for (let zoom = MIN_ZOOM; zoom <= MAX_ZOOM; zoom += 1) {
      expect(readable.has(tickStepMs(zoom))).toBe(true)
    }
  })

  it('never puts labels closer together than the minimum gap', () => {
    for (let zoom = MIN_ZOOM; zoom <= MAX_ZOOM; zoom += 1) {
      const majors = ticksForWindow(0, 60_000, zoom).filter((t) => t.major)
      for (let i = 1; i < majors.length; i += 1) {
        const gap = (majors[i]?.px ?? 0) - (majors[i - 1]?.px ?? 0)
        expect(gap).toBeGreaterThanOrEqual(80 - 1e-6)
      }
    }
  })

  it('costs the window, not the document', () => {
    // The property that matters: a 60-minute project does not lay out ticks
    // for 60 minutes. Ten seconds deep into an hour costs the same as ten
    // seconds at the start.
    const nearTheStart = ticksForWindow(0, 10_000, MAX_ZOOM)
    const anHourIn = ticksForWindow(3_600_000, 3_610_000, MAX_ZOOM)
    expect(anHourIn.length).toBeCloseTo(nearTheStart.length, -1)

    // And the ticks really are only the window's.
    expect(anHourIn.at(0)?.ms ?? 0).toBeGreaterThanOrEqual(3_599_000)
    expect(anHourIn.at(-1)?.ms ?? 0).toBeLessThanOrEqual(3_610_000)
  })

  it('never emits a negative tick', () => {
    expect(ticksForWindow(-5000, 2000, DEFAULT_ZOOM).every((t) => t.ms >= 0)).toBe(true)
  })

  it('shows milliseconds only once they are visible', () => {
    expect(tickLabel(1500, MIN_ZOOM)).not.toContain('.')
    expect(tickLabel(1500, MAX_ZOOM)).toContain('.')
  })
})

describe('timecode', () => {
  it('formats without an hours field until there are hours', () => {
    expect(formatTimecode(0)).toBe('0:00')
    expect(formatTimecode(8400)).toBe('0:08')
    expect(formatTimecode(65_000)).toBe('1:05')
    expect(formatTimecode(3_600_000)).toBe('1:00:00')
    expect(formatTimecode(3_725_000)).toBe('1:02:05')
  })

  it('pads milliseconds to three digits', () => {
    // 0.5 s is .500, not .5 — a timecode that changes width jitters.
    expect(formatTimecode(500, { withMillis: true })).toBe('0:00.500')
    expect(formatTimecode(8400, { withMillis: true })).toBe('0:08.400')
    expect(formatTimecode(1007, { withMillis: true })).toBe('0:01.007')
  })

  it('handles a negative offset', () => {
    expect(formatTimecode(-1500, { withMillis: true })).toBe('-0:01.500')
  })
})

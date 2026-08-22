import { describe, expect, it } from 'vitest'

import { columnsForWindow, type Peaks } from './waveform'

/** `seconds` of peaks at 100 a second, all at `level`. */
function flat(seconds: number, level: number): Peaks {
  return { peaks: new Array(seconds * 100).fill(level), bucketsPerSecond: 100 }
}

describe('columnsForWindow', () => {
  it('produces exactly one value per pixel column', () => {
    const columns = columnsForWindow(flat(10, 0.5), 0, 10_000, 320)
    expect(columns.length).toBe(320)
  })

  it('takes the peak of the buckets in a column, not the average', () => {
    // The assertion that matters. A waveform that averages still looks like a
    // waveform, so nothing on screen would reveal the mistake — but every
    // transient the user is looking for would be flattened away.
    const peaks = new Array(100).fill(0.1)
    peaks[50] = 1.0
    const columns = columnsForWindow({ peaks, bucketsPerSecond: 100 }, 0, 1000, 10)

    expect(Math.max(...columns)).toBe(1.0)
    // 100 buckets over 10 columns: the spike lands in the sixth.
    expect(columns[5]).toBe(1.0)
    expect(columns[0]).toBeCloseTo(0.1, 6)
  })

  it('reads at least one bucket per column when zoomed past one bucket a pixel', () => {
    // 100 ms across 400 columns is a quarter of a bucket each. Without the
    // guard, floor and ceil collapse onto the same index and three columns in
    // four come back empty — the waveform combs.
    const columns = columnsForWindow(flat(10, 0.7), 0, 100, 400)
    expect(columns.length).toBe(400)
    expect([...columns].every((value) => value > 0)).toBe(true)
  })

  it('reads silence, not a gap, past the end of the data', () => {
    const columns = columnsForWindow(flat(1, 0.8), 0, 4000, 100)
    expect(columns[0]).toBeCloseTo(0.8, 6)
    expect(columns[99]).toBe(0) // past the single second of audio
  })

  it('handles a window that starts before zero', () => {
    const columns = columnsForWindow(flat(2, 0.6), -500, 500, 50)
    expect(columns.length).toBe(50)
    expect([...columns].every((value) => value >= 0 && value <= 1)).toBe(true)
  })

  it('returns an empty result rather than throwing on degenerate input', () => {
    expect(columnsForWindow(flat(1, 0.5), 0, 1000, 0).length).toBe(0)
    expect(columnsForWindow(flat(1, 0.5), 1000, 0, 100).every((v) => v === 0)).toBe(true)
    expect(
      columnsForWindow({ peaks: [], bucketsPerSecond: 100 }, 0, 1000, 100).every((v) => v === 0),
    ).toBe(true)
  })

  it('stays cheap on a ten-minute clip', () => {
    // 60 000 peaks reduced to a 1200-pixel track. This is the case the "never
    // one DOM node per peak" rule exists for; it must also not be quadratic.
    const long = flat(600, 0.4)
    expect(long.peaks.length).toBe(60_000)

    const started = performance.now()
    const columns = columnsForWindow(long, 0, 600_000, 1200)
    const elapsed = performance.now() - started

    expect(columns.length).toBe(1200)
    expect(elapsed).toBeLessThan(50)
  })

  it('never reports a level outside 0 to 1', () => {
    const columns = columnsForWindow(flat(5, 1.0), 0, 5000, 200)
    expect([...columns].every((value) => value >= 0 && value <= 1)).toBe(true)
  })
})

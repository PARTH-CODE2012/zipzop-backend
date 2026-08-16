import { describe, expect, it } from 'vitest'

import { fitContain } from './renderer'

describe('fitContain', () => {
  it('fills exactly when the aspect ratios match', () => {
    expect(fitContain(1920, 1080, 960, 540)).toEqual([1, 1, 0, 0])
  })

  it('letterboxes a wide source into a square canvas', () => {
    const [w, h, x, y] = fitContain(1600, 800, 1000, 1000)
    expect(w).toBe(1)
    expect(h).toBeCloseTo(0.5, 9)
    expect(x).toBe(0)
    expect(y).toBeCloseTo(0.25, 9)
  })

  it('letterboxes a 16:9 proxy into a 9:16 project', () => {
    // The default project shape. A landscape proxy has to sit in a band across
    // the middle, not be stretched — stretching is the failure that only shows
    // up once someone watches the export.
    const [w, h, x, y] = fitContain(854, 480, 1080, 1920)
    expect(w).toBe(1)
    expect(h).toBeCloseTo(1080 / 1920 / (854 / 480), 9)
    expect(x).toBe(0)
    expect(y).toBeCloseTo((1 - h) / 2, 9)
  })

  it('pillarboxes a portrait source into a 16:9 project', () => {
    const [w, h, x, y] = fitContain(1080, 1920, 1920, 1080)
    expect(h).toBe(1)
    expect(w).toBeCloseTo(1080 / 1920 / (1920 / 1080), 9)
    expect(y).toBe(0)
    expect(x).toBeCloseTo((1 - w) / 2, 9)
  })

  it('always centres what it draws', () => {
    for (const [sw, sh, dw, dh] of [
      [854, 480, 1920, 1080],
      [1080, 1920, 1920, 1080],
      [640, 640, 1280, 720],
    ] as const) {
      const [w, h, x, y] = fitContain(sw, sh, dw, dh)
      expect(x * 2 + w).toBeCloseTo(1, 9)
      expect(y * 2 + h).toBeCloseTo(1, 9)
      expect(w).toBeLessThanOrEqual(1)
      expect(h).toBeLessThanOrEqual(1)
    }
  })

  it('falls back to filling when a dimension is not known yet', () => {
    // videoWidth is 0 until metadata arrives; dividing by it would put NaN in
    // a uniform, and a NaN uniform silently draws nothing at all.
    expect(fitContain(0, 0, 1920, 1080)).toEqual([1, 1, 0, 0])
    expect(fitContain(854, 480, 0, 0)).toEqual([1, 1, 0, 0])
  })
})

import { describe, expect, it } from 'vitest'

import { CubeParseError, parseCubeLut } from './cube'

/** An identity LUT of the given size, written the way a real .cube file is. */
function identityCube(size: number, header = ''): string {
  const lines = [header, `LUT_3D_SIZE ${size}`]
  const last = size - 1
  for (let b = 0; b < size; b++) {
    for (let g = 0; g < size; g++) {
      for (let r = 0; r < size; r++) {
        lines.push(`${r / last} ${g / last} ${b / last}`)
      }
    }
  }
  return lines.join('\n')
}

describe('parseCubeLut', () => {
  it('reads a minimal identity LUT', () => {
    const lut = parseCubeLut(identityCube(2))
    expect(lut.size).toBe(2)
    expect(lut.rgba).toHaveLength(2 * 2 * 2 * 4)
    // First entry is black, last is white, alpha is always opaque.
    expect(Array.from(lut.rgba.slice(0, 4))).toEqual([0, 0, 0, 255])
    expect(Array.from(lut.rgba.slice(-4))).toEqual([255, 255, 255, 255])
  })

  it('keeps red as the fastest-varying axis', () => {
    // That ordering is what makes the file uploadable to texImage3D unchanged.
    // Reversed, the grade comes out with its channels swapped — which looks
    // like a bad LUT rather than a bad reader.
    const lut = parseCubeLut(identityCube(3))
    const entry = (index: number) => Array.from(lut.rgba.slice(index * 4, index * 4 + 3))

    expect(entry(0)).toEqual([0, 0, 0]) // r=0 g=0 b=0
    expect(entry(1)).toEqual([128, 0, 0]) // r stepped
    expect(entry(3)).toEqual([0, 128, 0]) // g stepped after a full red run
    expect(entry(9)).toEqual([0, 0, 128]) // b stepped after a full red×green plane
  })

  it('reads the title, and tolerates comments and blank lines', () => {
    const text = [
      '# exported by something',
      'TITLE "cinematic_warm"',
      '',
      '   ',
      identityCube(2),
      '# trailing comment',
    ].join('\n')

    expect(parseCubeLut(text).title).toBe('cinematic_warm')
  })

  it('accepts an explicit 0–1 domain', () => {
    const text = ['DOMAIN_MIN 0.0 0.0 0.0', 'DOMAIN_MAX 1.0 1.0 1.0', identityCube(2)].join('\n')
    expect(parseCubeLut(text).size).toBe(2)
  })

  it('clamps values outside 0–1 instead of wrapping them', () => {
    const text = ['LUT_3D_SIZE 2', ...Array<string>(8).fill('-0.5 1.5 0.5')].join('\n')
    const lut = parseCubeLut(text)
    expect(Array.from(lut.rgba.slice(0, 4))).toEqual([0, 255, 128, 255])
  })

  it('rejects a truncated table', () => {
    const full = identityCube(3).split('\n')
    const truncated = full.slice(0, full.length - 1).join('\n')
    expect(() => parseCubeLut(truncated)).toThrow(CubeParseError)
    expect(() => parseCubeLut(truncated)).toThrow(/expected 27 entries.*found 26/)
  })

  it('rejects a table with extra rows', () => {
    expect(() => parseCubeLut(`${identityCube(2)}\n0 0 0`)).toThrow(/extra table rows/)
  })

  it('rejects a 1D LUT by name rather than by failing to parse it', () => {
    expect(() => parseCubeLut('LUT_1D_SIZE 32\n0 0 0')).toThrow(/1D LUT/)
  })

  it('rejects a missing size', () => {
    expect(() => parseCubeLut('0.0 0.0 0.0')).toThrow(/before LUT_3D_SIZE/)
  })

  it('rejects a non-unit domain rather than silently misapplying the grade', () => {
    const text = ['DOMAIN_MAX 4.0 4.0 4.0', identityCube(2)].join('\n')
    expect(() => parseCubeLut(text)).toThrow(/only the 0–1 domain/)
  })

  it('rejects an implausible size', () => {
    expect(() => parseCubeLut('LUT_3D_SIZE 0')).toThrow(/must be an integer/)
    expect(() => parseCubeLut('LUT_3D_SIZE 512')).toThrow(/must be an integer/)
  })

  it('rejects a malformed row', () => {
    expect(() => parseCubeLut('LUT_3D_SIZE 2\n0.0 0.0')).toThrow(/expected three numbers/)
    expect(() => parseCubeLut('LUT_3D_SIZE 2\n0.0 nope 0.0')).toThrow(/not a number triplet/)
  })

  it('handles CRLF line endings', () => {
    const lut = parseCubeLut(identityCube(2).split('\n').join('\r\n'))
    expect(lut.size).toBe(2)
  })
})

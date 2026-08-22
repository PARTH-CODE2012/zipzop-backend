/**
 * The catalogue, and the one property that matters about it.
 *
 * ⚠️ **A look the server can recommend but the browser cannot draw is worse
 * than no recommendation**: the result arrives, the effect is written into the
 * document, and the picture does not change — which reads as the tool being
 * broken rather than a missing file. The two lists must agree, and this is what
 * says so.
 */

import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { parseCubeLut } from './cube'
import { LUT_NAMES, isKnownLut, lutUrl } from './lut-catalogue'

const PUBLIC = join(process.cwd(), 'public')

/** The names `app/services/color_analysis.py` can return, kept in step by hand
 * — a generated contract would be better and is not worth a code generator for
 * five strings. If this list changes, that dictionary changed too. */
const SERVER_LOOKS = [
  'cinematic_warm',
  'vlog_clean',
  'cyberpunk',
  'sun_kissed',
  'mono_contrast',
]

describe('the LUT catalogue', () => {
  it('has a file for every look the server can recommend', () => {
    for (const name of SERVER_LOOKS) {
      expect(isKnownLut(name), `${name} is not in the client catalogue`).toBe(true)
      const path = join(PUBLIC, lutUrl(name))
      expect(existsSync(path), `${path} is missing — run: make luts`).toBe(true)
    }
  })

  it('ships exactly the five the scope doc asks for', () => {
    expect(LUT_NAMES).toHaveLength(5)
    expect([...LUT_NAMES].sort()).toEqual([...SERVER_LOOKS].sort())
  })

  it('every file parses into a usable table', () => {
    for (const name of LUT_NAMES) {
      const path = join(PUBLIC, lutUrl(name))
      if (!existsSync(path)) continue
      const lut = parseCubeLut(readFileSync(path, 'utf8'))
      expect(lut.size).toBeGreaterThanOrEqual(2)
      // The parser packs to RGBA bytes, ready for `texImage3D` without a copy.
      expect(lut.rgba.length).toBe(lut.size ** 3 * 4)
      // Alpha is opaque throughout: a LUT is a colour table, not a mask, and a
      // stray zero there would make the graded pixel transparent.
      for (let index = 3; index < lut.rgba.length; index += 4) {
        expect(lut.rgba[index]).toBe(255)
      }
    }
  })

  it('the looks are actually different from one another', () => {
    // Five names over one grade would recommend a difference nobody can see.
    const fingerprints = new Set<string>()
    for (const name of LUT_NAMES) {
      const path = join(PUBLIC, lutUrl(name))
      if (!existsSync(path)) continue
      const lut = parseCubeLut(readFileSync(path, 'utf8'))
      fingerprints.add([...lut.rgba.slice(0, 120)].join(','))
    }
    expect(fingerprints.size).toBe(LUT_NAMES.length)
  })

  it('an unknown name is refused rather than fetched', () => {
    expect(isKnownLut('not_a_look')).toBe(false)
  })
})

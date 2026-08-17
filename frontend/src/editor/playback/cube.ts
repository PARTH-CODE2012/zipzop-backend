/**
 * Adobe `.cube` 3D LUT parser.
 *
 * The same file is handed to FFmpeg's `lut3d` at export
 * (docs/04-frontend-architecture.md §4.4), so the two sides can only agree if
 * this reads it the way FFmpeg does. Two details carry that:
 *
 *  - **Red varies fastest**, then green, then blue. That is the Cube spec, and
 *    it happens to be exactly the memory layout `texImage3D` wants, with x as
 *    the fastest axis — so the file is uploaded without reordering.
 *  - The entry count must be exactly `size³`. A truncated file otherwise
 *    uploads as a LUT that is subtly wrong in the highlights only, which is
 *    close to undiagnosable by eye.
 *
 * Output is RGBA8 rather than RGB8: a 33-wide RGB row is 99 bytes and trips
 * `UNPACK_ALIGNMENT`, which shears the LUT along one axis. RGBA rows are
 * always 4-byte aligned and the failure cannot happen.
 */

export interface CubeLut {
  readonly title: string
  readonly size: number
  /** `size³ × 4` bytes, red-fastest, ready for `texImage3D`. */
  readonly rgba: Uint8Array
}

/** The spec allows 2–256; anything beyond 64 is a 1 MB+ texture nobody needs. */
const MIN_SIZE = 2
const MAX_SIZE = 64

export class CubeParseError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'CubeParseError'
  }
}

function toByte(v: number): number {
  const clamped = v < 0 ? 0 : v > 1 ? 1 : v
  return Math.round(clamped * 255)
}

/**
 * A LUT that changes nothing.
 *
 * The renderer always samples a 3D texture, so it needs one bound even when no
 * grade is applied. M2 ships no LUT catalogue — that is M4, which adds five of
 * them as shared assets — so the editor starts with this and every pixel comes
 * out exactly as it went in.
 *
 * Size 2 is the smallest the format allows and is exactly right here: an
 * identity mapping is linear, and trilinear interpolation across the eight
 * corners of a unit cube reproduces it perfectly. A larger table would be more
 * bytes for the same answer.
 */
export function identityLut(): CubeLut {
  const size = 2
  const rgba = new Uint8Array(size * size * size * 4)
  let at = 0
  // Red-fastest, matching the .cube ordering the parser produces.
  for (let b = 0; b < size; b += 1) {
    for (let g = 0; g < size; g += 1) {
      for (let r = 0; r < size; r += 1) {
        rgba[at++] = toByte(r / (size - 1))
        rgba[at++] = toByte(g / (size - 1))
        rgba[at++] = toByte(b / (size - 1))
        rgba[at++] = 255
      }
    }
  }
  return { title: 'identity', size, rgba }
}

export function parseCubeLut(text: string): CubeLut {
  let title = ''
  let size = 0
  const domainMin: number[] = [0, 0, 0]
  const domainMax: number[] = [1, 1, 1]

  let rgba: Uint8Array | null = null
  let written = 0

  const lines = text.split(/\r?\n/)

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i] ?? ''
    const hash = raw.indexOf('#')
    const line = (hash === -1 ? raw : raw.slice(0, hash)).trim()
    if (line === '') continue

    const upper = line.toUpperCase()

    if (upper.startsWith('TITLE')) {
      const quoted = /"([^"]*)"/.exec(line)
      title = quoted?.[1] ?? line.slice(5).trim()
      continue
    }

    if (upper.startsWith('LUT_1D_SIZE')) {
      throw new CubeParseError('this is a 1D LUT; the compositor needs a 3D one')
    }

    if (upper.startsWith('LUT_3D_SIZE')) {
      const n = Number(line.slice('LUT_3D_SIZE'.length).trim())
      if (!Number.isInteger(n) || n < MIN_SIZE || n > MAX_SIZE) {
        throw new CubeParseError(`LUT_3D_SIZE must be an integer ${MIN_SIZE}–${MAX_SIZE}, got "${n}"`)
      }
      size = n
      rgba = new Uint8Array(n * n * n * 4)
      continue
    }

    if (upper.startsWith('DOMAIN_MIN') || upper.startsWith('DOMAIN_MAX')) {
      const target = upper.startsWith('DOMAIN_MIN') ? domainMin : domainMax
      const parts = line.split(/\s+/).slice(1).map(Number)
      if (parts.length !== 3 || parts.some((v) => !Number.isFinite(v))) {
        throw new CubeParseError(`line ${i + 1}: malformed domain "${line}"`)
      }
      for (let k = 0; k < 3; k++) target[k] = parts[k] as number
      continue
    }

    // Anything left has to be a triplet of numbers.
    const parts = line.split(/\s+/)
    if (parts.length !== 3) {
      throw new CubeParseError(`line ${i + 1}: expected three numbers, got "${line}"`)
    }
    if (rgba === null) {
      throw new CubeParseError(`line ${i + 1}: table data before LUT_3D_SIZE`)
    }

    const r = Number(parts[0])
    const g = Number(parts[1])
    const b = Number(parts[2])
    if (!Number.isFinite(r) || !Number.isFinite(g) || !Number.isFinite(b)) {
      throw new CubeParseError(`line ${i + 1}: not a number triplet — "${line}"`)
    }

    const at = written * 4
    if (at + 3 >= rgba.length) {
      throw new CubeParseError(`more than ${size}³ entries — the file has extra table rows`)
    }
    rgba[at] = toByte(r)
    rgba[at + 1] = toByte(g)
    rgba[at + 2] = toByte(b)
    rgba[at + 3] = 255
    written++
  }

  if (size === 0 || rgba === null) throw new CubeParseError('no LUT_3D_SIZE in the file')

  const expected = size * size * size
  if (written !== expected) {
    throw new CubeParseError(`expected ${expected} entries for size ${size}, found ${written}`)
  }

  // A non-unit domain would need the sample coordinate rescaled before the
  // lookup. No catalogue LUT uses one, and silently ignoring it would apply
  // the grade to the wrong range — so refuse it out loud instead.
  const unit = domainMin.every((v) => v === 0) && domainMax.every((v) => v === 1)
  if (!unit) {
    throw new CubeParseError(
      `only the 0–1 domain is supported, got ${domainMin.join(',')} → ${domainMax.join(',')}`,
    )
  }

  return { title, size, rgba }
}

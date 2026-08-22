/**
 * The looks, and where their `.cube` files live.
 *
 * **One file, two renderers.** The browser uploads these into a WebGL
 * `TEXTURE_3D` for the preview and the export renderer hands the same file to
 * FFmpeg's `lut3d`. There is nothing for the two to disagree about, which is
 * the only way a preview and an export can be trusted to match
 * (docs/04-frontend-architecture.md §4.4, contract §4.4).
 *
 * The names are the ones `POST /jobs` returns from `color_analysis`, so a
 * recommendation maps to a file by lookup and not by convention. ⚠️ A name in
 * the server's catalogue with no entry here is a grade the browser silently
 * cannot draw — `assertCatalogueMatches` in the tests is what stops that being
 * discovered by a user.
 */

import { parseCubeLut, type CubeLut } from '@/editor/playback/cube'

export const LUT_NAMES = [
  'cinematic_warm',
  'vlog_clean',
  'cyberpunk',
  'sun_kissed',
  'mono_contrast',
] as const

export type LutName = (typeof LUT_NAMES)[number]

export function lutUrl(name: string): string {
  return `/luts/${name}.cube`
}

export function isKnownLut(name: string): name is LutName {
  return (LUT_NAMES as readonly string[]).includes(name)
}

/**
 * Fetched once per name and kept.
 *
 * A 17³ table is 133 kB of text that parses into the same 4,913 triples every
 * time. Re-fetching it each time the playhead crosses a graded clip would put a
 * network round trip inside the frame loop.
 */
const cache = new Map<string, Promise<CubeLut | null>>()

export function loadLut(name: string): Promise<CubeLut | null> {
  const existing = cache.get(name)
  if (existing) return existing

  const loading = fetch(lutUrl(name))
    .then(async (response) => (response.ok ? parseCubeLut(await response.text()) : null))
    .catch(() => null)

  cache.set(name, loading)
  return loading
}

/** For tests, so one file's cache cannot leak into another's. */
export function clearLutCache(): void {
  cache.clear()
}

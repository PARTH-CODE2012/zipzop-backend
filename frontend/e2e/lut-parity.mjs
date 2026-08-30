/**
 * M5's closing condition, measured.
 *
 *   "You export a 1080p 9:16 MP4 and it looks exactly like the preview."
 *
 * That is a visual claim. Until something measures it, it is a hope — and the
 * way it fails is not a crash, it is an export that is slightly the wrong
 * colour, which nobody notices until a user says their video looks different
 * from what they edited.
 *
 * This puts the same colours through **both real implementations** and reports
 * the difference:
 *
 *   preview  the exported `FRAGMENT_SRC` and `parseCubeLut`, on a real GPU
 *            context in a real browser (`/spike/lut-parity`)
 *   export   `lut3d` + `blend` exactly as `render_graph.py` builds them
 *
 * ## What it is measuring, and what it deliberately is not
 *
 * Only the grade. The preview composites a **480p proxy** and the export uses
 * the original, and H.264 is lossy on both sides, so a whole-frame comparison
 * of a photograph measures mostly compression and scaling — noise that would
 * either swamp a real drift or force a tolerance so loose it catches nothing.
 * The one thing the two implement *separately* is the LUT, its interpolation
 * and the mix at `strength`, so that is what is compared, on flat colours where
 * one level of drift is visible.
 *
 * What it therefore catches is precisely what `docs/15-m5-readiness.md` §4.1
 * asks for: a **systematic** difference — a LUT applied at the wrong strength,
 * in the wrong colour space, or not at all.
 *
 * ## The tolerance
 *
 * Mean absolute error per channel, over 8-bit values, across the swatches.
 *
 * `MAX_MEAN_ABS_ERROR` is not a number picked to make today pass. The two paths
 * cannot agree exactly and it is worth knowing why: the browser interpolates
 * the 3D texture in hardware at whatever precision the driver uses, FFmpeg
 * interpolates in software; the browser reads back 8-bit, and FFmpeg's chain
 * quantises to 8-bit at each conversion. A drift of a level or two is those
 * two facts. A LUT at the wrong strength moves colours by tens of levels, and
 * one not applied at all by more.
 *
 *   node e2e/lut-parity.mjs [--lut cyberpunk] [--strength 1]
 */

import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { launch, openPage, waitFor } from './cdp.mjs'

/** Levels, on 0-255. See "The tolerance" above. */
const MAX_MEAN_ABS_ERROR = 3.0
/** No single swatch may be wildly out even if the mean is fine — that would be
 *  a LUT right in the greys and wrong in one corner, which is what a
 *  colour-space mistake looks like. */
const MAX_SINGLE_CHANNEL_ERROR = 8

const args = process.argv.slice(2)
const option = (name, fallback) => {
  const at = args.indexOf(`--${name}`)
  return at === -1 ? fallback : args[at + 1]
}
const LUT = option('lut', 'cyberpunk')
const STRENGTH = Number(option('strength', '1'))
const WEB = process.env.WEB_ORIGIN ?? `http://localhost:${process.env.WEB_PORT ?? 3123}`
const FFMPEG = process.env.FFMPEG ?? 'ffmpeg'
// No LUT path here on purpose: which file a look resolves to is the renderer's
// business, and asking it removes the last thing this script could disagree
// with the renderer about.

/**
 * The export side's grade, **from the export renderer itself**.
 *
 * Not rebuilt here. A copy of the filter string would mean this compares the
 * browser against a copy of the renderer rather than against the renderer, and
 * the copy is the one that would be kept correct. That is not hypothetical:
 * this file did carry its own copy for an hour, and in that hour it reported
 * the blend-order bug as still present *after* the fix had landed, because the
 * copy still had it.
 */
function gradeFilter(lut, strength) {
  const python = process.env.BACKEND_PYTHON ?? join('..', 'backend', PY_BIN)
  const result = spawnSync(
    python,
    ['-m', 'app.scripts.grade_filter', '--lut', lut, '--strength', String(strength)],
    { cwd: join('..', 'backend'), encoding: 'utf8' },
  )
  if (result.status !== 0) {
    throw new Error(`could not read the grade filter: ${result.stderr ?? ''}`)
  }
  return result.stdout.trim()
}

/** `bin` on POSIX, `Scripts` on Windows — the same split the Makefile resolves. */
const PY_BIN = existsSync(join('..', 'backend', '.venv', 'Scripts', 'python.exe'))
  ? join('.venv', 'Scripts', 'python.exe')
  : join('.venv', 'bin', 'python')

/** One swatch through the export renderer's grade. */
function exportGrade([r, g, b], grade, work) {
  const hex = [r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('')
  const out = join(work, `x${hex}.png`)
  const result = spawnSync(
    FFMPEG,
    ['-hide_banner', '-v', 'error', '-y', '-f', 'lavfi', '-i',
     `color=c=#${hex}:s=8x8:d=0.1`, '-filter_complex', grade, '-frames:v', '1', out],
    { encoding: 'buffer' },
  )
  if (result.status !== 0) {
    throw new Error(`ffmpeg failed on #${hex}: ${result.stderr?.toString().slice(-500)}`)
  }
  // Read the pixel back through ffmpeg rather than decoding a PNG here: a
  // decoder of our own is a third implementation to be wrong.
  const raw = spawnSync(
    FFMPEG,
    ['-hide_banner', '-v', 'error', '-i', out, '-vf', 'crop=1:1:4:4', '-f', 'rawvideo',
     '-pix_fmt', 'rgb24', '-'],
    { encoding: 'buffer', maxBuffer: 1024 },
  )
  if (raw.status !== 0) throw new Error(`could not read #${hex} back`)
  return [raw.stdout[0], raw.stdout[1], raw.stdout[2]]
}

async function previewGrade(port) {
  const url = `${WEB}/spike/lut-parity?lut=${encodeURIComponent(LUT)}&strength=${STRENGTH}`
  const page = await openPage(port, url)
  await waitFor(
    async () => (await page.evaluate('!!window.__lutParity')) === true,
    { what: 'the parity harness to run', timeoutMs: 30_000 },
  )
  const result = await page.evaluate('JSON.stringify(window.__lutParity)')
  page.close()
  const parsed = JSON.parse(result)
  if (parsed.error) throw new Error(`the browser harness failed: ${parsed.error}`)
  return parsed
}

const work = mkdtempSync(join(tmpdir(), 'zipzop-parity-'))
let browser = null
try {
  // Headless Chrome has no GPU, and WebGL2 without one needs the software
  // rasteriser turned on explicitly. The grade is arithmetic either way — what
  // is being measured is the shader's result, not the driver's speed.
  // A different debugging port per run. `make parity` runs this fifteen times
  // in a row and a fixed port collides with the previous Chrome's socket
  // before the OS has released it — which shows up as a handful of runs
  // failing at random, the worst possible shape for a correctness check.
  const port = 9400 + Math.floor(Math.random() * 500)
  browser = await launch({
    port,
    extraArgs: ['--enable-unsafe-swiftshader', '--use-gl=swiftshader'],
  })
  const preview = await previewGrade(port)
  const grade = gradeFilter(LUT, STRENGTH)

  console.log(`LUT ${LUT} at strength ${STRENGTH}`)
  console.log('  swatch        preview        export        Δ per channel')

  let total = 0
  let count = 0
  let worst = 0
  const failures = []

  for (let i = 0; i < preview.swatches.length; i += 1) {
    const swatch = preview.swatches[i]
    const shown = preview.graded[i]
    const written = exportGrade(swatch, grade, work)
    const deltas = [0, 1, 2].map((c) => Math.abs(shown[c] - written[c]))
    total += deltas.reduce((a, b) => a + b, 0)
    count += 3
    worst = Math.max(worst, ...deltas)
    if (Math.max(...deltas) > MAX_SINGLE_CHANNEL_ERROR) {
      failures.push(`#${swatch.join(',')}: preview ${shown} vs export ${written}`)
    }
    console.log(
      `  ${String(swatch).padEnd(13)} ${String(shown).padEnd(14)} ${String(written).padEnd(13)} ${deltas}`,
    )
  }

  const mae = total / count
  console.log(`\n  mean absolute error ${mae.toFixed(2)} levels (ceiling ${MAX_MEAN_ABS_ERROR})`)
  console.log(`  worst single channel ${worst} levels (ceiling ${MAX_SINGLE_CHANNEL_ERROR})`)

  if (mae > MAX_MEAN_ABS_ERROR || failures.length > 0) {
    console.error('\nThe preview and the export do not agree:')
    for (const line of failures) console.error(`  ${line}`)
    console.error(
      '\nThis is the milestone\'s closing condition failing. Look for the LUT applied at\n' +
        'the wrong strength, in the wrong colour space, or not at all.',
    )
    process.exitCode = 1
  } else {
    console.log('\npreview and export agree within tolerance')
  }
} finally {
  if (browser) await browser.close()
  rmSync(work, { recursive: true, force: true })
}

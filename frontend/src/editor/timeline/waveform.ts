/**
 * Waveforms, drawn into a canvas.
 *
 * PHASE1-TASKS.md, M2: *"Waveform drawn to `<canvas>` from peaks — **never one
 * DOM node per peak**."* The reason is arithmetic: peaks arrive at 100 a
 * second, so a ten-minute clip is 60 000 of them. Sixty thousand `<div>`s is
 * a layout pass measured in seconds and a scroll that never recovers. One
 * canvas is one element whatever the length.
 *
 * The column reduction is split out from the drawing so it can be tested
 * without a canvas — and because it is the part that can be wrong in a way
 * nobody sees: a waveform that averages instead of taking the peak still looks
 * like a waveform.
 */

export interface Peaks {
  /** 0 to 1, one per bucket. */
  peaks: number[]
  bucketsPerSecond: number
}

/**
 * Reduce the peaks covering a time window down to one value per pixel column.
 *
 * More buckets than pixels is the normal case — at 40 px/s each pixel covers
 * 2.5 buckets — so most columns reduce several. **Maximum, not mean**: the
 * loud moments are what a waveform is read for, and averaging is what turns a
 * drum hit into a bump.
 *
 * Columns with no data at all read 0, which draws as silence rather than as a
 * gap.
 */
export function columnsForWindow(
  source: Peaks,
  fromMs: number,
  toMs: number,
  widthPx: number,
): Float32Array {
  const columns = new Float32Array(Math.max(0, Math.floor(widthPx)))
  if (columns.length === 0 || toMs <= fromMs || source.peaks.length === 0) return columns

  const msPerBucket = 1000 / source.bucketsPerSecond
  const spanMs = toMs - fromMs

  for (let x = 0; x < columns.length; x += 1) {
    const columnFromMs = fromMs + (x / columns.length) * spanMs
    const columnToMs = fromMs + ((x + 1) / columns.length) * spanMs

    let first = Math.floor(columnFromMs / msPerBucket)
    const last = Math.ceil(columnToMs / msPerBucket)

    // Zoomed in far enough that a column covers less than one bucket, the
    // floor and ceil can land on the same index. Read at least one, or every
    // other column would be empty and the waveform would comb.
    if (last <= first) first = Math.max(0, last - 1)

    let peak = 0
    for (let i = Math.max(0, first); i < Math.min(last, source.peaks.length); i += 1) {
      const value = source.peaks[i] ?? 0
      if (value > peak) peak = value
    }
    columns[x] = peak
  }
  return columns
}

export interface WaveformStyle {
  /** Read from a CSS variable by the caller, never hard-coded here. */
  color: string
}

/**
 * Paint the columns, mirrored about the vertical centre.
 *
 * Drawn as one filled path rather than a stroke per column: a stroke per
 * column is 60 000 path operations and antialiases into mush, while a single
 * path is one fill.
 */
export function drawWaveform(
  context: CanvasRenderingContext2D,
  columns: Float32Array,
  width: number,
  height: number,
  style: WaveformStyle,
): void {
  context.clearRect(0, 0, width, height)
  if (columns.length === 0 || height <= 0) return

  const middle = height / 2
  context.beginPath()
  context.moveTo(0, middle)

  // Out along the top…
  for (let x = 0; x < columns.length; x += 1) {
    context.lineTo(x, middle - (columns[x] ?? 0) * middle)
  }
  // …and back along the bottom, closing one shape.
  for (let x = columns.length - 1; x >= 0; x -= 1) {
    context.lineTo(x, middle + (columns[x] ?? 0) * middle)
  }

  context.closePath()
  context.fillStyle = style.color
  context.fill()
}

/**
 * Size a canvas for the display, in device pixels.
 *
 * Without this the waveform is soft on every retina screen: the canvas would
 * hold CSS pixels and be scaled up by the compositor. Returns the ratio that
 * was applied so the caller can draw in CSS pixel coordinates.
 */
export function sizeCanvasForDisplay(
  canvas: HTMLCanvasElement,
  cssWidth: number,
  cssHeight: number,
  devicePixelRatio = 1,
): number {
  const ratio = Math.max(1, devicePixelRatio)
  const width = Math.max(1, Math.round(cssWidth * ratio))
  const height = Math.max(1, Math.round(cssHeight * ratio))

  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width
    canvas.height = height
  }
  canvas.style.width = `${cssWidth}px`
  canvas.style.height = `${cssHeight}px`
  return ratio
}

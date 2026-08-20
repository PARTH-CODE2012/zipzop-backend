/**
 * The spike's timeline document, and the time arithmetic around it.
 *
 * Deliberately a *subset* of the real document (docs/05-api-contract.md §4),
 * with the same field names and the same units — integer milliseconds
 * everywhere, normalised 0–1 for anything spatial. The spike hardcodes it
 * instead of fetching it, but it must not invent a different shape: the point
 * of M1 is to learn whether a browser can play this document back, and that
 * answer is worthless if the document is not the one M2 will produce.
 *
 * Everything in this file is pure. It is the part of the compositor that can
 * be tested without a GPU, and the conversion in `sourceTimeMs` is the one
 * that puts captions subtly out of sync when it is wrong.
 */

export interface SpikeColourGrade {
  readonly type: 'color_grade'
  readonly lut: string
  /** 0–1. Multiplied by the global slider before it reaches the shader. */
  readonly strength: number
}

export interface SpikeMediaClip {
  readonly id: string
  /** Stands in for `assetId` + the proxy URL the API would hand back. */
  readonly src: string
  readonly label: string
  /** Length of the underlying file, so invariant 4 can be asserted. */
  readonly assetDurationMs: number
  readonly startMs: number
  readonly durationMs: number
  readonly sourceInMs: number
  readonly speed: number
  readonly effects: readonly SpikeColourGrade[]
}

export interface SpikeTextClip {
  readonly id: string
  readonly startMs: number
  readonly durationMs: number
  readonly text: string
  /** 0–1, from vocal emphasis. Here it just drives the size. */
  readonly emphasis: number
  /** Normalised 0–1 relative to the canvas, never pixels. */
  readonly position: { readonly x: number; readonly y: number }
  /** Cap height as a fraction of canvas height. */
  readonly fontSize: number
  /** Which edge `position.x` names. Absent means centred, which is what every
   * caption the spike drew used. */
  readonly anchor?: 'center' | 'left' | 'right'
}

export type TransitionMode = 'cut' | 'crossfade'

export interface SpikeTimeline {
  readonly mode: TransitionMode
  readonly transitionMs: number
  readonly durationMs: number
  readonly video: readonly SpikeMediaClip[]
  readonly text: readonly SpikeTextClip[]
}

export const CLIP_A_SRC = '/spike/media/clip-a.mp4'
export const CLIP_B_SRC = '/spike/media/clip-b.mp4'
export const LUT_URL = '/spike/luts/cinematic_warm.cube'

/** Both generated files are 8 s (scripts/make-spike-media.sh). */
const ASSET_MS = 8000
const CLIP_MS = 6000
const CROSSFADE_MS = 900

export function clamp01(x: number): number {
  return x < 0 ? 0 : x > 1 ? 1 : x
}

/**
 * Where inside the asset we are, given a position on the timeline.
 *
 * The inverse of `timelineTimeMs`. Both directions matter: playback reads this
 * one to drive the video element, and the clock reads the other one to turn a
 * `currentTime` back into a playhead position.
 */
export function sourceTimeMs(clip: SpikeMediaClip, timelineMs: number): number {
  return clip.sourceInMs + (timelineMs - clip.startMs) * clip.speed
}

export function timelineTimeMs(clip: SpikeMediaClip, sourceMs: number): number {
  return clip.startMs + (sourceMs - clip.sourceInMs) / clip.speed
}

export function isActive(clip: { startMs: number; durationMs: number }, timeMs: number): boolean {
  return timeMs >= clip.startMs && timeMs < clip.startMs + clip.durationMs
}

export function clipEndMs(clip: { startMs: number; durationMs: number }): number {
  return clip.startMs + clip.durationMs
}

export interface FrameLayers {
  /** The clip being shown, or the one being faded *from* during a transition. */
  readonly base: SpikeMediaClip | null
  /** The clip being faded *to*, or null outside a transition. */
  readonly over: SpikeMediaClip | null
  /** 0 = base only, 1 = over only. */
  readonly mix: number
}

/**
 * What the compositor has to draw at `timeMs`.
 *
 * Two clips are active at once exactly when they overlap, which in this
 * document only happens across a crossfade. Invariant 7 (a transition is at
 * most half the shorter clip) is what keeps that number at two rather than
 * three, so the two-texture shader is enough.
 */
export function resolveFrame(
  clips: readonly SpikeMediaClip[],
  timeMs: number,
): FrameLayers {
  const active = clips.filter((c) => isActive(c, timeMs)).sort((a, b) => a.startMs - b.startMs)

  const base = active[0] ?? null
  const over = active[1] ?? null
  if (base === null || over === null) return { base, over: null, mix: 0 }

  const overlapStart = over.startMs
  const overlapEnd = clipEndMs(base)
  const span = overlapEnd - overlapStart
  const mix = span <= 0 ? 1 : clamp01((timeMs - overlapStart) / span)
  return { base, over, mix }
}

/**
 * Audio gain for a clip during a crossfade.
 *
 * Equal-power rather than linear: two linearly-faded correlated signals dip in
 * loudness at the midpoint, which is audible. The real audio graph (M3) does
 * this with scheduled gain automation on the audio context clock; here it is
 * good enough to set `video.volume` per frame.
 */
export function crossfadeGain(mix: number, layer: 'base' | 'over'): number {
  const m = clamp01(mix)
  return layer === 'base' ? Math.cos((m * Math.PI) / 2) : Math.sin((m * Math.PI) / 2)
}

const CAPTIONS: readonly Omit<SpikeTextClip, 'position' | 'fontSize'>[] = [
  { id: 'clp_cap_01', startMs: 800, durationMs: 400, text: 'Two', emphasis: 0.2 },
  { id: 'clp_cap_02', startMs: 1250, durationMs: 500, text: 'clips,', emphasis: 0.35 },
  { id: 'clp_cap_03', startMs: 1850, durationMs: 350, text: 'one', emphasis: 0.15 },
  { id: 'clp_cap_04', startMs: 2250, durationMs: 400, text: 'cut,', emphasis: 0.8 },
  { id: 'clp_cap_05', startMs: 3000, durationMs: 350, text: 'no', emphasis: 0.25 },
  { id: 'clp_cap_06', startMs: 3400, durationMs: 500, text: 'black', emphasis: 0.6 },
  { id: 'clp_cap_07', startMs: 3950, durationMs: 700, text: 'frame.', emphasis: 0.95 },
  { id: 'clp_cap_08', startMs: 6600, durationMs: 450, text: 'Same', emphasis: 0.2 },
  { id: 'clp_cap_09', startMs: 7100, durationMs: 550, text: 'clock,', emphasis: 0.4 },
  { id: 'clp_cap_10', startMs: 7750, durationMs: 450, text: 'same', emphasis: 0.2 },
  { id: 'clp_cap_11', startMs: 8250, durationMs: 500, text: 'grade,', emphasis: 0.5 },
  { id: 'clp_cap_12', startMs: 9000, durationMs: 700, text: 'different', emphasis: 0.7 },
  { id: 'clp_cap_13', startMs: 9750, durationMs: 800, text: 'file.', emphasis: 0.95 },
]

/**
 * Two clips of the same length, butted together or overlapped by a crossfade.
 *
 * `sourceInMs` is non-zero on both on purpose: a clip that starts at the top of
 * its file hides every off-by-one in the asset-time conversion.
 */
export function buildSpikeTimeline(mode: TransitionMode): SpikeTimeline {
  const overlap = mode === 'crossfade' ? CROSSFADE_MS : 0

  const a: SpikeMediaClip = {
    id: 'clp_a',
    src: CLIP_A_SRC,
    label: 'A',
    assetDurationMs: ASSET_MS,
    startMs: 0,
    durationMs: CLIP_MS,
    sourceInMs: 500,
    speed: 1,
    effects: [{ type: 'color_grade', lut: 'cinematic_warm', strength: 1 }],
  }

  const b: SpikeMediaClip = {
    id: 'clp_b',
    src: CLIP_B_SRC,
    label: 'B',
    assetDurationMs: ASSET_MS,
    startMs: CLIP_MS - overlap,
    durationMs: CLIP_MS,
    sourceInMs: 300,
    speed: 1,
    effects: [{ type: 'color_grade', lut: 'cinematic_warm', strength: 1 }],
  }

  return {
    mode,
    transitionMs: overlap,
    durationMs: clipEndMs(b),
    video: [a, b],
    text: CAPTIONS.map((c) => ({ ...c, position: { x: 0.5, y: 0.78 }, fontSize: 0.062 })),
  }
}

/**
 * The compositor spike's engine: clock, loop, element scheduling, draw.
 *
 * This is the part M1 exists to answer — can a browser play a timeline back at
 * all. Everything it does follows three rules from
 * docs/04-frontend-architecture.md §4, and each of them is a bug that would
 * otherwise be found much later:
 *
 *  1. **The playing video element is the clock**, never `performance.now()`.
 *     A wall clock drifts against decoded audio and the drift is audible
 *     within a minute. `performance.now()` is only the bridge across gaps,
 *     across a stall, and while paused.
 *  2. **`requestVideoFrameCallback` drives the loop** while a video is
 *     playing, because it fires once per *decoded frame* with a precise media
 *     timestamp. `requestAnimationFrame` fires per *display refresh* and will
 *     happily hand you the same frame twice. rAF is the fallback.
 *  3. **The next clip is primed before it is needed** — seeked, decoded and
 *     holding its first frame. That is what makes a cut not flash black.
 *
 * The loop runs whenever the engine is mounted, not only during playback, so a
 * paused frame still redraws after a seek or a resolution change. It never
 * calls `setState`: stats are pushed out on a timer, and React renders at its
 * own rate (§9).
 */

import type { CubeLut } from './cube'
import { CompositorRenderer } from './renderer'
import { TextOverlay } from './text-overlay'
import {
  buildSpikeTimeline,
  clamp01,
  crossfadeGain,
  isActive,
  resolveFrame,
  sourceTimeMs,
  timelineTimeMs,
  type FrameLayers,
  type SpikeMediaClip,
  type SpikeTimeline,
  type TransitionMode,
} from './timeline'
import { PooledVideo, supportsRvfc } from './video-pool'

/** How early the next clip is loaded, seeked and decoded. */
const PRELOAD_MS = 2_000
/** Close enough that starting playback needs no seek — about 1.5 frames at 30 fps. */
const START_EPS_S = 0.05
/** A following element this far from where it should be is pulled back. */
const RESYNC_S = 0.15
/** A media clock that has not moved for this long is treated as stalled. */
const CLOCK_STALL_MS = 150
/** No frame for this long means the driver is not going to fire — fall back. */
const STALL_MS = 400
const WATCHDOG_INTERVAL_MS = 250
const STATS_INTERVAL_MS = 125

export type ClockSource = 'video' | 'wall' | 'hold'
export type LoopDriver = 'rvfc' | 'raf'

export interface CompositorStats {
  readonly positionMs: number
  readonly durationMs: number
  readonly playing: boolean
  readonly mode: TransitionMode
  readonly loopFps: number
  readonly frameCostMs: number
  readonly driver: LoopDriver
  readonly clock: ClockSource
  readonly layers: string
  readonly mix: number
  readonly canvasWidth: number
  readonly canvasHeight: number
  readonly droppedFrames: number
  readonly decodedFrames: number
  readonly textRedraws: number
  /** Frames left on screen untouched because a layer had nothing to show yet. */
  readonly skippedDraws: number
  readonly primed: readonly { readonly label: string; readonly primed: boolean }[]
  readonly mediaError: string | null
  readonly playError: string | null
  readonly contextLost: boolean
  readonly gpu: string
}

export interface CompositorEngineOptions {
  readonly glCanvas: HTMLCanvasElement
  readonly textCanvas: HTMLCanvasElement
  readonly videoHost: HTMLElement
  readonly lut: CubeLut
  readonly onStats: (stats: CompositorStats) => void
  /**
   * The timeline to start with. Omitted, the engine builds the M1 spike's
   * two-clip arrangement, which is what `/spike/compositor` still relies on.
   * The editor passes a real one built by `timeline-adapter.ts`.
   */
  readonly timeline?: SpikeTimeline
}

export class CompositorEngine {
  private readonly renderer: CompositorRenderer
  private readonly overlay: TextOverlay
  private readonly videos = new Map<string, PooledVideo>()
  private readonly onStats: (stats: CompositorStats) => void
  /** Held so `setTimeline` can attach elements for clips that arrive later. */
  private readonly videoHost: HTMLElement

  private timeline: SpikeTimeline
  private positionMs = 0
  private playing = false
  private loop = true
  private muted = true
  private lutStrength = 1
  private forceRaf = false

  private width = 1920
  private height = 1080

  private disposed = false
  private frameGen = 0
  private rafHandle = 0
  private rvfcHandle = 0
  private rvfcOwner: HTMLVideoElement | null = null
  private rvfcSuspendedUntil = 0
  private watchdog = 0

  private wallAnchorMs = 0
  private wallAnchorAt = 0
  private videoClock = { clipId: '', sec: -1, since: 0 }

  private clockClipId: string | null = null
  private clockSource: ClockSource = 'wall'
  private driver: LoopDriver = 'raf'

  private lastFrameAt = 0
  private skippedDraws = 0
  private dtEma = 0
  private costEma = 0
  private lastStatsAt = 0

  constructor(options: CompositorEngineOptions) {
    this.onStats = options.onStats
    this.videoHost = options.videoHost
    this.timeline = options.timeline ?? buildSpikeTimeline('cut')

    this.renderer = new CompositorRenderer(options.glCanvas)
    this.renderer.setLut(options.lut)
    this.overlay = new TextOverlay(options.textCanvas)

    for (const clip of this.timeline.video) {
      this.videos.set(clip.id, new PooledVideo(clip.id, clip.src, options.videoHost))
    }
    this.setMuted(true)
    this.setResolution(this.width, this.height)

    this.lastFrameAt = performance.now()
    this.wallAnchorAt = this.lastFrameAt

    this.watchdog = window.setInterval(() => this.checkForStall(), WATCHDOG_INTERVAL_MS)
    this.schedule()
  }

  // -------------------------------------------------------------------- controls

  get isPlaying(): boolean {
    return this.playing
  }

  /** The live playhead. Stats lag by up to one interval; this never does. */
  playheadMs(): number {
    return this.positionMs
  }

  get durationMs(): number {
    return this.timeline.durationMs
  }

  get mode(): TransitionMode {
    return this.timeline.mode
  }

  play(): void {
    if (this.disposed || this.playing) return
    if (this.positionMs >= this.timeline.durationMs) this.applySeek(0)
    for (const video of this.videos.values()) video.cancelPrime()
    this.playing = true
    this.resetClockAnchors()
    this.restartLoop()
  }

  pause(): void {
    if (this.disposed || !this.playing) return
    this.playing = false
    for (const video of this.videos.values()) video.pause()
    this.resetClockAnchors()
    this.restartLoop()
  }

  togglePlay(): void {
    if (this.playing) this.pause()
    else this.play()
  }

  seek(ms: number): void {
    if (this.disposed) return
    this.applySeek(ms)
  }

  setMode(mode: TransitionMode): void {
    if (this.disposed || mode === this.timeline.mode) return
    const wasPlaying = this.playing
    this.pause()
    // Both modes use the same two sources and the same clip ids, so the video
    // elements and their decoded state carry over — only the geometry changes.
    this.timeline = buildSpikeTimeline(mode)
    this.applySeek(Math.min(this.positionMs, this.timeline.durationMs))
    if (wasPlaying) this.play()
  }

  /**
   * Replace the whole timeline — a real project's, not the spike's.
   *
   * `setMode` above can keep its video elements because both of its modes use
   * the same two sources. This cannot: the clips are different assets at
   * different URLs, so the pool is rebuilt. Elements whose clip id **and**
   * source both survive are kept, which is what stops a re-render that changes
   * one clip from tearing down and re-decoding every other one.
   *
   * The `videoHost` is held from construction because a new `PooledVideo`
   * needs somewhere to attach.
   */
  setTimeline(next: SpikeTimeline): void {
    if (this.disposed) return
    const wasPlaying = this.playing
    this.pause()

    const wanted = new Map(next.video.map((clip) => [clip.id, clip.src]))
    for (const [id, video] of [...this.videos]) {
      if (wanted.get(id) !== video.src) {
        video.dispose()
        this.videos.delete(id)
      }
    }
    for (const clip of next.video) {
      if (!this.videos.has(clip.id)) {
        this.videos.set(clip.id, new PooledVideo(clip.id, clip.src, this.videoHost))
      }
    }
    this.setMuted(this.muted)

    this.timeline = next
    this.applySeek(Math.min(this.positionMs, this.timeline.durationMs))
    if (wasPlaying && next.video.length > 0) this.play()
  }

  setResolution(width: number, height: number): void {
    if (this.disposed) return
    this.width = width
    this.height = height
    this.renderer.resize(width, height)
    this.overlay.resize(width, height)
  }

  setLutStrength(strength: number): void {
    this.lutStrength = clamp01(strength)
  }

  setLoop(loop: boolean): void {
    this.loop = loop
  }

  setMuted(muted: boolean): void {
    this.muted = muted
    for (const video of this.videos.values()) video.setMuted(muted)
  }

  setForceRaf(force: boolean): void {
    this.forceRaf = force
    this.restartLoop()
  }

  simulateContextLoss(): boolean {
    return this.renderer.simulateContextLoss()
  }

  dispose(): void {
    if (this.disposed) return
    this.disposed = true
    if (this.watchdog !== 0) window.clearInterval(this.watchdog)
    this.watchdog = 0
    this.cancelPending()
    for (const video of this.videos.values()) video.dispose()
    this.videos.clear()
    this.overlay.dispose()
    this.renderer.dispose()
  }

  // ------------------------------------------------------------------- the loop

  private restartLoop(): void {
    this.cancelPending()
    this.schedule()
  }

  private cancelPending(): void {
    this.frameGen++
    if (this.rafHandle !== 0) {
      window.cancelAnimationFrame(this.rafHandle)
      this.rafHandle = 0
    }
    const owner = this.rvfcOwner
    if (this.rvfcHandle !== 0 && owner !== null && supportsRvfc(owner)) {
      owner.cancelVideoFrameCallback(this.rvfcHandle)
    }
    this.rvfcHandle = 0
    this.rvfcOwner = null
  }

  private schedule(): void {
    if (this.disposed) return
    const gen = ++this.frameGen

    const clip = this.playing ? this.clockClipAt(this.positionMs) : null
    const el = clip === null ? undefined : this.videos.get(clip.id)?.el

    if (
      clip !== null &&
      el !== undefined &&
      !this.forceRaf &&
      !el.paused &&
      performance.now() >= this.rvfcSuspendedUntil &&
      supportsRvfc(el)
    ) {
      const clipId = clip.id
      this.rvfcOwner = el
      this.rvfcHandle = el.requestVideoFrameCallback((_now, metadata) => {
        if (gen !== this.frameGen || this.disposed) return
        this.rvfcHandle = 0
        this.rvfcOwner = null
        this.driver = 'rvfc'
        this.frame(metadata.mediaTime, clipId)
      })
      return
    }

    this.rvfcOwner = null
    this.rafHandle = window.requestAnimationFrame(() => {
      if (gen !== this.frameGen || this.disposed) return
      this.rafHandle = 0
      this.driver = 'raf'
      this.frame(null, null)
    })
  }

  /**
   * A `requestVideoFrameCallback` that never fires leaves the loop dead, and
   * with it the HUD, the overlay and every control that reads position. It is
   * not hypothetical: a buffering stall or a decoder hiccup does exactly that.
   */
  private checkForStall(): void {
    if (this.disposed || document.hidden) return
    if (performance.now() - this.lastFrameAt < STALL_MS) return
    this.rvfcSuspendedUntil = performance.now() + 1_000
    this.restartLoop()
  }

  private frame(mediaTime: number | null, mediaClipId: string | null): void {
    const now = performance.now()
    const dt = now - this.lastFrameAt
    this.lastFrameAt = now
    if (dt > 0 && dt < 1_000) this.dtEma = this.dtEma === 0 ? dt : this.dtEma * 0.9 + dt * 0.1

    this.advanceClock(now, mediaTime, mediaClipId)

    const layers = resolveFrame(this.timeline.video, this.positionMs)
    this.syncElements(layers)
    this.drawFrame(layers)

    const cost = performance.now() - now
    this.costEma = this.costEma === 0 ? cost : this.costEma * 0.9 + cost * 0.1

    if (now - this.lastStatsAt >= STATS_INTERVAL_MS) {
      this.lastStatsAt = now
      this.emitStats(layers)
    }

    this.schedule()
  }

  // --------------------------------------------------------------------- clock

  /**
   * The *earliest* active clip owns the clock.
   *
   * During a crossfade that means A keeps it until it ends, and B — which has
   * been playing throughout the overlap — takes over at exactly the position A
   * left off, so the handover has no discontinuity to correct.
   */
  private clockClipAt(timeMs: number): SpikeMediaClip | null {
    for (const clip of this.timeline.video) {
      if (!isActive(clip, timeMs)) continue
      const video = this.videos.get(clip.id)
      if (video === undefined || video.error !== null) continue
      if (video.seeking || video.el.readyState < 2) continue
      return clip
    }
    return null
  }

  private advanceClock(now: number, mediaTime: number | null, mediaClipId: string | null): void {
    const clip = this.clockClipAt(this.positionMs)
    this.clockClipId = clip?.id ?? null

    if (this.playing) {
      const sec = clip === null ? null : this.readMediaClock(clip, now, mediaTime, mediaClipId)
      if (clip !== null && sec !== null) {
        this.positionMs = timelineTimeMs(clip, sec * 1000)
        this.clockSource = 'video'
      } else if (this.waitingOnMedia(this.positionMs)) {
        // There is media under the playhead and it is not presenting frames
        // yet — still seeking to its in-point, or buffering. Hold.
        //
        // Sliding forward on the wall clock here is what makes a clip ignore
        // its own `sourceInMs`: press play before the first seek lands and the
        // playhead runs off while the element is still at the top of the file,
        // so the first half second of the wrong footage plays. Holding costs a
        // few frames of latency and is always correct.
        this.clockSource = 'hold'
      } else {
        this.positionMs = this.wallAnchorMs + (now - this.wallAnchorAt)
        this.clockSource = 'wall'
      }
    } else {
      this.clockSource = 'hold'
    }

    // The wall clock is re-anchored every frame, so whenever it does take over
    // it continues from where the media clock was rather than from zero.
    this.wallAnchorMs = this.positionMs
    this.wallAnchorAt = now

    if (this.playing && this.positionMs >= this.timeline.durationMs) {
      if (this.loop) {
        this.applySeek(0)
      } else {
        this.positionMs = this.timeline.durationMs
        this.playing = false
        for (const video of this.videos.values()) video.pause()
        this.resetClockAnchors()
      }
    }
  }

  private readMediaClock(
    clip: SpikeMediaClip,
    now: number,
    mediaTime: number | null,
    mediaClipId: string | null,
  ): number | null {
    const video = this.videos.get(clip.id)
    if (video === undefined || video.el.paused || video.seeking) return null
    if (video.el.readyState < 2) return null

    // `mediaTime` is the presentation timestamp of the frame that just went on
    // screen — more precise than `currentTime`, which is interpolated.
    const sec = mediaTime !== null && mediaClipId === clip.id ? mediaTime : video.el.currentTime

    const tracker = this.videoClock
    if (tracker.clipId !== clip.id || Math.abs(sec - tracker.sec) > 1e-6) {
      this.videoClock = { clipId: clip.id, sec, since: now }
      return sec
    }
    // Unchanged. Briefly that is normal — an element that has just been told
    // to play takes a moment to spin up. Past the threshold it is a stall, and
    // the wall clock keeps everything else moving until frames resume.
    return now - tracker.since > CLOCK_STALL_MS ? null : sec
  }

  /**
   * Is a clip under the playhead whose element could still start presenting?
   *
   * A clip whose media failed is deliberately not counted: the playhead has to
   * be able to run past a broken asset rather than stopping on it forever.
   */
  private waitingOnMedia(timeMs: number): boolean {
    for (const clip of this.timeline.video) {
      if (!isActive(clip, timeMs)) continue
      const video = this.videos.get(clip.id)
      if (video === undefined || video.error !== null) continue
      return true
    }
    return false
  }

  private resetClockAnchors(): void {
    this.wallAnchorMs = this.positionMs
    this.wallAnchorAt = performance.now()
    this.videoClock = { clipId: '', sec: -1, since: 0 }
  }

  private applySeek(ms: number): void {
    const clamped = Math.min(Math.max(ms, 0), this.timeline.durationMs)
    this.positionMs = clamped
    this.resetClockAnchors()

    for (const clip of this.timeline.video) {
      const video = this.videos.get(clip.id)
      if (video === undefined || video.error !== null) continue
      if (isActive(clip, clamped)) {
        video.seekTo(sourceTimeMs(clip, clamped) / 1000)
        if (this.playing) video.play()
        else video.pause()
      } else {
        video.pause()
      }
    }
  }

  // ------------------------------------------------------------------ elements

  private syncElements(layers: FrameLayers): void {
    const t = this.positionMs

    for (const clip of this.timeline.video) {
      const video = this.videos.get(clip.id)
      if (video === undefined || video.error !== null) continue

      const active = isActive(clip, t)
      const el = video.el

      if (!active) {
        video.pause()
        // Get the next clip decoded and parked on its first frame before the
        // playhead arrives. Re-parking on every pass matters: after a loop or
        // a seek the element is wherever it stopped, and `primed` only records
        // that it has ever presented a frame.
        const upcoming = t >= clip.startMs - PRELOAD_MS && t < clip.startMs
        if (upcoming && el.paused && !video.seeking) {
          const start = clip.sourceInMs / 1000
          if (!video.primed) void video.prime(start)
          else if (Math.abs(el.currentTime - start) > START_EPS_S) video.seekTo(start)
        }
        continue
      }

      const expectedSec = sourceTimeMs(clip, t) / 1000

      if (!this.playing) {
        video.pause()
        // Paused on an active clip: decode one frame so the preview is not
        // black before the first play.
        if (!video.primed && !video.seeking) void video.prime(expectedSec)
        continue
      }

      if (el.paused) {
        // Never start playback from the wrong frame. If the element is not on
        // its in-point yet, seek and wait — the playhead is held meanwhile, so
        // the target is not moving away underneath the seek.
        if (Math.abs(el.currentTime - expectedSec) > START_EPS_S) {
          if (!video.seeking) video.seekTo(expectedSec)
        } else {
          video.play()
        }
      } else if (clip.id !== this.clockClipId && !video.seeking) {
        // Followers are pulled back to the clock. The clock element is never
        // corrected against itself — that is a feedback loop, not a sync.
        if (Math.abs(el.currentTime - expectedSec) > RESYNC_S) video.seekTo(expectedSec)
      }

      video.setVolume(gainFor(clip, layers))
    }
  }

  // ---------------------------------------------------------------------- draw

  private drawFrame(layers: FrameLayers): void {
    const toLayer = (clip: SpikeMediaClip | null) => {
      if (clip === null) return null
      const video = this.videos.get(clip.id)
      if (video === undefined || video.error !== null) return null
      const grade = clip.effects[0]
      return {
        video: video.el,
        strength: clamp01((grade?.strength ?? 0) * this.lutStrength),
      }
    }

    const status = this.renderer.draw({
      base: toLayer(layers.base),
      over: toLayer(layers.over),
      mix: layers.mix,
    })
    if (status === 'skipped') this.skippedDraws++
    this.overlay.render(this.timeline.text, this.positionMs)
  }

  private emitStats(layers: FrameLayers): void {
    let dropped = 0
    let decoded = 0
    let mediaError: string | null = null
    let playError: string | null = null
    const primed: { label: string; primed: boolean }[] = []

    for (const clip of this.timeline.video) {
      const video = this.videos.get(clip.id)
      if (video === undefined) continue
      primed.push({ label: clip.label, primed: video.primed })
      if (video.error !== null && mediaError === null) mediaError = video.error
      if (video.lastPlayError !== null && playError === null) playError = video.lastPlayError
      const quality = video.quality
      if (quality !== null) {
        dropped += quality.dropped
        decoded += quality.total
      }
    }

    this.onStats({
      positionMs: this.positionMs,
      durationMs: this.timeline.durationMs,
      playing: this.playing,
      mode: this.timeline.mode,
      loopFps: this.dtEma > 0 ? 1000 / this.dtEma : 0,
      frameCostMs: this.costEma,
      driver: this.driver,
      clock: this.clockSource,
      layers: describeLayers(layers),
      mix: layers.mix,
      canvasWidth: this.width,
      canvasHeight: this.height,
      droppedFrames: dropped,
      decodedFrames: decoded,
      textRedraws: this.overlay.redrawCount,
      skippedDraws: this.skippedDraws,
      primed,
      mediaError,
      playError,
      contextLost: this.renderer.lost,
      gpu: this.renderer.renderer,
    })
  }
}

function gainFor(clip: SpikeMediaClip, layers: FrameLayers): number {
  if (layers.over === null) return layers.base?.id === clip.id ? 1 : 0
  if (layers.base?.id === clip.id) return crossfadeGain(layers.mix, 'base')
  if (layers.over.id === clip.id) return crossfadeGain(layers.mix, 'over')
  return 0
}

function describeLayers(layers: FrameLayers): string {
  if (layers.base === null) return '—'
  if (layers.over === null) return layers.base.label
  return `${layers.base.label} → ${layers.over.label} ${Math.round(layers.mix * 100)}%`
}

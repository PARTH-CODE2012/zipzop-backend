/**
 * One hidden `<video>` per clip, with the priming and seek discipline the
 * compositor needs.
 *
 * M1 has two clips and two elements. The real pool (M2) recycles three across
 * an arbitrary number of clips — creating one element per clip exhausts the
 * browser's decoder budget within a couple of dozen — but the per-element
 * behaviour proven here is the same, and it is where the traps are:
 *
 *  - **Priming.** A `<video>` that has never played will not decode and present
 *    a frame just because you set `currentTime` in some browsers. `play()`
 *    immediately followed by `pause()` forces it. Done muted, so the blip is
 *    silent and the autoplay policy cannot refuse it.
 *  - **Seeks are throttled to ~15/s** with a trailing seek, so a playhead drag
 *    never queues a hundred of them. Queued seeks make scrubbing feel worse,
 *    not better (docs/04-frontend-architecture.md §4.6).
 *  - Every wait is bounded. A `seeked` that never arrives, a file that never
 *    loads — each has a watchdog, because the alternative is a promise that
 *    never settles and a render loop that stops.
 */

/** Minimal shape of what `requestVideoFrameCallback` hands back. */
export interface RvfcMetadata {
  readonly mediaTime: number
  readonly presentedFrames: number
}

export type RvfcVideo = HTMLVideoElement & {
  requestVideoFrameCallback(callback: (now: number, metadata: RvfcMetadata) => void): number
  cancelVideoFrameCallback(handle: number): void
}

export function supportsRvfc(el: HTMLVideoElement): el is RvfcVideo {
  return typeof (el as Partial<RvfcVideo>).requestVideoFrameCallback === 'function'
}

/** ~15 seeks per second. */
const SEEK_MIN_INTERVAL_MS = 66
/** Under an eighth of a frame at 30 fps — below this a seek is a no-op. */
const SEEK_EPS_S = 0.004
const SEEK_WATCHDOG_MS = 3_000
const PRIME_TIMEOUT_MS = 8_000

export class PooledVideo {
  readonly el: HTMLVideoElement
  readonly clipId: string
  /**
   * The URL as it was given, kept because `el.src` comes back resolved to an
   * absolute URL. `setTimeline` compares these to decide which elements can be
   * kept, and a comparison against the resolved form would never match a
   * relative path.
   */
  readonly src: string

  private readonly abort = new AbortController()
  private disposed = false

  private pendingSeek = false
  private desiredSec: number | null = null
  private lastSeekAt = 0
  private seekTimer = 0
  private seekWatchdog = 0
  private seekWaiters: (() => void)[] = []

  private playPending = false
  private playBlockedUntil = 0
  private priming = false
  private primeAborted = false

  /** Set once a real frame has been decoded and presented at `sourceInMs`. */
  primed = false
  /** Human-readable reason the media will not play, or null. */
  error: string | null = null
  lastPlayError: string | null = null

  constructor(clipId: string, src: string, host: HTMLElement) {
    this.clipId = clipId
    this.src = src

    const el = document.createElement('video')

    // **Set this before `src`.** Assigning `crossOrigin` after the source has
    // been set does nothing: the fetch has already been started without CORS,
    // and the browser will not restart it.
    //
    // Without it the element loads and plays perfectly — and then
    // `texImage2D` throws `SecurityError: the video element contains
    // cross-origin data`, because a frame from an opaque response taints the
    // canvas. The proxy comes from object storage on another origin (MinIO on
    // :9000 in development, a CDN in production) while the app is on :3000, so
    // this is the normal case, not an edge one. The picture is simply never
    // drawn.
    //
    // `anonymous` sends no credentials, which is right: the presigned
    // signature is in the query string and the bucket is private, so there is
    // nothing for a cookie to add. The storage side must answer with
    // `Access-Control-Allow-Origin` — MinIO does by default; CloudFront needs
    // a response-headers policy that says so.
    el.crossOrigin = 'anonymous'

    el.src = src
    el.preload = 'auto'
    el.playsInline = true
    el.controls = false
    el.loop = false
    // Kept in the layout rather than `display:none`: a video the browser
    // believes is not rendered can have its decode throttled, and the whole
    // point of these elements is that they keep decoding.
    el.style.flex = '1 1 0'
    el.style.minWidth = '0'
    el.style.height = '100%'
    el.style.objectFit = 'contain'
    el.style.pointerEvents = 'none'

    const { signal } = this.abort

    el.addEventListener('seeked', () => this.onSeeked(), { signal })
    el.addEventListener('loadedmetadata', () => this.pumpSeek(), { signal })
    el.addEventListener('loadeddata', () => this.pumpSeek(), { signal })
    el.addEventListener('canplay', () => this.pumpSeek(), { signal })
    el.addEventListener(
      'error',
      () => {
        this.error = describeMediaError(el, src)
        this.settleSeek()
      },
      { signal },
    )

    host.appendChild(el)
    el.load()
    this.el = el
  }

  get seeking(): boolean {
    return this.pendingSeek || this.desiredSec !== null
  }

  get quality(): { dropped: number; total: number } | null {
    const el = this.el
    if (typeof el.getVideoPlaybackQuality !== 'function') return null
    const q = el.getVideoPlaybackQuality()
    return { dropped: q.droppedVideoFrames, total: q.totalVideoFrames }
  }

  setMuted(muted: boolean): void {
    if (this.priming) return // prime() owns `muted` while it runs
    this.el.muted = muted
  }

  setVolume(volume: number): void {
    const v = volume < 0 ? 0 : volume > 1 ? 1 : volume
    if (Math.abs(this.el.volume - v) > 0.001) this.el.volume = v
  }

  play(): void {
    if (this.disposed || !this.el.paused || this.playPending) return
    if (performance.now() < this.playBlockedUntil) return

    this.playPending = true
    void this.el
      .play()
      .then(() => {
        this.playPending = false
        this.lastPlayError = null
      })
      .catch((err: unknown) => {
        this.playPending = false
        const name = err instanceof Error ? err.name : 'Error'
        // AbortError just means a pause landed between the call and its
        // promise — normal at a clip boundary, and not worth backing off for.
        if (name === 'AbortError') return
        this.lastPlayError = name
        this.playBlockedUntil = performance.now() + 500
      })
  }

  pause(): void {
    if (this.disposed || this.el.paused) return
    this.el.pause()
  }

  /** Throttled, superseding. The last position asked for is always the one that lands. */
  seekTo(sec: number): void {
    if (this.disposed) return
    this.desiredSec = Math.max(0, sec)
    this.pumpSeek()
  }

  /** Resolves when no seek is in flight and none is queued. */
  seekSettled(): Promise<void> {
    if (this.disposed || !this.seeking) return Promise.resolve()
    return new Promise<void>((resolve) => {
      this.seekWaiters.push(resolve)
    })
  }

  /**
   * Get a real frame on screen at `sec`, ready to be sampled.
   *
   * This is the whole answer to "no black flash at the cut": by the time the
   * playhead reaches clip B, B has already decoded and presented the frame it
   * is going to start on.
   */
  async prime(sec: number): Promise<void> {
    if (this.disposed || this.primed || this.priming || this.error !== null) return
    this.priming = true
    this.primeAborted = false
    try {
      await withTimeout(this.whenMetadata(), PRIME_TIMEOUT_MS)
      if (this.stopPriming()) return

      this.seekTo(sec)
      await withTimeout(this.seekSettled(), PRIME_TIMEOUT_MS)
      if (this.stopPriming()) return

      const wasMuted = this.el.muted
      this.el.muted = true
      try {
        await this.el.play()
        this.el.pause()
      } catch {
        // Autoplay refused even muted (Safari can be configured that way).
        // The seek above will usually have presented a frame regardless.
      } finally {
        this.el.muted = wasMuted
      }
      if (this.stopPriming()) return

      if (Math.abs(this.el.currentTime - sec) > 0.02) {
        this.seekTo(sec)
        await withTimeout(this.seekSettled(), PRIME_TIMEOUT_MS)
      }
      if (this.stopPriming()) return

      this.primed = true
    } finally {
      this.priming = false
      this.primeAborted = false
    }
  }

  /**
   * Stop a prime that is mid-flight.
   *
   * Priming pauses the element on purpose. If playback starts while a prime is
   * still running, its `pause()` lands on a clip that is meant to be playing
   * and stops it for a frame. The engine calls this the moment it starts.
   */
  cancelPrime(): void {
    if (this.priming) this.primeAborted = true
  }

  private stopPriming(): boolean {
    return this.disposed || this.primeAborted || this.error !== null
  }

  dispose(): void {
    if (this.disposed) return
    this.disposed = true

    if (this.seekTimer !== 0) window.clearTimeout(this.seekTimer)
    if (this.seekWatchdog !== 0) window.clearTimeout(this.seekWatchdog)
    this.seekTimer = 0
    this.seekWatchdog = 0
    this.desiredSec = null
    this.pendingSeek = false
    this.settleSeek()

    this.abort.abort()
    try {
      this.el.pause()
    } catch {
      // Already detached; nothing to stop.
    }
    // Releasing the decoder needs both: clearing `src` alone leaves the old
    // resource selected until `load()` runs the algorithm again.
    this.el.removeAttribute('src')
    this.el.load()
    this.el.remove()
  }

  // ------------------------------------------------------------------ internals

  private whenMetadata(): Promise<void> {
    if (this.el.readyState >= HTMLMediaElement.HAVE_METADATA) return Promise.resolve()
    if (this.error !== null) return Promise.reject(new Error(this.error))

    return new Promise<void>((resolve, reject) => {
      const { signal } = this.abort
      const done = new AbortController()
      const settle = (fn: () => void) => {
        done.abort()
        fn()
      }
      this.el.addEventListener('loadedmetadata', () => settle(resolve), {
        once: true,
        signal: done.signal,
      })
      this.el.addEventListener(
        'error',
        () => settle(() => reject(new Error(this.error ?? 'media error'))),
        { once: true, signal: done.signal },
      )
      signal.addEventListener('abort', () => settle(resolve), { once: true, signal: done.signal })
    })
  }

  private pumpSeek(): void {
    if (this.disposed) return
    const target = this.desiredSec
    if (target === null || this.pendingSeek) return

    // No metadata yet: the load events all pump again, so this is not a stall.
    if (this.el.readyState < HTMLMediaElement.HAVE_METADATA) return

    const now = performance.now()
    const wait = SEEK_MIN_INTERVAL_MS - (now - this.lastSeekAt)
    if (wait > 0) {
      if (this.seekTimer === 0) {
        this.seekTimer = window.setTimeout(() => {
          this.seekTimer = 0
          this.pumpSeek()
        }, wait)
      }
      return
    }

    this.desiredSec = null
    if (Math.abs(this.el.currentTime - target) <= SEEK_EPS_S) {
      this.settleSeek()
      return
    }

    this.lastSeekAt = now
    this.pendingSeek = true
    this.el.currentTime = target

    // `seeked` occasionally never arrives — a dropped range request, a decoder
    // hiccup. Nothing downstream may wait on it forever.
    this.seekWatchdog = window.setTimeout(() => {
      this.seekWatchdog = 0
      if (this.pendingSeek) {
        this.pendingSeek = false
        this.pumpSeek()
        this.settleSeek()
      }
    }, SEEK_WATCHDOG_MS)
  }

  private onSeeked(): void {
    if (this.seekWatchdog !== 0) {
      window.clearTimeout(this.seekWatchdog)
      this.seekWatchdog = 0
    }
    this.pendingSeek = false
    if (this.desiredSec !== null) this.pumpSeek()
    else this.settleSeek()
  }

  private settleSeek(): void {
    if (this.seeking && !this.disposed) return
    const waiters = this.seekWaiters
    this.seekWaiters = []
    for (const resolve of waiters) resolve()
  }
}

function describeMediaError(el: HTMLVideoElement, src: string): string {
  const code = el.error?.code
  switch (code) {
    case MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED:
      return `${src} is missing or is not playable H.264 — run \`make spike-media\``
    case MediaError.MEDIA_ERR_NETWORK:
      return `${src} failed to download`
    case MediaError.MEDIA_ERR_DECODE:
      return `${src} failed to decode`
    case MediaError.MEDIA_ERR_ABORTED:
      return `${src} load was aborted`
    default:
      return `${src} could not be loaded`
  }
}

function withTimeout(promise: Promise<void>, ms: number): Promise<void> {
  return new Promise<void>((resolve) => {
    const timer = window.setTimeout(resolve, ms)
    void promise
      .catch(() => undefined)
      .finally(() => {
        window.clearTimeout(timer)
        resolve()
      })
  })
}

'use client'

/**
 * The preview canvas: the M1 compositor, driven by the real timeline.
 *
 * The engine is created once and kept for the life of the component. It is
 * expensive — a WebGL context, a program, a 3D texture, and one decoding
 * `<video>` per clip — and rebuilding it on every render would drop the
 * decoded frames it is holding.
 *
 * Two rules carried over from M1 that this must not undo (see the write-up in
 * `README.md` in this directory):
 *
 * 1. A texture slot remembers **which source** it holds. Slots are assigned by
 *    role, so the base slot changes element at every cut, and "this slot has a
 *    frame" is not "this clip has a frame".
 * 2. The playhead **holds** while an element is still seeking to its in-point.
 *    Sliding on the wall clock instead makes the clip ignore its `sourceInMs`.
 *
 * Both live inside the engine and the renderer. Nothing here is allowed to
 * drive the playhead directly while playing, which is why the store's
 * `playheadMs` is only pushed *into* the engine on a deliberate seek.
 */

import { useEffect, useRef, useState } from 'react'

import { isKnownLut, loadLut } from '@/editor/playback/lut-catalogue'
import { selectClips, useEditor } from '@/editor/state/store'
import { identityLut } from '@/editor/playback/cube'
import { CompositorEngine, type CompositorStats } from '@/editor/playback/engine'
import { documentToPlaybackTimeline, type ResolvedAsset } from '@/editor/playback/timeline-adapter'

export interface PreviewProps {
  /** Signed proxy URLs and durations, keyed by asset id. */
  assets: Map<string, ResolvedAsset>
}

export function Preview({ assets }: PreviewProps) {
  const glRef = useRef<HTMLCanvasElement>(null)
  const textRef = useRef<HTMLCanvasElement>(null)
  const hostRef = useRef<HTMLDivElement>(null)
  const engineRef = useRef<CompositorEngine | null>(null)

  const timeline = useEditor((state) => state.timeline)
  const clips = useEditor(selectClips)
  const isPlaying = useEditor((state) => state.isPlaying)
  const playheadMs = useEditor((state) => state.playheadMs)
  const setPlayhead = useEditor((state) => state.setPlayhead)
  const setPlaying = useEditor((state) => state.setPlaying)

  const [stats, setStats] = useState<CompositorStats | null>(null)
  const [skipped, setSkipped] = useState(0)

  // ---- create once -------------------------------------------------------
  useEffect(() => {
    const gl = glRef.current
    const text = textRef.current
    const host = hostRef.current
    if (!gl || !text || !host) return

    const engine = new CompositorEngine({
      glCanvas: gl,
      textCanvas: text,
      videoHost: host,
      // Identity until a graded clip needs something else — see the effect
      // below, which loads the named LUT the moment one appears.
      lut: identityLut(),
      onStats: setStats,
      timeline: { mode: 'cut', transitionMs: 0, durationMs: 0, video: [], text: [] },
    })
    engine.setResolution(1280, 720)
    engineRef.current = engine

    return () => {
      engine.dispose()
      engineRef.current = null
    }
  }, [])

  // ---- push the document in whenever it or the assets change -------------
  useEffect(() => {
    const engine = engineRef.current
    if (!engine) return
    const { timeline: playable, skipped: dropped } = documentToPlaybackTimeline(
      timeline,
      (assetId) => assets.get(assetId),
    )
    engine.setTimeline(playable)
    setSkipped(dropped.length)
  }, [timeline, assets, clips.length])

  // ---- play and pause ----------------------------------------------------
  useEffect(() => {
    const engine = engineRef.current
    if (!engine) return
    if (isPlaying) engine.play()
    else engine.pause()
  }, [isPlaying])

  // ---- the engine owns the playhead while playing ------------------------
  useEffect(() => {
    if (!stats) return
    if (stats.playing) {
      // Mirror the engine's clock into the store so the ruler follows. The
      // other direction is deliberately not wired: writing the store's value
      // back into the engine on every frame would fight the media clock and
      // reintroduce M1's second bug.
      setPlayhead(stats.positionMs)
      if (stats.positionMs >= stats.durationMs && stats.durationMs > 0) setPlaying(false)
    }
  }, [stats, setPlayhead, setPlaying])

  /**
   * Load whichever grade the timeline is asking for.
   *
   * ⚠️ **One LUT is loaded at a time**, so a project mixing two graded clips
   * shows the first one's look on both. The renderer holds a single 3D texture,
   * and giving it one per clip is a real change to the shader and the pool
   * rather than a change here. Colour analysis grades one clip at a time, so
   * this is right until it is not — and it is written down rather than
   * discovered.
   */
  useEffect(() => {
    const engine = engineRef.current
    if (!engine) return
    const graded = clips.find((clip) =>
      clip.effects.some((effect) => effect.type === 'color_grade'),
    )
    const grade = graded?.effects.find((effect) => effect.type === 'color_grade')
    if (!grade || !isKnownLut(grade.lut)) {
      engine.setLut(identityLut())
      return
    }
    let cancelled = false
    void loadLut(grade.lut).then((lut) => {
      // The clip may have been deleted or regraded while the file was in
      // flight; applying a stale table would show a look nothing asked for.
      if (!cancelled && lut) engineRef.current?.setLut(lut)
    })
    return () => {
      cancelled = true
    }
  }, [clips])

  // ---- a deliberate seek, only while paused ------------------------------
  useEffect(() => {
    const engine = engineRef.current
    if (!engine || engine.isPlaying) return
    if (Math.abs(engine.playheadMs() - playheadMs) > 1) engine.seek(playheadMs)
  }, [playheadMs])

  return (
    // `p-4` belongs to the neon frame: its glow is drawn outside the picture's
    // box, and with the picture flush to the panel edges the light fell on the
    // media list and the inspector instead of on dark space.
    <div className="relative flex h-full w-full items-center justify-center bg-black/40 p-4">
      {/* `vivid-frame` is the neon ring — decoration only, and defined once in
          globals.css so no colour lands in this file. It sits on the picture's
          own box rather than the container so it tracks the 16:9 letterbox
          instead of the empty space around it. */}
      <div
        className="vivid-frame"
        style={{ aspectRatio: '16 / 9', maxHeight: '100%', width: '100%' }}
        data-testid="preview-frame"
      >
        <canvas
          ref={glRef}
          className="absolute inset-0 h-full w-full"
          style={{ borderRadius: 'inherit' }}
          data-testid="preview-gl"
        />
        <canvas
          ref={textRef}
          className="pointer-events-none absolute inset-0 h-full w-full"
          style={{ borderRadius: 'inherit' }}
          data-testid="preview-text"
        />
      </div>

      {/* The decoding elements. Kept in the layout rather than display:none —
          a video the browser believes is not rendered can have its decode
          throttled, and continuous decoding is the whole point of them. */}
      <div
        ref={hostRef}
        aria-hidden
        className="pointer-events-none absolute h-px w-px overflow-hidden opacity-0"
      />

      <div
        className="pointer-events-none absolute right-2 bottom-2 font-mono text-[10px]"
        style={{ color: 'var(--color-ink-2)' }}
        data-testid="preview-stats"
        data-driver={stats?.driver ?? ''}
        data-clock={stats?.clock ?? ''}
        data-fps={stats ? Math.round(stats.loopFps) : ''}
        data-dropped={stats?.droppedFrames ?? ''}
        data-skipped-draws={stats?.skippedDraws ?? ''}
        data-position-ms={stats?.positionMs ?? ''}
        data-clips-skipped={skipped}
      >
        {stats ? `${Math.round(stats.loopFps)} fps · ${stats.driver} · ${stats.clock}` : ''}
      </div>
    </div>
  )
}

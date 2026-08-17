'use client'

/**
 * The timeline shell: ruler, playhead, zoom, one video track.
 *
 * ⚠️ **This has no visual identity, deliberately.** No palette, typography or
 * visual states have been delivered by the project lead. Every colour below
 * comes from a token in `src/styles/globals.css`, and every one of those
 * tokens is currently a neutral grey. Applying the real charter means editing
 * that file — nothing here holds a literal colour, so a reskin cannot break
 * the behaviour.
 *
 * What *is* settled here is the behaviour, because that is what M2 exists to
 * prove: milliseconds in, pixels out, a waveform on a canvas rather than sixty
 * thousand elements, and a playhead you can drag.
 */

import { useCallback, useLayoutEffect, useRef, useState } from 'react'

import {
  selectClips,
  selectDurationMs,
  useEditor,
} from '@/editor/state/store'
import { clipEndMs, type MediaClip } from '@/editor/state/timeline-document'
import {
  MAX_ZOOM,
  MIN_ZOOM,
  formatTimecode,
  msToPx,
  pxToMs,
  scrollForAnchoredZoom,
  tickLabel,
  ticksForWindow,
} from '@/editor/timeline/scale'
import { WaveformCanvas } from '@/editor/timeline/WaveformCanvas'

const RULER_HEIGHT = 28
const TRACK_HEIGHT = 72
const HEADER_WIDTH = 96

export function Timeline() {
  const clips = useEditor(selectClips)
  const durationMs = useEditor(selectDurationMs)
  const zoom = useEditor((state) => state.zoom)
  const playheadMs = useEditor((state) => state.playheadMs)
  const selectedClipId = useEditor((state) => state.selectedClipId)
  const setZoom = useEditor((state) => state.setZoom)
  const setPlayhead = useEditor((state) => state.setPlayhead)
  const select = useEditor((state) => state.select)

  const laneRef = useRef<HTMLDivElement>(null)
  const [viewportPx, setViewportPx] = useState(0)
  const [scrollPx, setScrollPx] = useState(0)

  useLayoutEffect(() => {
    const lane = laneRef.current
    if (!lane) return
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (entry) setViewportPx(entry.contentRect.width)
    })
    observer.observe(lane)
    setViewportPx(lane.clientWidth)
    return () => observer.disconnect()
  }, [])

  const fromMs = pxToMs(scrollPx, zoom)
  const toMs = pxToMs(scrollPx + viewportPx, zoom)
  const ticks = viewportPx > 0 ? ticksForWindow(fromMs, toMs, zoom) : []

  /**
   * Ctrl/⌘ + wheel zooms around the cursor; a plain wheel scrolls. Zooming
   * around the cursor rather than the left edge is what stops the clip you are
   * looking at sliding out from under the hand.
   */
  const onWheel = useCallback(
    (event: React.WheelEvent) => {
      if (!(event.ctrlKey || event.metaKey)) {
        setScrollPx((current) => Math.max(0, current + event.deltaX + event.deltaY))
        return
      }
      event.preventDefault()
      const lane = laneRef.current
      if (!lane) return
      const anchorPx = event.clientX - lane.getBoundingClientRect().left
      const anchorMs = pxToMs(scrollPx + anchorPx, zoom)
      const next = zoom * (event.deltaY < 0 ? 1.15 : 1 / 1.15)
      const clamped = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, next))
      setZoom(clamped)
      setScrollPx(scrollForAnchoredZoom(anchorMs, anchorPx, clamped))
    },
    [scrollPx, zoom, setZoom],
  )

  /** Scrubbing: pointer capture so a drag that leaves the element still tracks. */
  const scrub = useCallback(
    (event: React.PointerEvent) => {
      const lane = laneRef.current
      if (!lane) return
      const x = event.clientX - lane.getBoundingClientRect().left
      setPlayhead(pxToMs(scrollPx + x, zoom))
    },
    [scrollPx, zoom, setPlayhead],
  )

  const onPointerDown = useCallback(
    (event: React.PointerEvent) => {
      ;(event.target as Element).setPointerCapture?.(event.pointerId)
      scrub(event)
    },
    [scrub],
  )

  const onPointerMove = useCallback(
    (event: React.PointerEvent) => {
      if (event.buttons !== 1) return
      scrub(event)
    },
    [scrub],
  )

  return (
    <section
      className="no-select flex flex-col border-t"
      style={{ borderColor: 'var(--color-rule)' }}
      data-testid="timeline"
      data-zoom={zoom}
      data-playhead-ms={playheadMs}
      data-duration-ms={durationMs}
      data-clip-count={clips.length}
    >
      <header
        className="flex items-center gap-4 px-3 py-1.5 text-xs"
        style={{ color: 'var(--color-ink-2)' }}
      >
        <span className="font-mono tabular-nums" data-testid="playhead-readout">
          {formatTimecode(playheadMs, { withMillis: true })}
        </span>
        <span className="font-mono tabular-nums opacity-70">
          / {formatTimecode(durationMs, { withMillis: true })}
        </span>
        <label className="ml-auto flex items-center gap-2">
          <span>Zoom</span>
          <input
            type="range"
            min={MIN_ZOOM}
            max={MAX_ZOOM}
            step={1}
            value={zoom}
            onChange={(event) => setZoom(Number(event.target.value))}
            data-testid="zoom"
            aria-label="Timeline zoom"
          />
        </label>
      </header>

      <div className="flex min-h-0 flex-1">
        <div
          className="shrink-0 border-r text-xs"
          style={{ width: HEADER_WIDTH, borderColor: 'var(--color-rule)' }}
        >
          <div style={{ height: RULER_HEIGHT }} />
          <div
            className="flex items-center px-3"
            style={{
              height: TRACK_HEIGHT,
              background: 'var(--color-track-header)',
              color: 'var(--color-ink-2)',
            }}
          >
            Video
          </div>
        </div>

        <div
          ref={laneRef}
          className="relative min-w-0 flex-1 overflow-hidden"
          onWheel={onWheel}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          data-testid="timeline-lane"
        >
          <Ruler ticks={ticks} scrollPx={scrollPx} zoom={zoom} />

          <div
            className="relative"
            style={{ height: TRACK_HEIGHT, background: 'var(--color-track)' }}
            data-testid="video-track"
          >
            {clips.map((clip) => (
              <Clip
                key={clip.id}
                clip={clip}
                zoom={zoom}
                scrollPx={scrollPx}
                selected={clip.id === selectedClipId}
                onSelect={() => select(clip.id)}
              />
            ))}
          </div>

          <Playhead ms={playheadMs} zoom={zoom} scrollPx={scrollPx} />
        </div>
      </div>
    </section>
  )
}

function Ruler({
  ticks,
  scrollPx,
  zoom,
}: {
  ticks: { ms: number; px: number; major: boolean }[]
  scrollPx: number
  zoom: number
}) {
  return (
    <div
      className="relative"
      style={{ height: RULER_HEIGHT, background: 'var(--color-ruler)' }}
      data-testid="ruler"
      data-tick-count={ticks.length}
    >
      {ticks.map((tick) => (
        <div
          key={tick.ms}
          className="absolute bottom-0"
          style={{
            left: tick.px - scrollPx,
            height: tick.major ? 10 : 5,
            width: 1,
            background: 'var(--color-ruler-tick)',
          }}
        >
          {tick.major && (
            <span
              className="absolute bottom-3 left-1 font-mono text-[10px] tabular-nums whitespace-nowrap"
              style={{ color: 'var(--color-ruler-label)' }}
            >
              {tickLabel(tick.ms, zoom)}
            </span>
          )}
        </div>
      ))}
    </div>
  )
}

function Clip({
  clip,
  zoom,
  scrollPx,
  selected,
  onSelect,
}: {
  clip: MediaClip
  zoom: number
  scrollPx: number
  selected: boolean
  onSelect: () => void
}) {
  const left = msToPx(clip.startMs, zoom) - scrollPx
  const width = msToPx(clip.durationMs, zoom)

  return (
    <div
      className="absolute top-1 bottom-1 overflow-hidden rounded-sm border"
      style={{
        left,
        width,
        background: selected ? 'var(--color-clip-selected)' : 'var(--color-clip)',
        borderColor: selected ? 'var(--color-clip-selected-border)' : 'var(--color-clip-border)',
      }}
      onPointerDown={(event) => {
        // Stop the scrub handler on the lane from also firing: clicking a clip
        // selects it, it does not move the playhead.
        event.stopPropagation()
        onSelect()
      }}
      data-testid="clip"
      data-clip-id={clip.id}
      data-asset-id={clip.assetId}
      data-start-ms={clip.startMs}
      data-duration-ms={clip.durationMs}
      data-end-ms={clipEndMs(clip)}
      data-selected={selected}
    >
      <WaveformCanvas assetId={clip.assetId} clip={clip} widthPx={width} />
      <span
        className="pointer-events-none absolute top-1 left-2 text-[10px]"
        style={{ color: 'var(--color-ink-2)' }}
      >
        {formatTimecode(clip.durationMs)}
      </span>
    </div>
  )
}

function Playhead({ ms, zoom, scrollPx }: { ms: number; zoom: number; scrollPx: number }) {
  const left = msToPx(ms, zoom) - scrollPx
  return (
    <div
      className="pointer-events-none absolute top-0 bottom-0"
      style={{ left, width: 1, background: 'var(--color-playhead)' }}
      data-testid="playhead"
      data-left-px={left}
    />
  )
}

/** Re-exported so the editor page can size its own layout consistently. */
export { RULER_HEIGHT, TRACK_HEIGHT }
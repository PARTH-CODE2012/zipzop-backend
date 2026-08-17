'use client'

/**
 * One canvas per clip, holding its waveform.
 *
 * The peaks document is fetched once per asset and cached in a module-level
 * map. A 10-minute file is ~400 KB and it never changes — refetching it every
 * time a clip re-renders would be the most expensive thing on the page.
 *
 * The colour is read from the CSS token at draw time rather than hard-coded,
 * so re-skinning the editor does not mean editing canvas code.
 */

import { useEffect, useRef, useState } from 'react'

import type { MediaClip } from '@/editor/state/timeline-document'
import {
  columnsForWindow,
  drawWaveform,
  sizeCanvasForDisplay,
  type Peaks,
} from '@/editor/timeline/waveform'
import { getPeaks } from '@/lib/api/endpoints'

const cache = new Map<string, Promise<Peaks | null>>()

function loadPeaks(assetId: string): Promise<Peaks | null> {
  let pending = cache.get(assetId)
  if (!pending) {
    pending = getPeaks(assetId)
      .then((document) => ({
        peaks: document.peaks,
        bucketsPerSecond: document.bucketsPerSecond,
      }))
      .catch(() => null) // an asset can be ready before its waveform is asked for
    cache.set(assetId, pending)
  }
  return pending
}

export function WaveformCanvas({
  assetId,
  clip,
  widthPx,
}: {
  assetId: string
  clip: MediaClip
  widthPx: number
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [peaks, setPeaks] = useState<Peaks | null>(null)

  useEffect(() => {
    let live = true
    loadPeaks(assetId).then((loaded) => {
      if (live) setPeaks(loaded)
    })
    return () => {
      live = false
    }
  }, [assetId])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !peaks || widthPx <= 0) return

    const height = canvas.parentElement?.clientHeight ?? 0
    if (height <= 0) return

    const ratio = sizeCanvasForDisplay(canvas, widthPx, height, window.devicePixelRatio)
    const context = canvas.getContext('2d')
    if (!context) return

    context.setTransform(ratio, 0, 0, ratio, 0, 0)

    // The window is the *asset* range this clip shows, not the timeline range.
    // A trimmed clip must draw the part of the waveform it actually plays —
    // drawing from zero would put the peaks under the wrong frames.
    const fromMs = clip.sourceInMs
    const toMs = clip.sourceInMs + Math.round(clip.durationMs * clip.speed)

    const columns = columnsForWindow(peaks, fromMs, toMs, widthPx)
    const colour =
      getComputedStyle(canvas).getPropertyValue('--color-waveform').trim() || '#7f9a9c'
    drawWaveform(context, columns, widthPx, height, { color: colour })
  }, [peaks, widthPx, clip.sourceInMs, clip.durationMs, clip.speed])

  return (
    <canvas
      ref={canvasRef}
      className="pointer-events-none absolute inset-0 h-full w-full opacity-70"
      data-testid="waveform"
      data-asset-id={assetId}
      data-loaded={peaks !== null}
    />
  )
}

/** Used by tests that need a clean slate between renders. */
export function clearPeaksCache(): void {
  cache.clear()
}

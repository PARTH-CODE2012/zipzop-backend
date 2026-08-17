/**
 * The timeline document, as the playback engine wants it.
 *
 * The engine was written for the M1 spike and speaks a shape with the media
 * URL and the asset's own duration on each clip. The document (contract §4)
 * carries neither: it names an `assetId`, and the URL is a signed link that
 * expires in an hour and must never be persisted alongside it.
 *
 * So this is the join. It is deliberately a **pure function of a document and
 * a lookup** rather than a method on either side: the engine keeps knowing
 * nothing about the API, the document keeps knowing nothing about playback,
 * and the conversion is testable without a browser.
 */

import type { MediaClip, TimelineDocument } from '@/editor/state/timeline-document'
import type { SpikeMediaClip, SpikeTimeline } from '@/editor/playback/timeline'

/** What the adapter needs to know about an asset that the document does not carry. */
export interface ResolvedAsset {
  /** The signed `proxyUrl` from `GET /media/{id}`. Expires in an hour. */
  proxyUrl: string
  /** The asset's own duration, for clamping a seek to the end of the media. */
  durationMs: number
}

export type AssetLookup = (assetId: string) => ResolvedAsset | undefined

export interface AdapterResult {
  timeline: SpikeTimeline
  /** Clips dropped because their asset had no usable proxy, with the reason. */
  skipped: { clipId: string; assetId: string; reason: string }[]
}

/**
 * Build a playable timeline from a document.
 *
 * A clip whose asset is missing or not yet ingested is **left out rather than
 * faked**. Handing the engine a clip with an empty `src` would make the video
 * element fail to load and the playhead hold forever waiting for a frame that
 * is never coming — the exact failure mode the M1 spike's second bug fix
 * exists to handle. Dropping it and reporting why lets the interface say
 * "still preparing" instead.
 */
export function documentToPlaybackTimeline(
  document: TimelineDocument,
  lookup: AssetLookup,
): AdapterResult {
  const skipped: AdapterResult['skipped'] = []
  const video: SpikeMediaClip[] = []

  const videoTrack = document.tracks.find((track) => track.kind === 'video')
  for (const clip of videoTrack?.clips ?? []) {
    const asset = lookup(clip.assetId)
    if (!asset) {
      skipped.push({ clipId: clip.id, assetId: clip.assetId, reason: 'asset not loaded' })
      continue
    }
    if (!asset.proxyUrl) {
      skipped.push({ clipId: clip.id, assetId: clip.assetId, reason: 'no proxy yet' })
      continue
    }
    video.push(toPlaybackClip(clip, asset))
  }

  const durationMs = video.reduce(
    (longest, clip) => Math.max(longest, clip.startMs + clip.durationMs),
    0,
  )

  return {
    timeline: {
      // M2 has no transitions in the interface, so every join is a cut. The
      // engine's crossfade path is proven (M1, on iOS too) and waits for M3,
      // where transitions become editable.
      mode: 'cut',
      transitionMs: 0,
      durationMs,
      video,
      text: [],
    },
    skipped,
  }
}

function toPlaybackClip(clip: MediaClip, asset: ResolvedAsset): SpikeMediaClip {
  return {
    id: clip.id,
    src: asset.proxyUrl,
    // The engine uses this only for a HUD readout. The filename would be
    // better, but the document does not carry one and fetching it here would
    // couple the adapter to the API.
    label: clip.id.replace(/^clp_/, ''),
    assetDurationMs: asset.durationMs,
    startMs: clip.startMs,
    durationMs: clip.durationMs,
    sourceInMs: clip.sourceInMs,
    speed: clip.speed,
    // Defaults to empty rather than undefined: the engine's clip type requires
    // the array, and an absent one would read as "no grade" only by accident.
    effects: (clip.effects ?? []).map((effect) => ({
      type: 'color_grade' as const,
      lut: effect.lut,
      strength: effect.strength,
    })),
  }
}

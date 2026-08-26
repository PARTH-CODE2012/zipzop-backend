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

import { timelineDurationMs, trackOfKind } from '@/editor/state/timeline-document'
import type { MediaClip, TextClip, TimelineDocument } from '@/editor/state/timeline-document'
import type { SpikeMediaClip, SpikeTextClip, SpikeTimeline } from '@/editor/playback/timeline'

/**
 * Cap height as a fraction of the canvas, for a title with no style override.
 *
 * The same value the M1 spike drew its captions at, and normalised rather than
 * a pixel count so a 480p preview and a 1080p export put the words in the same
 * place (contract §4.3).
 */
export const DEFAULT_TEXT_SIZE = 0.062

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

  // `trackOfKind` narrows to a MediaTrack, so `clip` is a MediaClip and not
  // the union — a text clip has no `assetId` and nothing here would mean
  // anything for one.
  const videoTrack = trackOfKind(document, 'video')
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

  // Titles need no asset, so they are never skipped — they are the one thing
  // in the document that can be drawn the instant it is typed.
  const text = (trackOfKind(document, 'text')?.clips ?? []).map(toPlaybackText)

  /**
   * How long the timeline is — **read from the document, never measured from
   * the clips that survived the join above.**
   *
   * This used to reduce over `video` and `text`, and it was wrong twice:
   *
   *  * **The audio track was not in the sum at all.** A music bed running six
   *    seconds past the last frame was cut off mid-note, and the engine stopped
   *    somewhere the ruler said was not the end.
   *  * **Skipped clips took their length with them.** A clip whose asset has no
   *    proxy yet is deliberately dropped from `video` (see the docstring above),
   *    so a project where nothing had finished ingesting measured only its text
   *    clips — and playback looped at the **last caption word** while the
   *    timeline, the transport and `selectDurationMs` all still read the real
   *    length. Three parts of the interface agreeing with each other and a
   *    fourth quietly disagreeing.
   *
   * `timelineDurationMs` is the same function the ruler and the transport use,
   * which is the point: there is one answer to *how long is this project*, and
   * playback is not entitled to a different one. A clip still being ingested
   * does not shorten the project — it appears when it is ready.
   */
  const durationMs = timelineDurationMs(document)

  return {
    timeline: {
      // ⚠️ **Every join is a cut, including the ones the user set a dissolve
      // on.** The engine derives a crossfade from two clips *overlapping in
      // time*, and the document forbids that (invariant 1) — a transition is
      // metadata on a join, and turning it into an overlap means deciding which
      // side gives up the frames, which the contract does not say. Rendering
      // one in the preview is engine work plus a contract decision, not an
      // adapter change; see docs/09-m3-notes.md §5.
      mode: 'cut',
      transitionMs: 0,
      durationMs,
      video,
      text,
    },
    skipped,
  }
}

function toPlaybackText(clip: TextClip): SpikeTextClip {
  return {
    id: clip.id,
    startMs: clip.startMs,
    durationMs: clip.durationMs,
    text: clip.text,
    emphasis: clip.emphasis,
    // The defaults match `appendTitle`, so a title typed into the editor lands
    // where the editor said it would.
    position: { x: clip.position?.x ?? 0.5, y: clip.position?.y ?? 0.82 },
    fontSize: clip.style?.fontSize ?? DEFAULT_TEXT_SIZE,
    anchor: clip.position?.anchor ?? 'center',
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

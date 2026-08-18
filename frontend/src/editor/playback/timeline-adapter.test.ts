import { describe, expect, it } from 'vitest'

import type { MediaClip, TimelineDocument } from '@/editor/state/timeline-document'

import { documentToPlaybackTimeline, type ResolvedAsset } from './timeline-adapter'

function clip(over: Partial<MediaClip> = {}): MediaClip {
  return {
    id: 'clp_a',
    assetId: 'ast_1',
    startMs: 0,
    durationMs: 4000,
    sourceInMs: 500,
    speed: 1,
    volume: 1,
    audioFadeInMs: 0,
    audioFadeOutMs: 0,
    effects: [],
    ...over,
  }
}

function document(clips: MediaClip[]): TimelineDocument {
  return {
    schemaVersion: 1,
    tracks: [{ id: 'trk_video', kind: 'video', index: 0, muted: false, locked: false, clips }],
  }
}

const READY: ResolvedAsset = { proxyUrl: 'https://minio/proxies/x/proxy.mp4', durationMs: 12_000 }
const lookupReady = () => READY

describe('documentToPlaybackTimeline', () => {
  it('carries the in-point and duration through unchanged', () => {
    // The M1 spike measured a bug here: a clip that ignored sourceInMs started
    // at 0.02 s instead of 0.50 s. Anything that drops the field on the way to
    // the engine reintroduces it.
    const { timeline } = documentToPlaybackTimeline(document([clip()]), lookupReady)
    expect(timeline.video).toHaveLength(1)
    expect(timeline.video.at(0)?.sourceInMs).toBe(500)
    expect(timeline.video.at(0)?.durationMs).toBe(4000)
    expect(timeline.video.at(0)?.startMs).toBe(0)
  })

  it('resolves the proxy URL from the lookup, never from the document', () => {
    // Signed URLs expire in an hour, so they are never stored in the document
    // (contract §3). The join happens here, at playback time.
    const { timeline } = documentToPlaybackTimeline(document([clip()]), lookupReady)
    expect(timeline.video.at(0)?.src).toBe(READY.proxyUrl)
  })

  it('measures the timeline by its furthest clip end', () => {
    const { timeline } = documentToPlaybackTimeline(
      document([
        clip({ id: 'a', startMs: 0, durationMs: 4000 }),
        clip({ id: 'b', startMs: 4000, durationMs: 3000 }),
      ]),
      lookupReady,
    )
    expect(timeline.durationMs).toBe(7000)
  })

  it('drops a clip whose asset is not loaded, and says which', () => {
    const result = documentToPlaybackTimeline(document([clip()]), () => undefined)
    expect(result.timeline.video).toHaveLength(0)
    expect(result.skipped).toEqual([
      { clipId: 'clp_a', assetId: 'ast_1', reason: 'asset not loaded' },
    ])
  })

  it('drops a clip whose asset has no proxy yet', () => {
    // Handing the engine an empty src makes the element fail to load, and the
    // playhead then holds forever waiting for a frame that is never coming —
    // which is exactly the M1 fix working correctly on bad input.
    const result = documentToPlaybackTimeline(document([clip()]), () => ({
      proxyUrl: '',
      durationMs: 12_000,
    }))
    expect(result.timeline.video).toHaveLength(0)
    expect(result.skipped.at(0)?.reason).toBe('no proxy yet')
  })

  it('keeps the clips it can when only one is unusable', () => {
    const result = documentToPlaybackTimeline(
      document([
        clip({ id: 'ready', assetId: 'ast_ok' }),
        clip({ id: 'pending', assetId: 'ast_pending', startMs: 4000 }),
      ]),
      (assetId) => (assetId === 'ast_ok' ? READY : undefined),
    )
    expect(result.timeline.video.map((c) => c.id)).toEqual(['ready'])
    expect(result.skipped.map((s) => s.clipId)).toEqual(['pending'])
  })

  it('produces an empty, playable timeline for an empty document', () => {
    const { timeline } = documentToPlaybackTimeline(
      { schemaVersion: 1, tracks: [] },
      lookupReady,
    )
    expect(timeline.video).toEqual([])
    expect(timeline.durationMs).toBe(0)
    expect(timeline.mode).toBe('cut')
  })

  it('ignores audio and text tracks for now', () => {
    // M2 renders one video track. An audio clip reaching the video pool would
    // become a <video> element with no picture.
    const doc = document([clip()])
    doc.tracks.push({
      id: 'trk_music',
      kind: 'audio',
      index: 0,
      muted: false,
      locked: false,
      clips: [clip({ id: 'clp_music', assetId: 'ast_music' })],
    })
    const { timeline } = documentToPlaybackTimeline(doc, lookupReady)
    expect(timeline.video.map((c) => c.id)).toEqual(['clp_a'])
  })

  it('carries a colour grade through', () => {
    const { timeline } = documentToPlaybackTimeline(
      document([
        clip({ effects: [{ type: 'color_grade', lut: 'cinematic_warm', strength: 0.75 }] }),
      ]),
      lookupReady,
    )
    expect(timeline.video.at(0)?.effects).toEqual([
      { type: 'color_grade', lut: 'cinematic_warm', strength: 0.75 },
    ])
  })
})

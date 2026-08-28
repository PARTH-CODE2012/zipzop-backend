import { describe, expect, it } from 'vitest'

import type { MediaClip, TextClip, TimelineDocument } from '@/editor/state/timeline-document'

import {
  DEFAULT_TEXT_SIZE,
  documentToPlaybackTimeline,
  type ResolvedAsset,
} from './timeline-adapter'

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

  it('keeps audio off the video track', () => {
    // An audio clip reaching the video pool would become a <video> element with
    // no picture. The music lane is Web Audio's job, not the compositor's.
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

  /**
   * Titles used to stop at the store. The adapter handed the engine `text: []`
   * unconditionally, so a title could be added, typed, moved and saved — and
   * never once appear over the picture it was written for. The engine has drawn
   * text since M1; nothing was reaching it.
   */
  describe('the text track', () => {
    const title = (over: Partial<TextClip> = {}): TextClip => ({
      id: 'clp_t1',
      kind: 'title',
      startMs: 1_000,
      durationMs: 3_000,
      text: 'Hello',
      styleId: 'plain_bold',
      emphasis: 0,
      position: { x: 0.5, y: 0.82, anchor: 'center' },
      ...over,
    })

    const withText = (clips: TextClip[]): TimelineDocument => {
      const doc = document([clip()])
      doc.tracks.push({ id: 'trk_text', kind: 'text', index: 0, muted: false, locked: false, clips })
      return doc
    }

    it('carries a title through to the overlay', () => {
      const { timeline } = documentToPlaybackTimeline(withText([title()]), lookupReady)
      expect(timeline.text).toEqual([
        {
          id: 'clp_t1',
          startMs: 1_000,
          durationMs: 3_000,
          text: 'Hello',
          emphasis: 0,
          position: { x: 0.5, y: 0.82 },
          fontSize: DEFAULT_TEXT_SIZE,
          anchor: 'center',
        },
      ])
    })

    it('needs no asset — a title is drawable the instant it is typed', () => {
      const doc: TimelineDocument = {
        schemaVersion: 1,
        tracks: [
          { id: 'trk_text', kind: 'text', index: 0, muted: false, locked: false, clips: [title()] },
        ],
      }
      const result = documentToPlaybackTimeline(doc, () => undefined)
      expect(result.skipped).toEqual([])
      expect(result.timeline.text).toHaveLength(1)
    })

    it('takes a style override for the size, still normalised', () => {
      const { timeline } = documentToPlaybackTimeline(
        withText([title({ style: { fontSize: 0.11 } })]),
        lookupReady,
      )
      expect(timeline.text.at(0)?.fontSize).toBe(0.11)
    })

    it('measures the timeline by the last thing on it, title or clip', () => {
      // A title after the final cut is still part of the project: stopping at
      // the last video clip would end playback before the words appeared.
      const { timeline } = documentToPlaybackTimeline(
        withText([title({ startMs: 9_000, durationMs: 2_000 })]),
        lookupReady,
      )
      expect(timeline.durationMs).toBe(11_000)
    })
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

// --------------------------------------------------------------------------
// Playback length — the bug where the picture looped at the last caption
// --------------------------------------------------------------------------

/** A document with all three lanes, each ending at a different time. */
function threeLanes(over: { videoProxy?: boolean } = {}): TimelineDocument {
  return {
    schemaVersion: 1,
    tracks: [
      {
        id: 'trk_video',
        kind: 'video',
        index: 0,
        muted: false,
        locked: false,
        clips: [clip({ id: 'clp_v', assetId: over.videoProxy === false ? 'ast_pending' : 'ast_1', startMs: 0, durationMs: 9_000 })],
      },
      {
        id: 'trk_audio',
        kind: 'audio',
        index: 1,
        muted: false,
        locked: false,
        // The music runs six seconds past the last frame of video.
        clips: [clip({ id: 'clp_music', assetId: 'ast_music', startMs: 0, durationMs: 15_000 })],
      },
      {
        id: 'trk_text',
        kind: 'text',
        index: 2,
        muted: false,
        locked: false,
        clips: [
          { id: 'clp_cap', kind: 'caption', text: 'last', startMs: 6_400, durationMs: 400,
            styleId: 'caption_bold', emphasis: 0.2 } as TextClip,
        ],
      },
    ],
  }
}

describe('how long the engine thinks the timeline is', () => {
  it('runs to the end of the document, not to the end of the video track', () => {
    // The music ends at 15 s and the last frame of video is at 9 s. Stopping at
    // 9 s cuts the music off and disagrees with the duration the timeline and
    // the transport both display.
    const { timeline } = documentToPlaybackTimeline(threeLanes(), lookupReady)
    expect(timeline.durationMs).toBe(15_000)
  })

  it('does not shrink to the last caption when no clip has a proxy yet', () => {
    // **The reported bug.** Playback restarted at 6 800 ms — the end of the last
    // caption — instead of running to the end of the timeline. Clips whose asset
    // has no proxy are dropped from `video` on purpose, so with none ready the
    // only clips left to measure were the text ones, and the engine looped at
    // the last word while the ruler still read 15 s.
    //
    // A clip still being ingested must not shorten the project: it appears when
    // it is ready, and until then the length of the timeline is unchanged.
    const { timeline, skipped } = documentToPlaybackTimeline(
      threeLanes({ videoProxy: false }),
      (assetId) => (assetId === 'ast_1' ? READY : undefined),
    )
    expect(skipped).toHaveLength(1)
    expect(timeline.video).toHaveLength(0)
    expect(timeline.durationMs).toBe(15_000)
  })

  it('is zero for an empty document', () => {
    expect(documentToPlaybackTimeline({ schemaVersion: 1, tracks: [] }, lookupReady).timeline.durationMs).toBe(0)
  })
})

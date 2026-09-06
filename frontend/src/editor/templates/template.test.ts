/**
 * Saving settings from one project and applying them to another.
 *
 * Pure, so this is exhaustive without a browser. The tests worth reading are
 * the two that are not obvious: that extraction takes the **commonest** value
 * rather than the first, and that applying a template stays inside the
 * timeline's invariants — a saved 2-second dissolve landing on a 1-second clip
 * has to be clamped here, or the server rejects the next autosave and the user
 * loses the edit they just made.
 */

import { produce } from 'immer'
import { describe, expect, it } from 'vitest'

import {
  TEMPLATE_VERSION,
  applyTemplate,
  extractTemplate,
  isEmpty,
  type TemplateSettings,
} from './template'
import {
  trackOfKind,
  violatedInvariants,
  type MediaClip,
  type TextClip,
  type TimelineDocument,
} from '@/editor/state/timeline-document'

function clip(over: Partial<MediaClip> = {}): MediaClip {
  return {
    id: 'clp_a',
    assetId: 'ast_1',
    startMs: 0,
    durationMs: 10_000,
    sourceInMs: 0,
    speed: 1,
    volume: 1,
    audioFadeInMs: 0,
    audioFadeOutMs: 0,
    effects: [],
    ...over,
  }
}

function title(over: Partial<TextClip> = {}): TextClip {
  return {
    id: 'clp_t',
    text: 'Hello',
    startMs: 0,
    durationMs: 2_000,
    position: { x: 0.5, y: 0.8, align: 'center' },
    style: { color: '#ffffff', fontSize: 48, strokeColor: '#000000', strokeWidth: 3 },
    ...over,
  } as TextClip
}

function timeline(media: MediaClip[] = [], text: TextClip[] = []): TimelineDocument {
  return {
    schemaVersion: 1,
    tracks: [
      { id: 'trk_video', kind: 'video', index: 0, muted: false, locked: false, clips: media },
      ...(text.length
        ? [{ id: 'trk_text', kind: 'text' as const, index: 1, muted: false, locked: false, clips: text }]
        : []),
    ],
  } as TimelineDocument
}

/** Apply and assert the result is still a document the server accepts. */
function apply(document: TimelineDocument, settings: TemplateSettings): TimelineDocument {
  const next = produce(document, (draft) => applyTemplate(draft, settings))
  expect(violatedInvariants(next)).toEqual([])
  return next
}

const media = (document: TimelineDocument) => trackOfKind(document, 'video')?.clips ?? []

// --------------------------------------------------------------------------

describe('reading settings out of a project', () => {
  it('records the version, so an old template is recognisable as old', () => {
    expect(extractTemplate(timeline()).version).toBe(TEMPLATE_VERSION)
  })

  it('takes the commonest text style, not the first', () => {
    // A project where four titles are white and one was turned red by accident
    // should save white. "The first clip" is an arbitrary pick that would save
    // whichever happens to sit at zero.
    const settings = extractTemplate(
      timeline(
        [],
        [
          title({ id: 'clp_1', style: { color: '#ff0000', fontSize: 48 } as never }),
          title({ id: 'clp_2', style: { color: '#00ff00', fontSize: 64 } as never }),
          title({ id: 'clp_3', style: { color: '#00ff00', fontSize: 64 } as never }),
        ],
      ),
    )
    expect(settings.textStyle?.color).toBe('#00ff00')
    expect(settings.textStyle?.fontSize).toBe(64)
  })

  it('takes the commonest grade', () => {
    const settings = extractTemplate(
      timeline([
        clip({ id: 'clp_a', effects: [{ type: 'color_grade', lut: 'warm_film', strength: 0.5 }] as never }),
        clip({ id: 'clp_b', startMs: 10_000, effects: [{ type: 'color_grade', lut: 'cool_clean', strength: 0.8 }] as never }),
        clip({ id: 'clp_c', startMs: 20_000, effects: [{ type: 'color_grade', lut: 'cool_clean', strength: 0.8 }] as never }),
      ]),
    )
    expect(settings.grade).toEqual({ lut: 'cool_clean', strength: 0.8 })
  })

  it('records nothing from an empty project rather than inventing defaults', () => {
    // A template of nothing is worse than no template: it appears in the list,
    // it can be applied, and applying it does nothing — which reads as broken.
    const settings = extractTemplate(timeline())
    expect(isEmpty(settings)).toBe(true)
    expect(settings.grade).toBeUndefined()
    expect(settings.transition).toBeUndefined()
  })

  it('knows a template with anything in it is not empty', () => {
    expect(isEmpty({ version: 1, grade: { lut: 'warm_film', strength: 0.4 } })).toBe(false)
  })
})

describe('applying settings to another project', () => {
  it('puts the grade on every clip', () => {
    const document = apply(
      timeline([clip({ id: 'clp_a' }), clip({ id: 'clp_b', startMs: 10_000 })]),
      { version: 1, grade: { lut: 'warm_film', strength: 0.6 } },
    )
    for (const found of media(document)) {
      expect(found.effects).toContainEqual({
        type: 'color_grade',
        lut: 'warm_film',
        strength: 0.6,
        sourceJobId: null,
      })
    }
  })

  it('replaces a grade rather than stacking a second one', () => {
    const document = apply(
      timeline([clip({ effects: [{ type: 'color_grade', lut: 'cool_clean', strength: 1 }] as never })]),
      { version: 1, grade: { lut: 'warm_film', strength: 0.6 } },
    )
    const effects = media(document)[0]?.effects ?? []
    expect(effects.filter((effect) => effect.type === 'color_grade')).toHaveLength(1)
  })

  it('restyles every title', () => {
    const document = apply(timeline([], [title(), title({ id: 'clp_t2', startMs: 3_000 })]), {
      version: 1,
      textStyle: { color: '#ffcc00', fontSize: 72 },
    })
    const texts = trackOfKind(document, 'text')?.clips ?? []
    expect(texts).toHaveLength(2)
    for (const text of texts) {
      expect(text.style?.color).toBe('#ffcc00')
      expect(text.style?.fontSize).toBe(72)
    }
  })

  it('keeps style fields the template does not mention', () => {
    // A template that saved only a colour must not silently reset the stroke.
    const document = apply(timeline([], [title()]), {
      version: 1,
      textStyle: { color: '#ffcc00' },
    })
    const text = (trackOfKind(document, 'text')?.clips ?? [])[0]
    expect(text?.style?.strokeWidth).toBe(3)
  })

  it('clamps a transition too long for the clips it lands between', () => {
    // ⚠️ Timeline invariant 7: a transition is at most half the shorter of the
    // two clips it joins. A template saved from a project of ten-second clips
    // and applied to one-second clips would otherwise produce a document the
    // server rejects on the next autosave — losing the edit the user just made.
    const document = apply(
      timeline([
        clip({ id: 'clp_a', durationMs: 1_000 }),
        clip({ id: 'clp_b', startMs: 1_000, durationMs: 1_000 }),
      ]),
      { version: 1, transition: { type: 'dissolve', durationMs: 2_000 } },
    )
    const first = media(document)[0]
    expect(first?.transitionOut?.durationMs).toBeLessThanOrEqual(500)
  })

  it('leaves the last clip without a transition out', () => {
    // There is nothing to dissolve into, and a transition off the end of the
    // timeline is a fade to black nobody asked for.
    const document = apply(
      timeline([
        clip({ id: 'clp_a', durationMs: 10_000 }),
        clip({ id: 'clp_b', startMs: 10_000, durationMs: 10_000 }),
      ]),
      { version: 1, transition: { type: 'dissolve', durationMs: 400 } },
    )
    const found = media(document)
    expect(found[0]?.transitionOut?.durationMs).toBe(400)
    // Falsy rather than `null`: a clip that never had one carries `undefined`,
    // and both mean the same thing to the renderer.
    expect(found[1]?.transitionOut ?? null).toBeNull()
  })

  it('does nothing to a project with nothing in it', () => {
    const document = apply(timeline(), {
      version: 1,
      grade: { lut: 'warm_film', strength: 0.6 },
      transition: { type: 'dissolve', durationMs: 400 },
    })
    expect(media(document)).toHaveLength(0)
  })
})

describe('a round trip', () => {
  it('reads back what it wrote', () => {
    const source = timeline(
      [
        clip({ id: 'clp_a', effects: [{ type: 'color_grade', lut: 'warm_film', strength: 0.6 }] as never }),
        clip({
          id: 'clp_b',
          startMs: 10_000,
          effects: [{ type: 'color_grade', lut: 'warm_film', strength: 0.6 }] as never,
          transitionIn: { type: 'dissolve', durationMs: 400 },
        }),
      ],
      [title()],
    )
    const settings = extractTemplate(source)

    const target = apply(
      timeline([clip({ id: 'clp_x' }), clip({ id: 'clp_y', startMs: 10_000 })], [title({ id: 'clp_z' })]),
      settings,
    )

    expect(extractTemplate(target).grade).toEqual(settings.grade)
    expect(extractTemplate(target).textStyle).toEqual(settings.textStyle)
  })
})

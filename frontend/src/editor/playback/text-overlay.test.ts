/**
 * The caption overlay's dirty check.
 *
 * The class exists to *avoid* redrawing: clearing and re-filling a 1920×1080 2D
 * canvas every frame costs more than the whole WebGL pass. That optimisation is
 * only correct if the key it compares changes whenever the picture would, and
 * the original key was the list of visible clip ids — right for captions, which
 * are generated once and never touched, and wrong for the one thing M3 added:
 * a title whose entire purpose is to be retyped.
 */

import { describe, expect, it } from 'vitest'

import { TextOverlay } from './text-overlay'
import type { SpikeTextClip } from './timeline'

function stub() {
  const drawn: string[] = []
  const ctx = {
    clearRect: () => {},
    fillText: (text: string) => drawn.push(text),
    strokeText: () => {},
    textAlign: '',
    textBaseline: '',
    lineJoin: '',
    miterLimit: 0,
    font: '',
    lineWidth: 0,
    strokeStyle: '',
    fillStyle: '',
  }
  const canvas = {
    width: 1280,
    height: 720,
    getContext: () => ctx,
  } as unknown as HTMLCanvasElement
  return { overlay: new TextOverlay(canvas), ctx, drawn }
}

const title = (over: Partial<SpikeTextClip> = {}): SpikeTextClip => ({
  id: 'clp_t1',
  startMs: 0,
  durationMs: 3_000,
  text: 'Hello',
  emphasis: 0,
  position: { x: 0.5, y: 0.82 },
  fontSize: 0.062,
  ...over,
})

describe('TextOverlay', () => {
  it('draws the visible words, then holds still', () => {
    const { overlay, drawn } = stub()
    expect(overlay.render([title()], 1_000)).toBe(true)
    expect(drawn).toEqual(['Hello'])
    // Sixty frames a second, one draw.
    expect(overlay.render([title()], 1_050)).toBe(false)
    expect(overlay.render([title()], 1_100)).toBe(false)
    expect(drawn).toEqual(['Hello'])
  })

  it('redraws when the words change under the same id', () => {
    const { overlay, drawn } = stub()
    overlay.render([title()], 1_000)
    expect(overlay.render([title({ text: 'Hello there' })], 1_000)).toBe(true)
    expect(drawn).toEqual(['Hello', 'Hello there'])
  })

  it('redraws when the title is moved or resized', () => {
    const { overlay } = stub()
    overlay.render([title()], 1_000)
    expect(overlay.render([title({ position: { x: 0.5, y: 0.2 } })], 1_000)).toBe(true)
    expect(overlay.render([title({ position: { x: 0.5, y: 0.2 }, fontSize: 0.1 })], 1_000)).toBe(
      true,
    )
  })

  it('clears when the clip goes out of range', () => {
    const { overlay } = stub()
    overlay.render([title()], 1_000)
    expect(overlay.render([title()], 4_000)).toBe(true)
    expect(overlay.render([title()], 4_500)).toBe(false)
  })

  it('honours the anchor the document carries', () => {
    const { overlay, ctx } = stub()
    overlay.render([title({ anchor: 'left' })], 1_000)
    expect(ctx.textAlign).toBe('left')
  })

  it('counts its redraws, which is what the frame budget is measured against', () => {
    const { overlay } = stub()
    overlay.render([title()], 1_000)
    overlay.render([title()], 1_016)
    overlay.render([title({ text: 'next' })], 1_033)
    expect(overlay.redrawCount).toBe(2)
  })
})

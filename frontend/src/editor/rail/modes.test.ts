/**
 * The rail's shape.
 *
 * M4.5 item 4's claim is that **the rail grows by one icon per tool and the
 * inspector's job never changes shape** — which only holds if adding a mode
 * really is one entry in one list. These tests are what makes that true rather
 * than intended: a mode added to the union and forgotten in `MODES`, or added
 * to `MODES` with no glyph, fails here rather than rendering an empty button.
 */

import { describe, expect, it } from 'vitest'

import { DEFAULT_MODE, MODE_IDS, MODES, modeById, needsSelection } from '@/editor/rail/modes'

describe('the mode list', () => {
  it('has an entry for every id, and no more', () => {
    expect(MODES.map((mode) => mode.id)).toEqual([...MODE_IDS])
  })

  it('opens on a mode that works with nothing selected', () => {
    // A rail whose first mode says "select a clip first" on a new project is a
    // rail that greets every new user with an error.
    expect(needsSelection(DEFAULT_MODE)).toBe(false)
  })

  it('gives every mode a label and a hint', () => {
    // The hint is the accessible name on an icon-only control — charter §14.
    for (const mode of MODES) {
      expect(mode.label.length).toBeGreaterThan(0)
      expect(mode.hint.length).toBeGreaterThan(10)
    }
  })

  it('marks exactly the modes that spend credits', () => {
    // Charter rule 5: a user must never learn what a button costs by pressing
    // it. Colour is *not* in this list — grading by hand is free, and only the
    // "suggest a look" button inside it starts a job.
    expect(MODES.filter((mode) => mode.costsCredits).map((mode) => mode.id)).toEqual([
      'captions',
      'trim',
    ])
  })

  it('resolves every id', () => {
    for (const id of MODE_IDS) expect(modeById(id).id).toBe(id)
  })

  it('throws on an id that is not in the list', () => {
    // The failure mode this replaces is a blank panel with no clue why.
    // @ts-expect-error deliberately outside the union
    expect(() => modeById('nope')).toThrow(/unknown rail mode/)
  })
})

describe('needsSelection', () => {
  it('is false for the modes that stand alone', () => {
    expect(needsSelection('media')).toBe(false)
    expect(needsSelection('titles')).toBe(false)
  })

  it('is true for the ones that act on a clip', () => {
    expect(needsSelection('audio')).toBe(true)
    expect(needsSelection('colour')).toBe(true)
    expect(needsSelection('captions')).toBe(true)
    expect(needsSelection('trim')).toBe(true)
  })
})

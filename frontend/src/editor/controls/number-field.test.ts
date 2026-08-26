/**
 * What a typed value has to survive before it reaches the document.
 *
 * M4.5 item 5 added typed input to every numeric control, and the interesting
 * cases are all the ones a slider never produced: empty strings, half-typed
 * numbers, a comma for a decimal point, and floating-point steps that do not
 * land where arithmetic says they should.
 */

import { describe, expect, it } from 'vitest'

import {
  clamp,
  decimalsFor,
  displayRange,
  parseNumericInput,
  snapToStep,
  toDisplay,
  toDocument,
} from '@/editor/controls/number-field'

const VOLUME = { min: 0, max: 2, step: 0.05 }
const SPEED = { min: 0.25, max: 4, step: 0.05 }
const FADE = { min: 0, max: 5_000, step: 50 }

describe('parseNumericInput', () => {
  it('reads an ordinary number', () => {
    expect(parseNumericInput('1.25', SPEED)).toBe(1.25)
  })

  it('accepts a comma as the decimal separator', () => {
    // A French or Hindi keyboard gives this, and the product accepts both those
    // languages for captions, so it will see it.
    expect(parseNumericInput('1,25', SPEED)).toBe(1.25)
  })

  it('ignores surrounding space and a leading plus', () => {
    expect(parseNumericInput('  +1.5 ', SPEED)).toBe(1.5)
  })

  it('clamps to the range rather than refusing', () => {
    expect(parseNumericInput('9', SPEED)).toBe(4)
    expect(parseNumericInput('-3', VOLUME)).toBe(0)
  })

  it.each(['', '   ', '-', '+', '.', 'abc', '1.2x', '1 2', '1e5', 'NaN', 'Infinity'])(
    'refuses %o rather than inventing a number',
    (raw) => {
      expect(parseNumericInput(raw, VOLUME)).toBeNull()
    },
  )

  it('refuses a partial number instead of taking the numeric prefix', () => {
    // `parseFloat('1.2x')` is 1.2. Taking it would leave the user with a value
    // they did not mean and no sign that anything was dropped — and for volume,
    // a wrong number is a clip that plays at the wrong level rather than an
    // obvious error.
    expect(parseNumericInput('1.2x', VOLUME)).toBeNull()
  })

  it('snaps what it accepts to the step', () => {
    expect(parseNumericInput('0.77', VOLUME)).toBe(0.75)
    expect(parseNumericInput('137', FADE)).toBe(150)
  })
})

describe('snapToStep', () => {
  it('measures from min, not from zero', () => {
    // Speed starts at 0.25 with a step of 0.05, so the valid values are 0.25,
    // 0.30, 0.35 … Snapping from zero agrees whenever min is a whole multiple
    // of step, which is most ranges — which is exactly why getting this wrong
    // survives casual testing.
    expect(snapToStep(0.28, { min: 0.25, max: 4, step: 0.05 })).toBe(0.3)
    expect(snapToStep(0.26, { min: 0.25, max: 4, step: 0.05 })).toBe(0.25)
  })

  it('leaves a value that is already on a step alone', () => {
    expect(snapToStep(1.25, SPEED)).toBe(1.25)
    expect(snapToStep(0, VOLUME)).toBe(0)
  })

  it('does not leak floating-point noise into the document', () => {
    // 0.1 + 0.2 is the reason. A speed of 1.3000000000000003 in the timeline
    // document is a value somebody will eventually be shown.
    const result = snapToStep(1.3, SPEED)
    expect(result).toBe(1.3)
    expect(String(result)).toBe('1.3')
  })

  it('never produces negative zero', () => {
    // `-0` is valid JSON and reads as a mistake to anyone looking at the file.
    expect(Object.is(snapToStep(-0.01, VOLUME), -0)).toBe(false)
  })

  it('is a no-op when the step is not usable', () => {
    expect(snapToStep(1.234, { min: 0, max: 2, step: 0 })).toBe(1.234)
  })
})

describe('decimalsFor', () => {
  it('reads the precision off the step', () => {
    expect(decimalsFor(1)).toBe(0)
    expect(decimalsFor(0.05)).toBe(2)
    expect(decimalsFor(0.001)).toBe(3)
  })

  it('handles a step JavaScript prints in exponent form', () => {
    // `String(0.0000001)` is '1e-7'. Counting characters after a dot that is
    // not there would return 0 and round the step away.
    expect(decimalsFor(1e-7)).toBe(7)
  })
})

describe('clamp', () => {
  it('holds both ends', () => {
    expect(clamp(5, 0, 2)).toBe(2)
    expect(clamp(-5, 0, 2)).toBe(0)
    expect(clamp(1, 0, 2)).toBe(1)
  })
})

describe('display units', () => {
  const STRENGTH = { min: 0, max: 1, step: 0.01 }

  it('shows a 0-1 value as a percentage', () => {
    expect(toDisplay(0.66, 100)).toBe(66)
  })

  it('reads a typed percentage back as a 0-1 value', () => {
    // The regression this exists for. The field showed `66 %`; typing `42` —
    // which is what anybody reading a percentage types — used to parse as 42,
    // clamp to the maximum of 1, and set the grade to *full* strength without
    // saying so.
    expect(toDocument(42, 100, 0.01)).toBe(0.42)
  })

  it('does not leak division noise into the document', () => {
    // 4.2 / 100 is 0.042000000000000003 in IEEE 754, and that is the number
    // that would be written to the timeline and sent to the server.
    expect(toDocument(4.2, 100, 0.01)).toBe(0.04)
    expect(String(toDocument(4.2, 100, 0.01))).toBe('0.04')
  })

  it('is an exact round trip at every step of a percentage range', () => {
    for (let percent = 0; percent <= 100; percent++) {
      expect(toDisplay(toDocument(percent, 100, 0.01), 100, 1)).toBe(percent)
    }
  })

  it('leaves a field that is already in its own unit untouched', () => {
    // Milliseconds and a speed multiplier are shown as themselves, and the
    // default scale must cost nothing and change nothing.
    expect(toDisplay(1.25, 1)).toBe(1.25)
    expect(toDocument(250, 1, 50)).toBe(250)
    expect(displayRange({ min: 0, max: 5000, step: 50 }, 1)).toEqual({
      min: 0,
      max: 5000,
      step: 50,
    })
  })

  it('scales the whole range, so the slider and the field agree', () => {
    // A track still running 0-1 under a field showing 0-100 is a handle that
    // jumps to the end on the first drag.
    expect(displayRange(STRENGTH, 100)).toEqual({ min: 0, max: 100, step: 1 })
  })

  it('parses against the scaled range, not the stored one', () => {
    // `42` is out of range for 0-1 and in range for 0-100. Parsing against the
    // wrong one is exactly how the bug clamped to full strength.
    expect(parseNumericInput('42', displayRange(STRENGTH, 100))).toBe(42)
    expect(parseNumericInput('42', STRENGTH)).toBe(1)
  })
})

/**
 * Autosave, on fake timers.
 *
 * The whole state machine is here rather than in a React hook precisely so it
 * can be tested like this: no network, no browser, no component tree. The
 * failures being guarded against are all timing ones, and none of them would
 * show up in a manual click-through.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AUTOSAVE_DELAY_MS, Autosave, asConflict } from './autosave'
import { emptyTimeline, type TimelineDocument } from './timeline-document'

interface World {
  projectId: string
  timeline: TimelineDocument
  version: number
  isDirty: boolean
  isDragging: boolean
}

function harness(overrides: Partial<World> = {}) {
  const world: World = {
    projectId: 'prj_1',
    timeline: emptyTimeline(),
    version: 3,
    isDirty: true,
    isDragging: false,
    ...overrides,
  }
  const saved: number[] = []
  const savedSnapshots: TimelineDocument[] = []
  const conflicts: number[] = []
  const errors: unknown[] = []

  let resolveSave: ((version: number) => void) | null = null
  let rejectSave: ((error: unknown) => void) | null = null

  const save = vi.fn(
    (input: { version: number }) =>
      new Promise<{ version: number }>((resolve, reject) => {
        resolveSave = (version) => resolve({ version })
        rejectSave = reject
        void input
      }),
  )

  const autosave = new Autosave({
    read: () => world,
    save,
    onSaved: (version, snapshot) => {
      world.version = version
      world.isDirty = false
      saved.push(version)
      savedSnapshots.push(snapshot)
    },
    onConflict: (version) => conflicts.push(version),
    onError: (error) => errors.push(error),
  })

  return {
    world,
    autosave,
    save,
    saved,
    savedSnapshots,
    conflicts,
    errors,
    settle: async (version = 4) => {
      resolveSave?.(version)
      await vi.advanceTimersByTimeAsync(0)
    },
    fail: async (error: unknown) => {
      rejectSave?.(error)
      await vi.advanceTimersByTimeAsync(0)
    },
  }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('scheduling', () => {
  it('waits the debounce before saving', async () => {
    const h = harness()
    h.autosave.schedule()

    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS - 1)
    expect(h.save).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1)
    expect(h.save).toHaveBeenCalledTimes(1)
  })

  it('coalesces a burst of edits into one save', async () => {
    // A drag that commits on every drop, or a keyboard nudge held down.
    const h = harness()
    for (let index = 0; index < 20; index += 1) {
      h.autosave.schedule()
      await vi.advanceTimersByTimeAsync(100)
    }
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS)
    expect(h.save).toHaveBeenCalledTimes(1)
  })

  it('does not save a document that is not dirty', async () => {
    const h = harness({ isDirty: false })
    h.autosave.schedule()
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS)
    expect(h.save).not.toHaveBeenCalled()
  })
})

describe('the three rules that are about timing', () => {
  it('never saves mid-drag, and saves once the gesture ends', async () => {
    // The document does not contain the gesture yet, so a save during one
    // stores the position the clip was dragged *from*.
    const h = harness({ isDragging: true })
    h.autosave.schedule()
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS)
    expect(h.save).not.toHaveBeenCalled()

    h.world.isDragging = false
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS)
    expect(h.save).toHaveBeenCalledTimes(1)
  })

  it('keeps one save in flight and follows it with another', async () => {
    // Two overlapping PATCHes carry the same version, so the second is
    // guaranteed a 409 against the first — the client would manufacture its
    // own conflicts.
    const h = harness()
    h.autosave.schedule()
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS)
    expect(h.save).toHaveBeenCalledTimes(1)
    expect(h.autosave.isSaving).toBe(true)

    h.autosave.schedule()
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS)
    expect(h.save).toHaveBeenCalledTimes(1)

    await h.settle(4)
    expect(h.saved).toEqual([4])

    h.world.isDirty = true
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS)
    expect(h.save).toHaveBeenCalledTimes(2)
  })

  it('a conflict stops the loop until it is resolved', async () => {
    const h = harness()
    h.autosave.schedule()
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS)
    await h.fail({ code: 'VERSION_CONFLICT', details: { currentVersion: 14 } })

    expect(h.conflicts).toEqual([14])
    expect(h.autosave.isBlocked).toBe(true)

    // No retry: there is no automatic merge, and hammering the endpoint with a
    // document the server has already rejected helps nobody.
    h.autosave.schedule()
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS * 5)
    expect(h.save).toHaveBeenCalledTimes(1)

    h.autosave.resume()
    h.autosave.schedule()
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS)
    expect(h.save).toHaveBeenCalledTimes(2)
  })
})

describe('what comes back to the caller', () => {
  it('hands back the snapshot the request carried, not the current document', async () => {
    // The caller compares it by reference to decide whether an edit landed
    // while the save was in flight. Without it, that edit is stranded.
    const h = harness()
    const sent = h.world.timeline
    h.autosave.schedule()
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS)

    h.world.timeline = { schemaVersion: 1, tracks: [] }
    await h.settle(4)

    expect(h.savedSnapshots[0]).toBe(sent)
    expect(h.savedSnapshots[0]).not.toBe(h.world.timeline)
  })
})

describe('failures that are not conflicts', () => {
  it('reports and leaves the document dirty for the next change', async () => {
    const h = harness()
    h.autosave.schedule()
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS)
    await h.fail(new Error('offline'))

    expect(h.errors).toHaveLength(1)
    expect(h.world.isDirty).toBe(true)
    expect(h.autosave.isBlocked).toBe(false)
  })
})

describe('flush', () => {
  it('saves immediately instead of waiting out the debounce', async () => {
    // Blur, tab hidden, laptop lid closed.
    const h = harness()
    h.autosave.schedule()
    const flushed = h.autosave.flush()
    await vi.advanceTimersByTimeAsync(0)
    expect(h.save).toHaveBeenCalledTimes(1)
    await h.settle(4)
    await flushed
  })

  it('does nothing when there is nothing to save', async () => {
    const h = harness({ isDirty: false })
    await h.autosave.flush()
    expect(h.save).not.toHaveBeenCalled()
  })
})

describe('stop', () => {
  it('cancels a pending save when the editor closes', async () => {
    const h = harness()
    h.autosave.schedule()
    h.autosave.stop()
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS * 2)
    expect(h.save).not.toHaveBeenCalled()
  })
})

describe('asConflict', () => {
  it('recognises the contract shape and nothing else', () => {
    expect(asConflict({ code: 'VERSION_CONFLICT', details: { currentVersion: 9 } })).toBe(9)
    expect(asConflict({ code: 'VERSION_CONFLICT' })).toBe(0)
    expect(asConflict({ code: 'RATE_LIMITED' })).toBeNull()
    expect(asConflict(new Error('nope'))).toBeNull()
  })
})

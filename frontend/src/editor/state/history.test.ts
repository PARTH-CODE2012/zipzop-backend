import { describe, expect, it } from 'vitest'

import {
  HISTORY_LIMIT,
  canRedo,
  canUndo,
  commit,
  emptyHistory,
  redo,
  undo,
  undoLabel,
} from './history'

interface Doc {
  count: number
  items: string[]
}

const doc = (): Doc => ({ count: 0, items: [] })

describe('commit', () => {
  it('records an entry and returns the new state', () => {
    const first = commit(doc(), emptyHistory(), 'Increment', (draft) => {
      draft.count = 1
    })

    expect(first.changed).toBe(true)
    expect(first.state.count).toBe(1)
    expect(first.history.past).toHaveLength(1)
    expect(undoLabel(first.history)).toBe('Increment')
  })

  it('records nothing when the recipe changes nothing', () => {
    // A drag that ends where it started, or a property set to the value it
    // already had. An undo step here would appear to do nothing, and it would
    // queue an autosave for an unchanged document.
    const before = doc()
    const result = commit(before, emptyHistory(), 'No-op', (draft) => {
      draft.count = 0
    })

    expect(result.changed).toBe(false)
    expect(result.state).toBe(before)
    expect(result.history.past).toHaveLength(0)
  })

  it('leaves the previous state untouched', () => {
    const before = doc()
    const after = commit(before, emptyHistory(), 'Add', (draft) => {
      draft.items.push('a')
    })

    expect(before.items).toEqual([])
    expect(after.state.items).toEqual(['a'])
  })

  it('caps the stack and drops the oldest entry', () => {
    let state = doc()
    let history = emptyHistory()
    for (let index = 0; index < HISTORY_LIMIT + 20; index += 1) {
      const result = commit(state, history, `Step ${index}`, (draft) => {
        draft.count = index + 1
      })
      state = result.state
      history = result.history
    }

    expect(history.past).toHaveLength(HISTORY_LIMIT)
    expect(undoLabel(history)).toBe(`Step ${HISTORY_LIMIT + 19}`)
  })
})

describe('undo and redo', () => {
  it('round-trips a change', () => {
    const start = doc()
    const first = commit(start, emptyHistory(), 'Add', (draft) => {
      draft.items.push('a')
    })

    const undone = undo(first.state, first.history)
    expect(undone.state.items).toEqual([])
    expect(canUndo(undone.history)).toBe(false)
    expect(canRedo(undone.history)).toBe(true)

    const redone = redo(undone.state, undone.history)
    expect(redone.state.items).toEqual(['a'])
    expect(canRedo(redone.history)).toBe(false)
  })

  it('takes a large change back in one step', () => {
    // The property M4 depends on: captions land one clip per word, and one
    // undo has to take all eighteen hundred of them back out.
    const first = commit(doc(), emptyHistory(), 'Captions', (draft) => {
      for (let index = 0; index < 1_800; index += 1) draft.items.push(`w${index}`)
    })
    expect(first.history.past).toHaveLength(1)

    const undone = undo(first.state, first.history)
    expect(undone.state.items).toEqual([])
  })

  it('clears the redo stack on a new commit', () => {
    // A patch recorded against a document that has since diverged cannot be
    // replayed onto it.
    const first = commit(doc(), emptyHistory(), 'A', (draft) => {
      draft.items.push('a')
    })
    const undone = undo(first.state, first.history)
    expect(canRedo(undone.history)).toBe(true)

    const diverged = commit(undone.state, undone.history, 'B', (draft) => {
      draft.items.push('b')
    })
    expect(canRedo(diverged.history)).toBe(false)
  })

  it('is a no-op on an empty stack', () => {
    const state = doc()
    const history = emptyHistory()
    expect(undo(state, history).changed).toBe(false)
    expect(redo(state, history).changed).toBe(false)
  })
})

/**
 * Undo and redo, built from Immer patches.
 *
 * `docs/04-frontend-architecture.md` §3.2: undo is **not** hand-written
 * inverses. Every edit goes through `commit(label, recipe)`, which runs the
 * recipe through `produceWithPatches` and keeps the forward and inverse patch
 * sets Immer produces. An inverse written by hand has to be updated every time
 * the forward operation changes, and the day it is not is the day undo
 * silently corrupts a document.
 *
 * The other property this buys is the one M4 needs: **a commit is one undo
 * step regardless of how much it changed.** Captions land eighteen hundred
 * text clips in a single recipe, and one `⌘Z` takes all of them back out.
 */

import { applyPatches, enablePatches, produceWithPatches, type Draft, type Patch } from 'immer'

// Patch recording is opt-in and costs nothing until a producer is run with it.
enablePatches()

/** Capped so a long session cannot grow without bound (PHASE1-TASKS, M3). */
export const HISTORY_LIMIT = 200

export interface HistoryEntry {
  /** Shown in the interface — "Split clip", "Captions", "Move clip". */
  label: string
  forward: Patch[]
  inverse: Patch[]
}

export interface History {
  past: HistoryEntry[]
  future: HistoryEntry[]
}

export function emptyHistory(): History {
  return { past: [], future: [] }
}

export interface CommitResult<T> {
  state: T
  history: History
  /**
   * False when the recipe changed nothing.
   *
   * This matters more than it looks. A drag that ends a pixel from where it
   * started, or a property set to the value it already had, produces no
   * patches — and must therefore produce no undo step and must not mark the
   * document dirty. Otherwise a user who nudges a clip and puts it back has an
   * autosave queued and an undo that appears to do nothing.
   */
  changed: boolean
}

/**
 * Apply a recipe and record it.
 *
 * The redo stack is cleared, which is the conventional behaviour and the only
 * one that is safe: a patch recorded against a document that has since
 * diverged cannot be replayed onto it.
 */
export function commit<T extends object>(
  state: T,
  history: History,
  label: string,
  recipe: (draft: Draft<T>) => void,
): CommitResult<T> {
  const [next, forward, inverse] = produceWithPatches(state, recipe)
  if (forward.length === 0) {
    return { state, history, changed: false }
  }

  const past = [...history.past, { label, forward, inverse }]
  return {
    state: next,
    history: {
      past: past.length > HISTORY_LIMIT ? past.slice(past.length - HISTORY_LIMIT) : past,
      future: [],
    },
    changed: true,
  }
}

export function canUndo(history: History): boolean {
  return history.past.length > 0
}

export function canRedo(history: History): boolean {
  return history.future.length > 0
}

export function undoLabel(history: History): string | null {
  return history.past.at(-1)?.label ?? null
}

export function redoLabel(history: History): string | null {
  return history.future.at(-1)?.label ?? null
}

export function undo<T extends object>(state: T, history: History): CommitResult<T> {
  const entry = history.past.at(-1)
  if (!entry) return { state, history, changed: false }
  return {
    // `applyPatches` is typed against Immer's own `Objectish`, which no
    // generic parameter here can satisfy structurally. The cast is at the one
    // boundary rather than pushed onto every caller.
    state: applyPatches(state as never, entry.inverse) as T,
    history: { past: history.past.slice(0, -1), future: [...history.future, entry] },
    changed: true,
  }
}

export function redo<T extends object>(state: T, history: History): CommitResult<T> {
  const entry = history.future.at(-1)
  if (!entry) return { state, history, changed: false }
  return {
    state: applyPatches(state as never, entry.forward) as T,
    history: { past: [...history.past, entry], future: history.future.slice(0, -1) },
    changed: true,
  }
}

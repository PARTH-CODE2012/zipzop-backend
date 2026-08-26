'use client'

/**
 * Which mode the left rail is showing.
 *
 * A store rather than `useState` in the workspace, for one reason that is not
 * prop drilling: **other things need to change the mode.** Selecting a caption
 * clip should be able to bring up the text controls, and a tool finishing should
 * be able to show its result — neither of those is a child of the rail, and
 * neither should have to be handed a setter through three components to say so.
 *
 * Deliberately **not** part of the editor store. Nothing here is document state:
 * it does not commit, it is not undoable, it does not mark the project dirty,
 * and it must never end up in a patch. Keeping it in its own store is what makes
 * that impossible rather than merely unlikely.
 */

import { create } from 'zustand'

import { DEFAULT_MODE, type ModeId } from '@/editor/rail/modes'

interface RailState {
  mode: ModeId
  setMode: (mode: ModeId) => void
}

export const useRail = create<RailState>((set) => ({
  mode: DEFAULT_MODE,
  setMode: (mode) => set({ mode }),
}))

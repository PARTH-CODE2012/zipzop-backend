/**
 * The left rail's modes.
 *
 * M4.5 item 4, decided 22 August: *"the left panel becomes a mode rail, the
 * right panel stays the inspector and nothing else"*.
 *
 * The problem it solves is not that the media panel looked empty. It is that
 * the right side was **two components stacked** — an inspector with a fixed
 * height and a tools panel with a fixed width — and phase 2 adds four more
 * tools (face mapping, lip sync, denoise, dereverb) to a structure that was
 * already full. Stacking a third block is how a panel becomes a scrolling list
 * of everything.
 *
 * The property this shape has and the old one did not: **the rail grows by one
 * icon per tool, and the inspector's job never changes shape.**
 *
 * This module is the list and nothing else — no React, no state. It is
 * separated so that "phase 2 adds a mode" is one entry here plus one panel
 * component, and so a test can assert the shape of the rail without mounting
 * the editor.
 */

/** Every mode the rail can show. Order is the order they appear, top to bottom. */
export const MODE_IDS = ['media', 'titles', 'audio', 'colour', 'captions', 'trim'] as const

export type ModeId = (typeof MODE_IDS)[number]

export interface Mode {
  id: ModeId
  /** Shown under the icon, and as the panel's heading. Sentence case. */
  label: string
  /**
   * What the mode is for, in the words a user would use. Shown on hover and as
   * the accessible name — charter §14 requires an accessible name on an
   * icon-only control, and a mode rail is nothing but icon-only controls.
   */
  hint: string
  /**
   * True for the modes that start server work and spend credits.
   *
   * Charter rule 5: *a user must never learn what a button costs by pressing
   * it.* The rail tints these differently for the same reason the old tools
   * panel was visually separate from the toolbar — the distinction survived the
   * move, because it was never about where the controls were.
   */
  costsCredits: boolean
}

export const MODES: readonly Mode[] = [
  {
    id: 'media',
    label: 'Media',
    hint: 'Your uploaded video, audio and images',
    costsCredits: false,
  },
  {
    id: 'titles',
    label: 'Titles',
    hint: 'Add a title or an overlay to the text track',
    costsCredits: false,
  },
  {
    id: 'audio',
    label: 'Audio',
    hint: 'Volume and fades for the selected clip',
    costsCredits: false,
  },
  {
    id: 'colour',
    label: 'Colour',
    hint: 'Grade the selected clip by hand, from the five shipped looks',
    costsCredits: false,
  },
  {
    id: 'captions',
    label: 'Captions',
    hint: 'Transcribe the speech and put every word on the text track',
    costsCredits: true,
  },
  {
    id: 'trim',
    label: 'Smart trim',
    hint: 'Find silences, filler words and repeated takes',
    costsCredits: true,
  },
] as const

export const DEFAULT_MODE: ModeId = 'media'

export function modeById(id: ModeId): Mode {
  // Non-null: `ModeId` is derived from `MODE_IDS`, so the only way to reach
  // this with something absent is a mode added to the union and not to the
  // list — which the test in `modes.test.ts` catches.
  const found = MODES.find((mode) => mode.id === id)
  if (!found) throw new Error(`unknown rail mode: ${id}`)
  return found
}

/**
 * Which modes need a selected clip before they can do anything.
 *
 * Used to put a sentence in the panel rather than leaving a set of dead
 * controls: *"pick a clip first"* is a better empty state than four sliders
 * that silently write nowhere. **Not** used to hide the mode — a mode that
 * disappears when nothing is selected is a mode nobody finds.
 */
export function needsSelection(id: ModeId): boolean {
  return id !== 'media' && id !== 'titles'
}

/**
 * The timeline's height, and the rules it may not break.
 *
 * M4.5 item 7: *"a timeline whose height the user cannot change is a limitation
 * people notice quickly on a long project"*. Making it draggable is easy; making
 * it draggable **safely** is this file, and it is separated from the component
 * for the usual reason — a constraint enforced inside a pointer handler is a
 * constraint that only holds while that handler is the way in.
 *
 * Two bounds, and they are not the same kind of thing:
 *
 * * the timeline may not become **too small to use** — below three lanes and a
 *   ruler it is a strip that shows nothing;
 * * the timeline may not grow so far that the **picture** is squeezed out. The
 *   preview is what the user is editing against, and a layout that can hide it
 *   entirely is a layout with a state nobody can recover from without knowing
 *   to drag a divider they can no longer see.
 *
 * The second bound is why this takes the viewport height rather than a constant:
 * 320 px of timeline is comfortable on a desktop monitor and is the whole window
 * on a laptop in a split screen.
 */

/** Ruler plus three lanes plus the header strip. Below this it shows nothing. */
export const MIN_TIMELINE_PX = 140

/** What the picture and the transport under it need to stay usable. */
export const MIN_STAGE_PX = 220

/** Where a fresh editor opens. Matches the fixed height M3 shipped with. */
export const DEFAULT_TIMELINE_PX = 192

/**
 * The height the timeline may actually take, given what the window has.
 *
 * **The floor wins when the window cannot satisfy both.** On a very short
 * viewport the two minimums do not fit together, and something has to give;
 * giving it to the timeline keeps the picture on screen, which is the half the
 * user cannot work without. The alternative — honouring the requested height
 * and letting the stage collapse — produces an editor with no preview and no
 * obvious way back.
 */
export function clampTimelineHeight(requestedPx: number, viewportPx: number): number {
  // `NaN` and an infinity are not the same kind of broken. `NaN` has no order,
  // so there is nothing to clamp it to and the default is the only honest
  // answer; an infinity *does* have an order, and a function called `clamp`
  // that refuses to clamp the largest possible number is surprising in a way
  // that will be worked around somewhere else.
  if (Number.isNaN(requestedPx)) return DEFAULT_TIMELINE_PX
  const ceiling = Math.max(MIN_TIMELINE_PX, viewportPx - MIN_STAGE_PX)
  return Math.round(Math.min(ceiling, Math.max(MIN_TIMELINE_PX, requestedPx)))
}

/**
 * What a drag of the divider proposes.
 *
 * The divider sits above the timeline, so dragging **up** makes the timeline
 * **taller** — the sign flip is here rather than in the component, where it is
 * the kind of thing that gets inverted once and then compensated for somewhere
 * else.
 */
export function heightFromDrag(startHeightPx: number, deltaY: number): number {
  return startHeightPx - deltaY
}

// --------------------------------------------------------------------------
// Persistence
// --------------------------------------------------------------------------

const STORAGE_KEY = 'zipzop.timelineHeightPx'

/**
 * Remembered across sessions, and deliberately **not** in the timeline document.
 *
 * How tall someone likes their timeline is a property of the person and the
 * screen they are sitting at, not of the project. Putting it in the document
 * would sync it between machines, mark the project dirty on a drag, and put a
 * layout preference into a file the export renderer reads.
 */
export function readStoredHeight(storage: Pick<Storage, 'getItem'> | null): number | null {
  if (!storage) return null
  try {
    const raw = storage.getItem(STORAGE_KEY)
    if (raw === null) return null
    const parsed = Number(raw)
    return Number.isFinite(parsed) ? parsed : null
  } catch {
    // Private browsing and blocked third-party storage both throw on access
    // rather than returning null. A missing preference is not worth an error.
    return null
  }
}

export function writeStoredHeight(
  storage: Pick<Storage, 'setItem'> | null,
  heightPx: number,
): void {
  if (!storage) return
  try {
    storage.setItem(STORAGE_KEY, String(Math.round(heightPx)))
  } catch {
    // Storage full, or blocked. The layout still works for this session.
  }
}

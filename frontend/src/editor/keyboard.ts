/**
 * Keyboard bindings — PHASE1-TASKS M3.
 *
 * Separated from the component so the mapping is a pure function of an event
 * and can be read, and tested, without a DOM. The component's job is to
 * attach it.
 *
 * The rule that matters: **a binding never fires while the user is typing.**
 * Renaming a project and pressing `s` must write an `s`, not split a clip.
 */

export type EditorAction =
  | 'play-pause'
  | 'split'
  | 'delete'
  | 'duplicate'
  | 'undo'
  | 'redo'
  | 'save'
  | 'nudge-left'
  | 'nudge-right'
  | 'go-start'
  | 'go-end'
  | 'cancel'

export interface KeyContext {
  /** True when focus is in a text field — see the rule above. */
  isTyping: boolean
}

/** One frame at 30 fps, the arrow-key step (`docs/02-scope-v1.md` §3.3). */
export const NUDGE_MS = 33

export function actionFor(
  event: Pick<KeyboardEvent, 'key' | 'code' | 'metaKey' | 'ctrlKey' | 'shiftKey'>,
  context: KeyContext,
): EditorAction | null {
  const accel = event.metaKey || event.ctrlKey

  // Escape is the exception: it cancels a drag or clears a selection, and it
  // has to work from anywhere, including out of a field.
  if (event.key === 'Escape') return 'cancel'
  if (context.isTyping) return null

  if (accel && event.key.toLowerCase() === 'z') return event.shiftKey ? 'redo' : 'undo'
  if (accel && event.key.toLowerCase() === 'y') return 'redo'
  if (accel && event.key.toLowerCase() === 's') return 'save'
  if (accel && event.key.toLowerCase() === 'd') return 'duplicate'

  if (event.code === 'Space') return 'play-pause'
  if (event.key.toLowerCase() === 's') return 'split'
  if (event.key === 'Delete' || event.key === 'Backspace') return 'delete'
  if (event.key === 'ArrowLeft') return 'nudge-left'
  if (event.key === 'ArrowRight') return 'nudge-right'
  if (event.key === 'Home') return 'go-start'
  if (event.key === 'End') return 'go-end'
  return null
}

/** Whether an event target is somewhere text is being entered. */
export function isTypingTarget(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null
  if (!element) return false
  if (element.isContentEditable) return true
  return /^(INPUT|TEXTAREA|SELECT)$/.test(element.tagName)
}

'use client'

/**
 * The handle between the picture and the timeline.
 *
 * M4.5 item 7's larger half: *"a timeline whose height the user cannot change
 * is a limitation people notice quickly on a long project"*. Three lanes and a
 * ruler at a fixed 192 px is fine for a two-clip test and cramped the moment
 * captions put eighteen hundred text clips on the third lane.
 *
 * The arithmetic and the bounds are in `split.ts`, tested there. This is the
 * gesture, and it follows the same rule the timeline's own drags follow: the
 * pointer is captured so it cannot be lost over an iframe or outside the
 * window, and `Escape` puts it back where it started.
 *
 * Keyboard-operable, because a divider that only a mouse can move is a layout
 * preference a keyboard user cannot have (charter §14).
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import {
  clampTimelineHeight,
  DEFAULT_TIMELINE_PX,
  heightFromDrag,
  MIN_TIMELINE_PX,
} from '@/editor/layout/split'

const KEYBOARD_STEP_PX = 24

export function TimelineSplitter({
  heightPx,
  viewportPx,
  onResize,
}: {
  heightPx: number
  viewportPx: number
  onResize: (heightPx: number) => void
}) {
  const [dragging, setDragging] = useState(false)
  const origin = useRef<{ y: number; height: number } | null>(null)

  const apply = useCallback(
    (next: number) => onResize(clampTimelineHeight(next, viewportPx)),
    [onResize, viewportPx],
  )

  // On `window` rather than on the handle: a fast drag outpaces the element,
  // and a pointer released over the video element would otherwise never end the
  // gesture — the divider would follow the cursor with no button held.
  useEffect(() => {
    if (!dragging) return

    function move(event: PointerEvent) {
      const from = origin.current
      if (!from) return
      apply(heightFromDrag(from.height, event.clientY - from.y))
    }
    function stop() {
      setDragging(false)
      origin.current = null
    }
    function cancel(event: KeyboardEvent) {
      if (event.key !== 'Escape') return
      const from = origin.current
      if (from) apply(from.height)
      stop()
    }

    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', stop)
    window.addEventListener('pointercancel', stop)
    window.addEventListener('keydown', cancel)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', stop)
      window.removeEventListener('pointercancel', stop)
      window.removeEventListener('keydown', cancel)
    }
  }, [dragging, apply])

  return (
    <div
      role="separator"
      aria-orientation="horizontal"
      aria-label="Resize the timeline"
      aria-valuenow={Math.round(heightPx)}
      aria-valuemin={MIN_TIMELINE_PX}
      aria-valuemax={Math.round(Math.max(MIN_TIMELINE_PX, viewportPx))}
      tabIndex={0}
      onPointerDown={(event) => {
        origin.current = { y: event.clientY, height: heightPx }
        setDragging(true)
        event.preventDefault()
      }}
      onKeyDown={(event) => {
        // Up makes the timeline taller, matching the drag direction.
        if (event.key === 'ArrowUp') apply(heightPx + KEYBOARD_STEP_PX)
        else if (event.key === 'ArrowDown') apply(heightPx - KEYBOARD_STEP_PX)
        else return
        event.preventDefault()
      }}
      // Double-click restores the height a fresh editor opens at — the
      // conventional escape hatch from a divider dragged somewhere useless.
      onDoubleClick={() => apply(DEFAULT_TIMELINE_PX)}
      className="group relative flex h-2 shrink-0 cursor-row-resize items-center justify-center"
      style={{
        background: dragging ? 'var(--color-accent-soft)' : 'transparent',
        transition: 'background var(--duration-micro) ease-out',
      }}
      data-testid="timeline-splitter"
      data-dragging={dragging}
    >
      {/* A grip that is visible without being loud: three pixels of rule,
          brightening on hover and while held. */}
      <span
        aria-hidden="true"
        style={{
          width: 44,
          height: 3,
          borderRadius: 999,
          background: dragging ? 'var(--color-accent)' : 'var(--color-rule)',
        }}
      />
    </div>
  )
}

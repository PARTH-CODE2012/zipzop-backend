'use client'

/**
 * The timeline: ruler, grid, three lanes, and every gesture that edits them.
 *
 * Two rules from `docs/08-ui-charter.md` shape what this is allowed to be.
 *
 * **Rule 2 — blur and translucency stop at the chrome.** The container is
 * chrome and may be translucent; a clip may not. Every clip here is an opaque
 * fill with at most a 1 px ring, because a `backdrop-filter` per clip is a
 * compositor layer per clip and the budget is 500 of them at 60 fps
 * (`docs/04-frontend-architecture.md` §9).
 *
 * **Rule 4 — nothing driven by the pointer is animated.** Clips carry a
 * transition on `background` only. Position and width change with the hand, and
 * a transition on those reads as lag.
 *
 * The arithmetic — snapping, windowing, hit zones, the marquee — is in
 * `gestures.ts`, tested without a DOM. What is left here is pointer plumbing.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import {
  selectAllClips,
  clipBoundsMs,
  selectDurationMs,
  selectLanes,
  trimEndCeilingMs,
  useEditor,
} from '@/editor/state/store'
import {
  clipEndMs,
  isMediaTrack,
  type AnyClip,
  type MediaClip,
  type Track,
} from '@/editor/state/timeline-document'
import {
  clipsInWindow,
  marqueeSelection,
  msAtLaneX,
  snapCandidatesFor,
  snapMs,
  zoneAt,
} from '@/editor/timeline/gestures'
import {
  MAX_ZOOM,
  MIN_ZOOM,
  formatTimecode,
  msToPx,
  pxToMs,
  scrollForAnchoredZoom,
  tickLabel,
  ticksForWindow,
} from '@/editor/timeline/scale'
import {
  IconMovie,
  IconMusic,
  IconTypography,
  IconVolume,
  IconVolumeOff,
  type IconProps,
} from '@/editor/icons'
import { WaveformCanvas } from '@/editor/timeline/WaveformCanvas'

const RULER_HEIGHT = 26
const LANE_HEIGHT = 56
const HEADER_WIDTH = 96
/** Half a screen either side, so nothing is seen popping in during a scroll. */
const OVERSCAN_FRACTION = 0.5

const LANE_LABEL: Record<string, string> = { video: 'V1', audio: 'A1', text: 'T1' }

interface Marquee {
  fromMs: number
  toMs: number
  /**
   * The lanes the rubber band covers, as indices into `lanes`.
   *
   * **A marquee without a vertical extent is not a marquee**, it is a time
   * range: it would catch every clip playing at that instant on every lane, and
   * a plain click on an empty patch of the text lane would select whatever
   * video happened to be underneath it.
   */
  fromLane: number
  toLane: number
  additive: boolean
}

export function Timeline() {
  const lanes = useEditor(selectLanes)
  const allClips = useEditor(selectAllClips)
  const durationMs = useEditor(selectDurationMs)
  const zoom = useEditor((state) => state.zoom)
  const playheadMs = useEditor((state) => state.playheadMs)
  const selection = useEditor((state) => state.selection)
  const drag = useEditor((state) => state.drag)

  const laneRef = useRef<HTMLDivElement>(null)
  const gridRef = useRef<HTMLDivElement>(null)
  const [viewportPx, setViewportPx] = useState(0)
  const [scrollPx, setScrollPx] = useState(0)
  const [marquee, setMarquee] = useState<Marquee | null>(null)
  const [hovered, setHovered] = useState<string | null>(null)

  useLayoutEffect(() => {
    const lane = laneRef.current
    if (!lane) return
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (entry) setViewportPx(entry.contentRect.width)
    })
    observer.observe(lane)
    setViewportPx(lane.clientWidth)
    return () => observer.disconnect()
  }, [])

  const fromMs = pxToMs(scrollPx, zoom)
  const toMs = pxToMs(scrollPx + viewportPx, zoom)
  const overscanMs = pxToMs(viewportPx * OVERSCAN_FRACTION, zoom)
  const ticks = viewportPx > 0 ? ticksForWindow(fromMs, toMs, zoom) : []

  const snapTargets = useMemo(
    () => snapCandidatesFor(allClips, drag?.clipId ?? null, playheadMs),
    [allClips, drag?.clipId, playheadMs],
  )

  const xToMs = useCallback(
    (clientX: number) => {
      const lane = laneRef.current
      if (!lane) return 0
      return msAtLaneX(clientX - lane.getBoundingClientRect().left, zoom, scrollPx)
    },
    [zoom, scrollPx],
  )

  /** Ctrl/⌘ + wheel zooms around the cursor; a plain wheel scrolls. Anchoring on
   * the cursor is what stops the clip you are looking at sliding away. */
  const onWheel = useCallback(
    (event: WheelEvent) => {
      if (!(event.ctrlKey || event.metaKey)) {
        setScrollPx((current) => Math.max(0, current + event.deltaX + event.deltaY))
        return
      }
      event.preventDefault()
      const lane = laneRef.current
      if (!lane) return
      const anchorPx = event.clientX - lane.getBoundingClientRect().left
      const anchorMs = pxToMs(scrollPx + anchorPx, zoom)
      const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom * (event.deltaY < 0 ? 1.15 : 1 / 1.15)))
      useEditor.getState().setZoom(next)
      setScrollPx(scrollForAnchoredZoom(anchorMs, anchorPx, next))
    },
    [scrollPx, zoom],
  )

  /**
   * Registered by hand, because **React's `onWheel` is passive**.
   *
   * React attaches `wheel` (with `touchstart` and `touchmove`) to the root with
   * `{ passive: true }`, so `preventDefault()` inside a JSX `onWheel` does
   * nothing but log a warning — ⌘/ctrl + wheel would zoom the timeline *and*
   * the whole browser page at the same time. A listener added here can say it
   * is not passive; the one on the JSX attribute cannot.
   */
  const latestWheel = useRef(onWheel)
  useLayoutEffect(() => {
    latestWheel.current = onWheel
  }, [onWheel])
  useEffect(() => {
    const lane = laneRef.current
    if (!lane) return
    const handler = (event: WheelEvent) => latestWheel.current(event)
    lane.addEventListener('wheel', handler, { passive: false })
    return () => lane.removeEventListener('wheel', handler)
  }, [])

  // ------------------------------------------------------------------ drag
  const onClipPointerDown = useCallback(
    (event: React.PointerEvent, clip: AnyClip, widthPx: number) => {
      event.stopPropagation()
      const element = event.currentTarget as HTMLElement
      element.setPointerCapture?.(event.pointerId)

      const store = useEditor.getState()
      const additive = event.shiftKey || event.metaKey || event.ctrlKey
      if (!store.selection.has(clip.id) || additive) store.select(clip.id, { additive })

      const zone = zoneAt(event.clientX - element.getBoundingClientRect().left, widthPx)
      const grabbedMs = xToMs(event.clientX)
      store.beginDrag({
        kind: zone,
        clipId: clip.id,
        previewMs: zone === 'trim-end' ? clipEndMs(clip) : clip.startMs,
        // How far into the clip the pointer landed, so the clip does not jump
        // its own left edge to the cursor on the first move.
        grabOffsetMs: zone === 'move' ? grabbedMs - clip.startMs : 0,
      })
    },
    [xToMs],
  )

  /** Which lane a pointer is over, as an index into `lanes`. */
  const laneAt = useCallback(
    (clientY: number) => {
      const grid = gridRef.current
      if (!grid) return 0
      const offset = clientY - grid.getBoundingClientRect().top
      return Math.max(0, Math.min(lanes.length - 1, Math.floor(offset / LANE_HEIGHT)))
    },
    [lanes.length],
  )

  const onLanePointerMove = useCallback(
    (event: React.PointerEvent) => {
      const store = useEditor.getState()
      const current = store.drag
      if (current) {
        const raw = xToMs(event.clientX) - (current.grabOffsetMs ?? 0)
        let previewMs = snapMs(raw, snapTargets, zoom, event.altKey)
        if (current.kind === 'trim-end') {
          // The commit clamps to this; so must the preview, or the edge trails
          // the pointer past the end of the media and snaps back on release.
          const ceiling = trimEndCeilingMs(store, current.clipId)
          if (ceiling !== undefined) previewMs = Math.min(previewMs, ceiling)
        }
        store.updateDrag(previewMs)
        return
      }
      if (marquee && event.buttons === 1) {
        setMarquee({ ...marquee, toMs: xToMs(event.clientX), toLane: laneAt(event.clientY) })
      }
    },
    [laneAt, marquee, snapTargets, xToMs, zoom],
  )

  const onLanePointerUp = useCallback(() => {
    const store = useEditor.getState()
    if (store.drag) {
      store.endDrag()
      return
    }
    if (marquee) {
      // Only the lanes the band actually covers — see `marqueeSelection`.
      const hits = marqueeSelection(lanes, marquee)
      const next = marquee.additive ? [...store.selection, ...hits] : hits
      store.selectMany(next)
      setMarquee(null)
    }
  }, [lanes, marquee])

  /** An empty patch of lane starts a marquee. The ruler is what scrubs — a lane
   * that both scrubbed and lassoed would move the playhead on every attempt to
   * select. */
  const onLanePointerDown = useCallback(
    (event: React.PointerEvent) => {
      ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
      const at = xToMs(event.clientX)
      const lane = laneAt(event.clientY)
      setMarquee({ fromMs: at, toMs: at, fromLane: lane, toLane: lane, additive: event.shiftKey })
    },
    [laneAt, xToMs],
  )

  const scrub = useCallback(
    (event: React.PointerEvent) => {
      useEditor.getState().setPlayhead(xToMs(event.clientX))
    },
    [xToMs],
  )

  return (
    <section
      className="no-select flex flex-col"
      // M4.5 item 7. It was *"a fixed-height block at the bottom of the window
      // with no border and no visual relationship to anything above it"* — all
      // of its legibility came from inside the component. It is now a raised
      // surface with its own rule and radius, so it reads as a region of the
      // editor rather than something parked underneath one. The height it sits
      // at is the workspace's business, and draggable since the same item.
      style={{
        background: 'var(--color-surface-2)',
        borderTop: '1px solid var(--color-rule)',
        boxShadow: 'inset 0 1px 0 var(--color-rule)',
      }}
      data-testid="timeline"
      data-zoom={zoom}
      data-playhead-ms={playheadMs}
      data-duration-ms={durationMs}
      data-clip-count={allClips.length}
      data-lane-count={lanes.length}
    >
      <header
        className="flex items-center gap-3 px-3 py-1.5 text-xs"
        style={{ color: 'var(--color-ink-3)' }}
      >
        <span className="tnum" style={{ color: 'var(--color-ink) ' }} data-testid="playhead-readout">
          {formatTimecode(playheadMs, { withMillis: true })}
        </span>
        <span className="tnum">/ {formatTimecode(durationMs, { withMillis: true })}</span>
        <span className="tnum">{allClips.length} clips</span>
        {/* M4.5 item 6, the discoverability half. The precise zoom — ctrl/⌘ +
            wheel, anchored on the cursor — has existed since M3 and was
            findable only by reading the source. The complaint was really *"the
            slider is coarse and I did not know the precise way existed"*, and
            it goes away the moment the precise way is named on screen.

            Decided 22 August: the behaviour itself does **not** change. Plain
            wheel keeps scrolling, because horizontal scrolling on a timeline is
            not optional, and ctrl+wheel-to-zoom is the convention every browser
            already teaches. */}
        <span className="ml-auto" style={{ color: 'var(--color-ink-faint)' }}>
          alt suppresses snapping
        </span>
        <label className="flex items-center gap-2">
          <span>Zoom</span>
          <input
            type="range"
            min={MIN_ZOOM}
            max={MAX_ZOOM}
            step={1}
            value={zoom}
            onChange={(event) => useEditor.getState().setZoom(Number(event.target.value))}
            className="w-24"
            data-testid="zoom"
            aria-label="Timeline zoom"
          />
        </label>
        <span
          className="whitespace-nowrap"
          style={{ color: 'var(--color-ink-faint)' }}
          title="Hold ctrl (or ⌘) and scroll over the timeline to zoom around the pointer"
          data-testid="zoom-hint"
        >
          <kbd style={{ fontFamily: 'inherit' }}>ctrl</kbd> + scroll to zoom here
        </span>
      </header>

      <div className="flex min-h-0 flex-1">
        <div
          className="shrink-0 text-xs"
          style={{ width: HEADER_WIDTH, borderRight: '1px solid var(--color-rule)' }}
        >
          <div style={{ height: RULER_HEIGHT }} />
          {lanes.map((track) => (
            <LaneHeader key={track.id} track={track} />
          ))}
        </div>

        <div
          ref={laneRef}
          className="relative min-w-0 flex-1 overflow-hidden"
          onPointerMove={onLanePointerMove}
          onPointerUp={onLanePointerUp}
          data-testid="timeline-lane"
        >
          <div
            style={{ height: RULER_HEIGHT, background: 'var(--color-ruler)', position: 'relative' }}
            onPointerDown={(event) => {
              ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
              scrub(event)
            }}
            onPointerMove={(event) => {
              if (event.buttons === 1 && !useEditor.getState().drag) scrub(event)
            }}
            data-testid="ruler"
            data-tick-count={ticks.length}
          >
            {ticks.map((tick) => (
              <div
                key={tick.ms}
                className="absolute bottom-0"
                style={{
                  left: tick.px - scrollPx,
                  height: tick.major ? 9 : 4,
                  width: 1,
                  background: 'var(--color-ruler-tick)',
                }}
              >
                {tick.major && (
                  <span
                    className="tnum absolute bottom-2.5 left-1 text-[10px] whitespace-nowrap"
                    style={{ color: 'var(--color-ruler-label)' }}
                  >
                    {tickLabel(tick.ms, zoom)}
                  </span>
                )}
              </div>
            ))}
          </div>

          <div
            ref={gridRef}
            className="relative"
            style={{
              // The grid the project lead asked to keep technical: majors on the
              // ruler's own labels, minors at a fifth of that, 1px hairlines.
              ...gridStyle(ticks, scrollPx),
              backgroundColor: 'var(--color-track)',
            }}
            onPointerDown={onLanePointerDown}
          >
            {lanes.map((track) => (
              <Lane
                key={track.id}
                track={track}
                zoom={zoom}
                scrollPx={scrollPx}
                fromMs={fromMs}
                toMs={toMs}
                overscanMs={overscanMs}
                selection={selection}
                hovered={hovered}
                onHover={setHovered}
                onClipPointerDown={onClipPointerDown}
              />
            ))}
            {marquee && Math.abs(marquee.toMs - marquee.fromMs) > 0 && (
              <div
                className="pointer-events-none absolute"
                style={{
                  left: msToPx(Math.min(marquee.fromMs, marquee.toMs), zoom) - scrollPx,
                  width: msToPx(Math.abs(marquee.toMs - marquee.fromMs), zoom),
                  top: Math.min(marquee.fromLane, marquee.toLane) * LANE_HEIGHT,
                  height:
                    (Math.abs(marquee.toLane - marquee.fromLane) + 1) * LANE_HEIGHT,
                  background: 'var(--color-accent-soft)',
                  border: '1px solid var(--color-accent-line)',
                }}
                data-testid="marquee"
                data-from-lane={Math.min(marquee.fromLane, marquee.toLane)}
                data-to-lane={Math.max(marquee.fromLane, marquee.toLane)}
              />
            )}
          </div>

          {drag && (
            <div
              className="pointer-events-none absolute top-0 bottom-0"
              style={{
                left: msToPx(drag.previewMs, zoom) - scrollPx,
                width: 1,
                background: 'var(--color-snap-guide)',
              }}
              data-testid="snap-guide"
            />
          )}

          <div
            className="pointer-events-none absolute top-0 bottom-0"
            style={{
              left: msToPx(playheadMs, zoom) - scrollPx,
              width: 2,
              background: 'var(--color-playhead)',
              borderRadius: 'var(--radius-xs)',
            }}
            data-testid="playhead"
            data-left-px={msToPx(playheadMs, zoom) - scrollPx}
          />
        </div>
      </div>
    </section>
  )
}

/**
 * The grid, as a repeating background rather than elements.
 *
 * One div per gridline at 500 clips' worth of zoom is thousands of nodes that
 * exist only to be one pixel wide. `background-image` costs one paint.
 *
 * **The scroll offset is the whole reason this returns a position too.** The
 * gradient repeats from the element's own left edge, while the ruler draws its
 * ticks at `tick.px - scrollPx`: leave the background where it is and the grid
 * and the ruler agree only at the top of the timeline, then drift apart by up
 * to a full interval as soon as anything is scrolled. Both layers share the
 * origin — the minor interval divides the major one exactly — so one offset
 * moves them together.
 */
function gridStyle(
  ticks: { px: number; major: boolean }[],
  scrollPx: number,
): { backgroundImage: string; backgroundPosition: string } {
  const majors = ticks.filter((tick) => tick.major)
  const first = majors[0]
  const second = majors[1]
  if (!first || !second) return { backgroundImage: 'none', backgroundPosition: '0 0' }
  const majorPx = second.px - first.px
  if (majorPx <= 0) return { backgroundImage: 'none', backgroundPosition: '0 0' }
  return {
    backgroundImage:
      `repeating-linear-gradient(90deg, var(--color-grid-major) 0 1px, transparent 1px ${majorPx}px), ` +
      `repeating-linear-gradient(90deg, var(--color-grid-minor) 0 1px, transparent 1px ${majorPx / 5}px)`,
    backgroundPosition: `${-modulo(scrollPx, majorPx)}px 0`,
  }
}

/** `%` keeps the sign of the dividend in JavaScript, and a negative offset here
 * would shift the grid the wrong way at the left edge. */
function modulo(value: number, span: number): number {
  return ((value % span) + span) % span
}

const LANE_ICON: Record<string, React.ComponentType<IconProps>> = {
  video: IconMovie,
  audio: IconMusic,
  text: IconTypography,
}

function LaneHeader({ track }: { track: Track }) {
  const muted = track.muted ?? false
  const KindIcon = LANE_ICON[track.kind]
  return (
    <div
      className="flex items-center gap-2 px-3 text-xs"
      style={{
        height: LANE_HEIGHT,
        background: 'var(--color-track-header)',
        borderBottom: '1px solid var(--color-rule)',
        color: 'var(--color-ink-3)',
      }}
      data-testid="lane-header"
      data-track-kind={track.kind}
      data-muted={muted}
    >
      {KindIcon && <KindIcon size={13} aria-hidden="true" />}
      <span style={{ fontWeight: 600 }}>{LANE_LABEL[track.kind] ?? track.kind}</span>
      <button
        type="button"
        onClick={() => useEditor.getState().setTrackMuted(track.id, !muted)}
        className="ml-auto flex items-center gap-1 px-1.5"
        style={{
          borderRadius: 'var(--radius-xs)',
          border: '1px solid var(--color-rule)',
          // Charter rule 3: the icon swaps and the letter stays, on top of the
          // colour change — three signals, not one.
          color: muted ? 'var(--color-warning)' : 'var(--color-ink-faint)',
        }}
        aria-label={muted ? `Unmute ${track.kind}` : `Mute ${track.kind}`}
        aria-pressed={muted}
      >
        {muted ? (
          <IconVolumeOff size={12} aria-hidden="true" />
        ) : (
          <IconVolume size={12} aria-hidden="true" />
        )}
        M
      </button>
    </div>
  )
}

function Lane({
  track,
  zoom,
  scrollPx,
  fromMs,
  toMs,
  overscanMs,
  selection,
  hovered,
  onHover,
  onClipPointerDown,
}: {
  track: Track
  zoom: number
  scrollPx: number
  fromMs: number
  toMs: number
  overscanMs: number
  selection: ReadonlySet<string>
  hovered: string | null
  onHover: (id: string | null) => void
  onClipPointerDown: (event: React.PointerEvent, clip: AnyClip, widthPx: number) => void
}) {
  // Virtualised by time: everything off screen is not rendered at all.
  const visible = clipsInWindow(track.clips as AnyClip[], fromMs, toMs, overscanMs)
  const muted = track.muted ?? false

  return (
    <div
      className="relative"
      style={{
        height: LANE_HEIGHT,
        borderBottom: '1px solid var(--color-rule)',
        // Charter §9: a muted lane dims, and its header shows an M.
        opacity: muted ? 'var(--color-track-muted-opacity)' : 1,
      }}
      data-testid="lane"
      data-track-id={track.id}
      data-track-kind={track.kind}
      data-visible-clips={visible.length}
    >
      {visible.map((clip) => (
        <ClipView
          key={clip.id}
          clip={clip}
          isMedia={isMediaTrack(track)}
          zoom={zoom}
          scrollPx={scrollPx}
          selected={selection.has(clip.id)}
          hovered={hovered === clip.id}
          onHover={onHover}
          onPointerDown={onClipPointerDown}
        />
      ))}
    </div>
  )
}

/**
 * One clip, in the four states the charter's §9 table defines.
 *
 * Each changes at least two properties — fill and weight, fill and ring, fill
 * and position — because at 500 clips a clip is three pixels wide and hue is
 * the first thing that stops being readable.
 */
function ClipView({
  clip,
  isMedia,
  zoom,
  scrollPx,
  selected,
  hovered,
  onHover,
  onPointerDown,
}: {
  clip: AnyClip
  isMedia: boolean
  zoom: number
  scrollPx: number
  selected: boolean
  hovered: boolean
  onHover: (id: string | null) => void
  onPointerDown: (event: React.PointerEvent, clip: AnyClip, widthPx: number) => void
}) {
  // Subscribe to the drag itself — one stable reference per pointer move — and
  // compute the bounds outside the subscription. Subscribing to the computed
  // object instead returns a fresh value on every read, which Zustand sees as a
  // change, which re-renders, which reads again: the infinite loop.
  const drag = useEditor((state) => state.drag)
  const bounds = clipBoundsMs(drag, clip as MediaClip)
  const dragging = drag?.clipId === clip.id

  const left = msToPx(bounds.startMs, zoom) - scrollPx
  const width = Math.max(2, msToPx(bounds.durationMs, zoom))

  const background = dragging
    ? 'var(--color-clip-dragging)'
    : selected
      ? 'var(--color-clip-selected)'
      : hovered
        ? 'var(--color-clip-hover)'
        : 'var(--color-clip)'

  return (
    <div
      className="absolute top-1 bottom-1 overflow-hidden"
      style={{
        left,
        width,
        background,
        borderRadius: 'var(--radius-xs)',
        // A 1px border only when the clip is too narrow for fill alone to
        // separate it from its neighbour.
        border: width < 6 ? '1px solid var(--color-clip-border)' : undefined,
        boxShadow: selected
          ? '0 0 0 1px var(--color-clip-selected-border), 0 0 14px var(--color-accent-glow)'
          : dragging
            ? '0 0 0 1px var(--color-accent), 0 5px 16px rgba(0,0,0,0.5)'
            : undefined,
        transform: dragging ? 'translateY(-2px)' : undefined,
        color: selected ? 'var(--color-accent-ink)' : 'var(--color-ink-2)',
        // Rule 4: only the discrete change animates. Position and width follow
        // the hand and must not.
        transition: 'background var(--duration-micro) ease-out',
        cursor: dragging ? 'grabbing' : 'grab',
      }}
      onPointerDown={(event) => onPointerDown(event, clip, width)}
      onPointerEnter={() => onHover(clip.id)}
      onPointerLeave={() => onHover(null)}
      data-testid="clip"
      data-clip-id={clip.id}
      data-start-ms={bounds.startMs}
      data-duration-ms={bounds.durationMs}
      data-end-ms={bounds.startMs + bounds.durationMs}
      data-selected={selected}
      data-dragging={dragging}
    >
      {isMedia && 'assetId' in clip && (
        <WaveformCanvas assetId={clip.assetId} clip={clip as MediaClip} widthPx={width} />
      )}
      <span
        className="pointer-events-none absolute top-1 left-2 truncate text-[11px]"
        style={{ maxWidth: width - 8, fontWeight: selected ? 600 : 400 }}
      >
        {'text' in clip ? clip.text : formatTimecode(clip.durationMs)}
      </span>
      {/* The trim handles. Wider than they look: `zoneAt` gives them a quarter
          of a narrow clip so a 14px clip is still grabbable in the middle. */}
      <span
        className="absolute top-0 bottom-0 left-0"
        style={{ width: 6, cursor: 'ew-resize' }}
        data-testid="trim-start"
      />
      <span
        className="absolute top-0 right-0 bottom-0"
        style={{ width: 6, cursor: 'ew-resize' }}
        data-testid="trim-end"
      />
    </div>
  )
}

/** Re-exported so the editor page can size its own layout consistently. */
export { LANE_HEIGHT, RULER_HEIGHT }

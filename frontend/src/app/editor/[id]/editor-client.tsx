'use client'

/**
 * Editor shell.
 *
 *   ┌────────────────────────────────────────────────────┐
 *   │ header — account, credits, save state              │
 *   ├────────────────────────────────────────────────────┤
 *   │ toolbar — split, duplicate, delete, undo, redo     │
 *   ├────┬─────────┬───────────────────────┬─────────────┤
 *   │ ra │ mode    │ preview (WebGL)       │ inspector   │
 *   │ il │ panel   ├───────────────────────┤             │
 *   │    │         │ transport             │             │
 *   ├────┴─────────┴───────────────────────┴─────────────┤
 *   │ ═══════ draggable divider ═════════════════════════│
 *   │ timeline — video, audio and text lanes             │
 *   └────────────────────────────────────────────────────┘
 *
 * M3 adds what makes the milestone's title true: the project is loaded from
 * the server, every edit commits through the store's patch history, and
 * autosave puts it back. The visual charter (docs/08-ui-charter.md) is applied
 * through tokens — there is not a literal colour in this file.
 *
 * **M4.5 rearranged it** (docs/12-m4-5-interface-pass.md). Four of the seven
 * items are visible in the map above:
 *
 *  * *item 2* — the transport left the header and sits under the picture it
 *    plays, next to the playhead it moves;
 *  * *item 4* — the left panel became a mode rail, and the right panel went
 *    back to being the inspector and nothing else. The tools panel that used to
 *    stack beneath it is gone; its three tools are modes on the rail;
 *  * *item 7* — the timeline's height is the user's to set, and the divider is
 *    a real control rather than a border;
 *  * *item 3* — the manual colour and audio controls the toolbar never had now
 *    have somewhere to live, which is what the rail bought.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'

import { AuthPanel } from '@/account/AuthPanel'
import { useSession } from '@/account/session'
import { actionFor, isTypingTarget, NUDGE_MS } from '@/editor/keyboard'
import { Preview } from '@/editor/playback/Preview'
import { PreviewBoundary } from '@/editor/playback/PreviewBoundary'
import type { ResolvedAsset } from '@/editor/playback/timeline-adapter'
import {
  selectCanRedo,
  selectCanUndo,
  selectClips,
  selectDurationMs,
  useEditor,
} from '@/editor/state/store'
import {
  IconCopy,
  IconLogout,
  IconRedo,
  IconScissors,
  IconSparkles,
  IconTrash,
  IconTypography,
  IconUndo,
} from '@/editor/icons'
import { Inspector } from '@/editor/inspector/Inspector'
import {
  clampTimelineHeight,
  DEFAULT_TIMELINE_PX,
  readStoredHeight,
  writeStoredHeight,
} from '@/editor/layout/split'
import { TimelineSplitter } from '@/editor/layout/TimelineSplitter'
import { modeById } from '@/editor/rail/modes'
import { ModeRail } from '@/editor/rail/ModeRail'
import { ModePanel } from '@/editor/rail/panels'
import { useRail } from '@/editor/rail/rail-store'
import { useTools } from '@/editor/tools/jobs-store'
import { Transport } from '@/editor/transport/Transport'
import { useProjectPersistence, type SaveStatus } from '@/editor/state/use-persistence'
import { Timeline } from '@/editor/timeline/Timeline'
import { listMedia } from '@/lib/api/endpoints'

export function EditorClient({ projectId }: { projectId: string }) {
  const { status, account, signOut } = useSession()

  if (status === 'restoring') {
    return (
      <main className="flex h-screen items-center justify-center text-sm" data-testid="restoring">
        <span style={{ color: 'var(--color-ink-2)' }}>Checking your session…</span>
      </main>
    )
  }

  if (status === 'signed-out') {
    return (
      <main className="flex h-screen items-center justify-center" data-testid="signed-out">
        <AuthPanel />
      </main>
    )
  }

  return (
    <Workspace
      projectId={projectId}
      email={account?.email ?? ''}
      credits={account?.credits.total ?? 0}
      onSignOut={() => void signOut()}
    />
  )
}

/** Charter §8: a state is never carried by colour alone, so each of these has
 * its own words as well as its own token. */
const SAVE_LABEL: Record<SaveStatus, { text: string; token: string }> = {
  idle: { text: 'Ready', token: 'var(--color-ink-3)' },
  loading: { text: 'Opening…', token: 'var(--color-ink-3)' },
  dirty: { text: 'Unsaved changes', token: 'var(--color-ink-2)' },
  saving: { text: 'Saving…', token: 'var(--color-accent)' },
  saved: { text: 'All changes saved', token: 'var(--color-ink-3)' },
  conflict: { text: 'Changed elsewhere', token: 'var(--color-warning)' },
  error: { text: 'Could not save', token: 'var(--color-danger)' },
}

function Workspace({
  projectId,
  email,
  credits,
  onSignOut,
}: {
  projectId: string
  email: string
  credits: number
  onSignOut: () => void
}) {
  const clips = useEditor(selectClips)

  const persistence = useProjectPersistence(projectId)
  // Subscribed rather than read once: the project id arrives asynchronously,
  // and `getState()` in a render would capture the null it had at mount.
  const openProjectId = useEditor((state) => state.projectId)
  const mode = modeById(useRail((state) => state.mode))

  /**
   * The timeline's height — M4.5 item 7.
   *
   * Kept here rather than in the editor store because it is not document state:
   * it does not commit, it is not undoable, and it must never reach a patch or
   * an autosave. It is remembered in `localStorage`, which is a property of this
   * person and this screen rather than of the project.
   */
  const [timelineHeight, setStoredTimelineHeight] = useState(DEFAULT_TIMELINE_PX)
  const [viewportHeight, setViewportHeight] = useState(0)

  useEffect(() => {
    setViewportHeight(window.innerHeight)
    const onResize = () => setViewportHeight(window.innerHeight)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // Read once on mount, not during render: `localStorage` does not exist on the
  // server, and reading it in a render body makes the first client paint differ
  // from the markup Next produced.
  useEffect(() => {
    const stored = readStoredHeight(typeof window === 'undefined' ? null : window.localStorage)
    if (stored !== null) setStoredTimelineHeight(clampTimelineHeight(stored, window.innerHeight))
  }, [])

  const setTimelineHeight = useCallback((next: number) => {
    setStoredTimelineHeight(next)
    writeStoredHeight(typeof window === 'undefined' ? null : window.localStorage, next)
  }, [])


  /**
   * Signed proxy URLs, keyed by asset id.
   *
   * Refetched rather than cached forever: the URLs expire in an hour
   * (contract §3), so a tab left open overnight has to ask again. They are
   * kept out of the timeline document for the same reason.
   */
  const [assets, setAssets] = useState<Map<string, ResolvedAsset>>(new Map())

  const loadAssets = useCallback(async () => {
    try {
      const page = await listMedia({ limit: 100 })
      const next = new Map<string, ResolvedAsset>()
      const durations: Record<string, number> = {}
      for (const asset of page.items) {
        if (asset.status === 'ready' && asset.proxyUrl) {
          next.set(asset.id, { proxyUrl: asset.proxyUrl, durationMs: asset.durationMs ?? 0 })
        }
        // Every asset with a length, not only the ones with a proxy: an
        // audio-only file never gets one, and a music clip is trimmed against
        // its media exactly like a video clip is. This is what stops a trim
        // running past the end of the file and failing the next autosave on
        // invariant 4.
        if (asset.durationMs != null) durations[asset.id] = asset.durationMs
      }
      setAssets(next)
      useEditor.getState().setAssetDurations(durations)
    } catch {
      // The media bin surfaces its own failure; the preview simply has nothing
      // to play, which it already handles.
    }
  }, [])

  /**
   * One socket for the editor, opened here rather than by a panel.
   *
   * It used to live in `ToolsPanel`, which was fine while every tool was in one
   * component and wrong the moment they became modes: a socket that opens when
   * you click Captions and closes when you click Media would drop the progress
   * of the job you just started. It also re-syncs on every reconnect, which is
   * how a job that finished while the laptop slept is found rather than waited
   * for.
   */
  useEffect(() => {
    if (!openProjectId) return
    const { connect, disconnect } = useTools.getState()
    connect(openProjectId)
    return () => disconnect()
  }, [openProjectId])

  useEffect(() => {
    void loadAssets()
  }, [loadAssets])

  useEffect(() => {
    if (clips.length > 0) void loadAssets()
  }, [clips.length, loadAssets])

  // The keyboard map is a pure function in `editor/keyboard.ts`; this is only
  // the wiring. Nothing fires while focus is in a text field.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const action = actionFor(event, { isTyping: isTypingTarget(event.target) })
      if (!action) return
      const store = useEditor.getState()

      switch (action) {
        case 'play-pause':
          store.setPlaying(!store.isPlaying)
          break
        case 'split':
          store.splitAtPlayhead()
          break
        case 'delete':
          store.deleteSelection()
          break
        case 'duplicate':
          store.duplicateSelection()
          break
        case 'undo':
          store.undo()
          break
        case 'redo':
          store.redo()
          break
        case 'save':
          void persistence.flush()
          break
        case 'nudge-left':
          store.setPlayhead(store.playheadMs - NUDGE_MS)
          break
        case 'nudge-right':
          store.setPlayhead(store.playheadMs + NUDGE_MS)
          break
        case 'go-start':
          store.setPlayhead(0)
          break
        case 'go-end':
          store.setPlayhead(selectDurationMs(store))
          break
        case 'cancel':
          if (store.drag) store.cancelDrag()
          else store.select(null)
          break
      }
      event.preventDefault()
    }

    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [persistence])

  const readyCount = useMemo(() => assets.size, [assets])
  const save = SAVE_LABEL[persistence.status]

  return (
    <div className="no-select flex h-screen flex-col" data-testid="workspace">
      <header
        className="flex h-12 shrink-0 items-center gap-4 border-b px-4 text-xs"
        style={{ borderColor: 'var(--color-rule)' }}
      >
        <span data-testid="save-status" style={{ color: save.token }}>
          {save.text}
        </span>

        {/* A way back out. Before M4.5 the editor was reachable only by URL
            and had no link to anything else, so it was also a dead end once
            you were in it. */}
        <a
          href="/projects"
          className="ml-auto"
          style={{ color: 'var(--color-ink-2)' }}
          data-testid="back-to-projects"
        >
          Projects
        </a>

        <span
          className="tnum flex items-center gap-1"
          style={{ color: 'var(--color-ink-2)' }}
          data-testid="credits"
        >
          <IconSparkles size={12} aria-hidden="true" style={{ color: 'var(--color-accent)' }} />
          {credits} credits
        </span>
        <span
          className="max-w-40 truncate"
          style={{ color: 'var(--color-ink-2)' }}
          data-testid="account-email"
        >
          {email}
        </span>
        <button
          type="button"
          onClick={onSignOut}
          className="flex items-center gap-1.5 border px-2 py-1"
          style={{ borderColor: 'var(--color-rule)', borderRadius: 'var(--radius-sm)' }}
          data-testid="sign-out"
        >
          <IconLogout size={13} aria-hidden="true" />
          Sign out
        </button>
      </header>

      <Toolbar />

      {persistence.conflictVersion !== null && (
        <ConflictBar
          currentVersion={persistence.conflictVersion}
          onKeepMine={() => void persistence.keepMine()}
          onLoadTheirs={() => void persistence.loadTheirs()}
        />
      )}

      <div className="flex min-h-0 flex-1">
        <ModeRail />

        {/* The mode's own content, where the media list used to be the only
            thing that could go. One mode at a time — item 4. */}
        <aside
          className="w-72 shrink-0 border-r"
          style={{ borderColor: 'var(--color-rule)' }}
          data-ready-assets={readyCount}
          data-testid="mode-content"
        >
          <ModePanel mode={mode} />
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-1">
            {/* A browser without WebGL2 loses the picture and nothing else.
                Without this the renderer's throw unmounts the whole editor. */}
            <PreviewBoundary>
              <Preview assets={assets} />
            </PreviewBoundary>
          </div>
          {/* Item 2: under the picture, above the playhead, between the two
              things it relates to. */}
          <Transport />
        </div>

        {/* Properties of whatever is selected, and nothing else. It is no
            longer asked to also hold a scrolling list of tools, which is what
            makes it scale — item 4. */}
        <aside
          className="w-72 shrink-0 overflow-y-auto border-l"
          style={{ borderColor: 'var(--color-rule)' }}
        >
          <Inspector />
        </aside>
      </div>

      <TimelineSplitter
        heightPx={timelineHeight}
        viewportPx={viewportHeight}
        onResize={setTimelineHeight}
      />
      {/* The padding is the neon frame's, not decoration for its own sake. The
          ring and its glow are drawn outside the timeline's box: flush against
          the window they were sheared off on three sides, and with only a few
          pixels the glow landed on the transport bar instead of on dark space.
          A glow needs somewhere dark to fall off into or it just soils its
          neighbour. */}
      <div className="shrink-0 px-4 pt-2 pb-4" style={{ height: timelineHeight }}>
        <Timeline />
      </div>
    </div>
  )
}

/**
 * The toolbar — one dedicated, named button per feature, which is what the
 * project lead asked for at the start and what the A2 mockup keeps.
 *
 * Everything here is free. The three AI tools are named and disabled until M4
 * lands them, and they are visually separate because they will cost credits:
 * charter rule 5, a user must never learn what a button costs by pressing it.
 */
function Toolbar() {
  const canUndo = useEditor(selectCanUndo)
  const canRedo = useEditor(selectCanRedo)
  const hasSelection = useEditor((state) => state.selection.size > 0)
  const store = useEditor.getState

  return (
    <div
      className="flex shrink-0 items-center gap-1.5 px-3 py-2 text-xs"
      style={{ borderBottom: '1px solid var(--color-rule)' }}
      data-testid="toolbar"
    >
      <Tool icon={<IconScissors size={15} />} onClick={() => store().splitAtPlayhead()}>
        Split
      </Tool>
      <Tool
        icon={<IconCopy size={15} />}
        onClick={() => store().duplicateSelection()}
        disabled={!hasSelection}
      >
        Duplicate
      </Tool>
      <Tool
        icon={<IconTrash size={15} />}
        onClick={() => store().deleteSelection()}
        disabled={!hasSelection}
      >
        Delete
      </Tool>
      <Tool icon={<IconTypography size={15} />} onClick={() => store().addTitle('New title')}>
        Add title
      </Tool>
      <span className="mx-1" style={{ width: 1, height: 18, background: 'var(--color-rule)' }} />
      <Tool icon={<IconUndo size={15} />} onClick={() => store().undo()} disabled={!canUndo}>
        Undo
      </Tool>
      <Tool icon={<IconRedo size={15} />} onClick={() => store().redo()} disabled={!canRedo}>
        Redo
      </Tool>
      <span className="mx-1" style={{ width: 1, height: 18, background: 'var(--color-rule)' }} />
      {/* The tools moved to the rail on the left in M4.5, as modes of their
          own. This line follows them — a label pointing at a panel that no
          longer exists is worse than no label, and it is the kind of thing only
          opening the editor finds. */}
      <span className="ml-1 flex items-center gap-1" style={{ color: 'var(--color-ink-faint)' }}>
        <IconSparkles size={12} aria-hidden="true" />
        Colour, captions and smart trim are on the left
      </span>
    </div>
  )
}

function Tool({
  children,
  icon,
  onClick,
  disabled,
  ai,
}: {
  children: React.ReactNode
  icon?: React.ReactNode
  onClick?: () => void
  disabled?: boolean
  ai?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex items-center gap-1.5 px-2.5 py-1"
      style={{
        borderRadius: 'var(--radius-sm)',
        background: ai ? 'var(--color-accent-soft)' : 'var(--color-surface-2)',
        // The icon inherits this via `currentColor` — one value to change, not
        // two things to keep in sync.
        color: ai ? 'var(--color-accent)' : 'var(--color-ink-2)',
        // Charter §8: disabled is opacity plus no pointer events, never colour
        // alone.
        opacity: disabled ? 0.4 : 1,
        transition: 'background var(--duration-micro) ease-out',
      }}
    >
      {icon && <span aria-hidden="true" className="flex shrink-0">{icon}</span>}
      {children}
    </button>
  )
}

/**
 * A version conflict — contract §5, `409 VERSION_CONFLICT`.
 *
 * Two choices and no automatic merge: two timelines cannot be reconciled
 * without knowing which edit the user meant (docs/04-frontend-architecture.md
 * §6.1). A bar rather than a modal, because the user has to be able to look at
 * what they have before deciding to throw it away.
 */
function ConflictBar({
  currentVersion,
  onKeepMine,
  onLoadTheirs,
}: {
  currentVersion: number
  onKeepMine: () => void
  onLoadTheirs: () => void
}) {
  return (
    <div
      className="flex shrink-0 items-center gap-4 px-4 py-2 text-xs"
      style={{
        background: 'var(--color-surface-2)',
        borderBottom: '1px solid var(--color-warning)',
        color: 'var(--color-ink)',
      }}
      data-testid="version-conflict"
    >
      <span>
        This project was changed somewhere else — it is now at version {currentVersion}. Autosave
        is paused until you choose.
      </span>
      <button
        type="button"
        onClick={onKeepMine}
        className="px-3 py-1"
        style={{
          borderRadius: 'var(--radius-pill)',
          background: 'var(--color-accent)',
          color: 'var(--color-accent-ink)',
          fontWeight: 600,
        }}
      >
        Keep mine
      </button>
      <button
        type="button"
        onClick={onLoadTheirs}
        className="border px-3 py-1"
        style={{ borderRadius: 'var(--radius-pill)', borderColor: 'var(--color-rule)' }}
      >
        Load the other version
      </button>
    </div>
  )
}

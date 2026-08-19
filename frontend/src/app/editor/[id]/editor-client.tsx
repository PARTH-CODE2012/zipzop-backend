'use client'

/**
 * Editor shell.
 *
 *   ┌──────────────────────────────────────────┐
 *   │ toolbar — ordinary tools + AI tools (M4) │
 *   ├───────────┬──────────────────────────────┤
 *   │ media bin │ preview (WebGL canvas)       │
 *   │           ├──────────────────────────────┤
 *   │           │ inspector (M3)               │
 *   ├───────────┴──────────────────────────────┤
 *   │ timeline — video track                   │
 *   └──────────────────────────────────────────┘
 *
 * M3 adds what makes the milestone's title true: the project is loaded from
 * the server, every edit commits through the store's patch history, and
 * autosave puts it back. The visual charter (docs/08-ui-charter.md) is applied
 * through tokens — there is not a literal colour in this file.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'

import { AuthPanel } from '@/account/AuthPanel'
import { useSession } from '@/account/session'
import { actionFor, isTypingTarget, NUDGE_MS } from '@/editor/keyboard'
import { Preview } from '@/editor/playback/Preview'
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
  IconMessage,
  IconPalette,
  IconPause,
  IconPlay,
  IconRedo,
  IconScissors,
  IconSparkles,
  IconTrash,
  IconTypography,
  IconUndo,
  IconWand,
} from '@/editor/icons'
import { Inspector } from '@/editor/inspector/Inspector'
import { useProjectPersistence, type SaveStatus } from '@/editor/state/use-persistence'
import { Timeline } from '@/editor/timeline/Timeline'
import { formatTimecode } from '@/editor/timeline/scale'
import { listMedia } from '@/lib/api/endpoints'
import { MediaBin } from '@/media/MediaBin'

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
  const durationMs = useEditor(selectDurationMs)
  const isPlaying = useEditor((state) => state.isPlaying)

  const persistence = useProjectPersistence(projectId)

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
      for (const asset of page.items) {
        if (asset.status === 'ready' && asset.proxyUrl) {
          next.set(asset.id, { proxyUrl: asset.proxyUrl, durationMs: asset.durationMs ?? 0 })
        }
      }
      setAssets(next)
    } catch {
      // The media bin surfaces its own failure; the preview simply has nothing
      // to play, which it already handles.
    }
  }, [])

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
        <button
          type="button"
          onClick={() => useEditor.getState().setPlaying(!isPlaying)}
          disabled={durationMs === 0}
          className="flex items-center gap-1.5 px-3 py-1 disabled:opacity-40"
          style={{
            borderRadius: 'var(--radius-pill)',
            background: 'var(--color-accent)',
            color: 'var(--color-accent-ink)',
            fontWeight: 600,
          }}
          data-testid="play"
          data-playing={isPlaying}
        >
          {isPlaying ? <IconPause size={13} aria-hidden="true" /> : <IconPlay size={13} aria-hidden="true" />}
          {isPlaying ? 'Pause' : 'Play'}
        </button>
        <span className="tnum" style={{ color: 'var(--color-ink-2)' }}>
          {formatTimecode(durationMs, { withMillis: true })}
        </span>

        <span data-testid="save-status" style={{ color: save.token }}>
          {save.text}
        </span>

        <span
          className="ml-auto uppercase tracking-widest"
          style={{ color: 'var(--color-ink-3)' }}
        >
          {/* AI tools land in M4. Named, not mocked. */}
          Captions · Smart trim · Colour — M4
        </span>

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
        <aside
          className="w-72 shrink-0 border-r"
          style={{ borderColor: 'var(--color-rule)' }}
          data-ready-assets={readyCount}
        >
          <MediaBin />
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-1">
            <Preview assets={assets} />
          </div>
          <div className="h-44 shrink-0 overflow-hidden">
            <Inspector />
          </div>
        </div>
      </div>

      <div className="h-48 shrink-0">
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
      <Tool ai disabled icon={<IconMessage size={15} />}>
        Captions
      </Tool>
      <Tool ai disabled icon={<IconWand size={15} />}>
        Smart trim
      </Tool>
      <Tool ai disabled icon={<IconPalette size={15} />}>
        Colour
      </Tool>
      <span className="ml-1 flex items-center gap-1" style={{ color: 'var(--color-ink-faint)' }}>
        <IconSparkles size={12} aria-hidden="true" />
        AI tools land in M4
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

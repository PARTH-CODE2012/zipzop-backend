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
 * M2 fills in the media bin, the preview and the timeline. The inspector and
 * the AI tools are M3 and M4 and are left as labelled space rather than as
 * something that looks finished and does nothing.
 *
 * **This has no visual identity.** Every colour comes from a token in
 * `globals.css`, and those are all neutral greys until the project lead
 * delivers a palette. The structure and the behaviour are what M2 proves.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'

import { AuthPanel } from '@/account/AuthPanel'
import { useSession } from '@/account/session'
import { Preview } from '@/editor/playback/Preview'
import type { ResolvedAsset } from '@/editor/playback/timeline-adapter'
import { selectClips, selectDurationMs, useEditor } from '@/editor/state/store'
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
  const setPlaying = useEditor((state) => state.setPlaying)
  const setPlayhead = useEditor((state) => state.setPlayhead)

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

  // A clip cannot be added until its asset resolves, so reload whenever the
  // timeline gains one.
  useEffect(() => {
    if (clips.length > 0) void loadAssets()
  }, [clips.length, loadAssets])

  // Space plays and pauses, as it does in every editor.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return
      if (event.code === 'Space') {
        event.preventDefault()
        setPlaying(!isPlaying)
      }
      if (event.code === 'Home') setPlayhead(0)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isPlaying, setPlaying, setPlayhead])

  const readyCount = useMemo(() => assets.size, [assets])

  return (
    <div className="no-select flex h-screen flex-col" data-testid="workspace">
      <header
        className="flex h-12 shrink-0 items-center gap-4 border-b px-4 text-xs"
        style={{ borderColor: 'var(--color-rule)' }}
      >
        <button
          type="button"
          onClick={() => setPlaying(!isPlaying)}
          disabled={durationMs === 0}
          className="rounded border px-3 py-1 disabled:opacity-40"
          style={{ borderColor: 'var(--color-rule)' }}
          data-testid="play"
          data-playing={isPlaying}
        >
          {isPlaying ? 'Pause' : 'Play'}
        </button>
        <span className="font-mono tabular-nums" style={{ color: 'var(--color-ink-2)' }}>
          {formatTimecode(durationMs, { withMillis: true })}
        </span>

        <span
          className="ml-auto font-mono uppercase tracking-widest"
          style={{ color: 'var(--color-ink-2)' }}
        >
          {/* AI tools land in M4. Named, not mocked. */}
          Captions · Smart trim · Colour — M4
        </span>

        <span style={{ color: 'var(--color-ink-2)' }} data-testid="credits">
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
          className="rounded border px-2 py-1"
          style={{ borderColor: 'var(--color-rule)' }}
          data-testid="sign-out"
        >
          Sign out
        </button>
      </header>

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
          <div
            className="flex h-16 shrink-0 items-center border-t px-4 text-xs"
            style={{ borderColor: 'var(--color-rule)', color: 'var(--color-ink-2)' }}
          >
            <span className="font-mono uppercase tracking-widest">
              Inspector — M3 · project <code>{projectId}</code>
            </span>
          </div>
        </div>
      </div>

      <div className="h-48 shrink-0">
        <Timeline />
      </div>
    </div>
  )
}

'use client'

/**
 * Loading a project, and keeping it saved.
 *
 * The state machine lives in `autosave.ts` and is tested there. This is the
 * wiring: it opens the project, feeds every commit to the scheduler, and hangs
 * the flush points off the browser events that actually fire.
 *
 * ⚠️ **`sendBeacon` cannot do this job.** `docs/04-frontend-architecture.md` §6
 * says to flush with it on `pagehide`, and it turns out `sendBeacon` only ever
 * issues a POST — the save is a PATCH, so the request would be rejected. The
 * equivalent that works is `fetch(..., { keepalive: true })`, which survives
 * the page being torn down and can carry any method. It has a **64 KB body
 * limit**, which a caption-heavy timeline will exceed, so this is a
 * best-effort last resort and not the safety net. The real protection is the
 * two-second debounce plus the flush on `visibilitychange`, which fires
 * reliably where `pagehide` does not — and the IndexedDB mirror, deferred in
 * the checklist, is the proper answer.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { Autosave } from '@/editor/state/autosave'
import { useEditor } from '@/editor/state/store'
import type { TimelineDocument } from '@/editor/state/timeline-document'
import { API_BASE_URL, getAccessToken } from '@/lib/api/client'
import { createProject, getProject, saveTimeline } from '@/lib/api/endpoints'

export type SaveStatus = 'idle' | 'loading' | 'dirty' | 'saving' | 'saved' | 'conflict' | 'error'

export interface PersistenceState {
  status: SaveStatus
  /** Set when another tab saved first. Null the rest of the time. */
  conflictVersion: number | null
  /** Discard the other version and force this document over the top of it. */
  keepMine: () => Promise<void>
  /** Throw this document away and load what the server has. */
  loadTheirs: () => Promise<void>
  /** Save now — ⌘S, or before an action that must not race the debounce. */
  flush: () => Promise<void>
}

export function useProjectPersistence(projectId: string): PersistenceState {
  const [status, setStatus] = useState<SaveStatus>('loading')
  const [conflictVersion, setConflictVersion] = useState<number | null>(null)
  const autosaveRef = useRef<Autosave | null>(null)

  const open = useCallback(async () => {
    setStatus('loading')
    // A route segment that is not a real project id — `/editor/new`, and the
    // `/editor/e2e` and `/editor/scratch` the end-to-end run and the dev flow
    // have always used — means "open a fresh project". Before M3 those URLs
    // opened a browser-only timeline that was gone on reload; now they create
    // a real one and swap the id into the address bar, so reloading comes back
    // to the same project rather than making another.
    const project = projectId.startsWith('prj_')
      ? await getProject(projectId)
      : await createProject({ title: 'Untitled project', aspectRatio: '9:16' })
    if (project.id !== projectId && typeof window !== 'undefined') {
      window.history.replaceState(null, '', `/editor/${project.id}`)
    }
    useEditor.getState().load({
      projectId: project.id,
      timeline: project.timeline,
      version: project.version,
    })
    setConflictVersion(null)
    setStatus('saved')
  }, [projectId])

  // ---------------------------------------------------------------- open
  useEffect(() => {
    void open().catch(() => setStatus('error'))
  }, [open])

  // ------------------------------------------------------------- autosave
  useEffect(() => {
    const autosave = new Autosave({
      read: () => {
        const state = useEditor.getState()
        if (!state.projectId) return null
        return {
          projectId: state.projectId,
          timeline: state.timeline,
          version: state.version,
          isDirty: state.isDirty,
          isDragging: state.drag !== null,
        }
      },
      save: async ({ projectId: id, timeline, version }) => {
        setStatus('saving')
        return saveTimeline(id, { timeline, version })
      },
      onSaved: (version, savedTimeline) => {
        useEditor.getState().markSaved(version, savedTimeline)
        setStatus(useEditor.getState().isDirty ? 'dirty' : 'saved')
      },
      onConflict: (version) => {
        setConflictVersion(version)
        setStatus('conflict')
      },
      onError: () => setStatus('error'),
    })
    autosaveRef.current = autosave

    // Every commit marks the document dirty; that transition is the signal.
    const unsubscribe = useEditor.subscribe((state, previous) => {
      if (state.isDirty && !previous.isDirty) {
        setStatus((current) => (current === 'conflict' ? current : 'dirty'))
      }
      if (state.timeline !== previous.timeline) autosave.schedule()
    })

    return () => {
      unsubscribe()
      autosave.stop()
      autosaveRef.current = null
    }
  }, [])

  // ---------------------------------------------------- flush on the way out
  useEffect(() => {
    const flush = () => void autosaveRef.current?.flush()

    const onVisibility = () => {
      // The one that fires when a laptop lid closes or a tab is backgrounded.
      // `pagehide` does not, on mobile especially.
      if (document.visibilityState === 'hidden') flush()
    }
    const onPageHide = () => {
      const state = useEditor.getState()
      if (!state.isDirty || !state.projectId || state.drag) return
      beaconSave(state.projectId, state.timeline, state.version)
    }

    window.addEventListener('blur', flush)
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('pagehide', onPageHide)
    return () => {
      window.removeEventListener('blur', flush)
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('pagehide', onPageHide)
    }
  }, [])

  const keepMine = useCallback(async () => {
    // Take the server's version number without its document, so the next save
    // is accepted and overwrites it. The user was shown both and chose.
    const current = await getProject(projectId)
    useEditor.setState({ version: current.version, isDirty: true })
    autosaveRef.current?.resume()
    setConflictVersion(null)
    setStatus('dirty')
    await autosaveRef.current?.flush()
  }, [projectId])

  const loadTheirs = useCallback(async () => {
    await open()
    autosaveRef.current?.resume()
  }, [open])

  const flush = useCallback(async () => {
    await autosaveRef.current?.flush()
  }, [])

  return { status, conflictVersion, keepMine, loadTheirs, flush }
}

/**
 * The last-resort save, issued while the page is being destroyed.
 *
 * Not `request()` from the API client: that refreshes and retries on a 401,
 * and neither of those can complete during unload. This is one shot with
 * whatever token is already in memory.
 */
function beaconSave(projectId: string, timeline: TimelineDocument, version: number): void {
  const token = getAccessToken()
  if (!token) return
  try {
    void fetch(`${API_BASE_URL}/projects/${projectId}`, {
      method: 'PATCH',
      keepalive: true,
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ timeline, version }),
    })
  } catch {
    // Nothing useful to do: the page is going away, and the two-second
    // debounce is what this is a backstop for.
  }
}

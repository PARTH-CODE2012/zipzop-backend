/**
 * Autosave — docs/04-frontend-architecture.md §6.
 *
 * Five rules, and each exists because of a specific way this goes wrong:
 *
 * 1. **Debounced two seconds.** Saving per keystroke or per nudge would send a
 *    request for every frame of a drag.
 * 2. **One save in flight at a time.** Two overlapping PATCHes carry the same
 *    `version`, so the second is guaranteed a 409 against the first — the
 *    client would manufacture its own conflicts.
 * 3. **Never mid-drag.** The document does not yet contain the gesture, so a
 *    save during one stores the position the clip was dragged *from*.
 * 4. **A 409 stops the loop.** There is no automatic merge: two timelines
 *    cannot be reconciled without knowing which edit the user meant. The
 *    conflict is handed up and autosave goes quiet until it is resolved.
 * 5. **Flushed on blur and on `visibilitychange`.** Closing a laptop lid is
 *    the common case, and it never reaches `pagehide`.
 *
 * The class takes its dependencies rather than importing them, so the whole
 * state machine is testable on fake timers with no network and no React.
 */

import type { TimelineDocument } from '@/editor/state/timeline-document'

export const AUTOSAVE_DELAY_MS = 2_000

export interface AutosaveSnapshot {
  projectId: string
  timeline: TimelineDocument
  version: number
  isDirty: boolean
  isDragging: boolean
}

export interface AutosaveDeps {
  /** The current state of the world, read at the moment a save is attempted. */
  read: () => AutosaveSnapshot | null
  save: (input: {
    projectId: string
    timeline: TimelineDocument
    version: number
  }) => Promise<{ version: number }>
  onSaved: (version: number) => void
  /** Another tab saved first. `currentVersion` is what to re-fetch. */
  onConflict: (currentVersion: number) => void
  onError?: (error: unknown) => void
  delayMs?: number
}

type Timer = ReturnType<typeof setTimeout>

export class Autosave {
  private timer: Timer | null = null
  private inFlight = false
  /** A change arrived while a save was running; save again when it lands. */
  private again = false
  private stopped = false
  private conflicted = false

  constructor(private readonly deps: AutosaveDeps) {}

  private get delay(): number {
    return this.deps.delayMs ?? AUTOSAVE_DELAY_MS
  }

  /** Call whenever the document changes. Cheap, and safe to call per commit. */
  schedule(): void {
    if (this.stopped || this.conflicted) return
    if (this.timer !== null) clearTimeout(this.timer)
    this.timer = setTimeout(() => {
      this.timer = null
      void this.run()
    }, this.delay)
  }

  /** Save now if there is anything to save — blur, tab hidden, explicit ⌘S. */
  async flush(): Promise<void> {
    if (this.timer !== null) {
      clearTimeout(this.timer)
      this.timer = null
    }
    await this.run()
  }

  /**
   * Called after a conflict has been resolved — the user picked a side and the
   * store was reloaded at the current version.
   */
  resume(): void {
    this.conflicted = false
  }

  stop(): void {
    this.stopped = true
    if (this.timer !== null) clearTimeout(this.timer)
    this.timer = null
  }

  /** For tests and for the interface's "saving…" indicator. */
  get isSaving(): boolean {
    return this.inFlight
  }

  get isBlocked(): boolean {
    return this.conflicted
  }

  private async run(): Promise<void> {
    if (this.stopped || this.conflicted) return

    if (this.inFlight) {
      // Rule 2. Remember that more work arrived rather than starting a second
      // request that is guaranteed to conflict with the first.
      this.again = true
      return
    }

    const snapshot = this.deps.read()
    if (!snapshot || !snapshot.isDirty) return

    if (snapshot.isDragging) {
      // Rule 3. Try again after the gesture rather than storing the position
      // the clip was dragged from.
      this.schedule()
      return
    }

    this.inFlight = true
    try {
      const result = await this.deps.save({
        projectId: snapshot.projectId,
        timeline: snapshot.timeline,
        version: snapshot.version,
      })
      this.deps.onSaved(result.version)
    } catch (error) {
      const conflict = asConflict(error)
      if (conflict !== null) {
        this.conflicted = true
        this.again = false
        this.deps.onConflict(conflict)
        return
      }
      // Anything else — offline, a 500, a dropped connection. The document is
      // still dirty, so the next change reschedules; there is no backoff loop
      // here because a failed save that keeps retrying on its own is how a
      // broken tab hammers an API it cannot reach.
      this.deps.onError?.(error)
    } finally {
      this.inFlight = false
    }

    if (this.again) {
      this.again = false
      this.schedule()
    }
  }
}

/** `409 VERSION_CONFLICT` carries the current version in `details`. */
export function asConflict(error: unknown): number | null {
  const candidate = error as { code?: unknown; details?: { currentVersion?: unknown } }
  if (candidate?.code !== 'VERSION_CONFLICT') return null
  const version = candidate.details?.currentVersion
  return typeof version === 'number' ? version : 0
}

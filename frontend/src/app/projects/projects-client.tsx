'use client'

/**
 * The project list.
 *
 * M4.5 item 1, and the reason it was marked 🔴 and *"do this one first
 * regardless of what happens to the rest of this document"*: this page rendered
 * one heading and the words *"Built in M3."* It was a stub written before M3 and
 * never replaced when M3 shipped. The only link into the product from the home
 * page points here, and nothing anywhere linked to `/editor/scratch` — so a
 * person who did not already know that URL could not reach the editor at all.
 *
 * The document offered two ways out: redirect to a new project (ten minutes), or
 * build the real list (half a day). This is the second, because `GET /projects`
 * already exists and returns everything the page needs, and because a redirect
 * would have made *"open my project from last week"* impossible — which is a
 * new dead end in place of the old one.
 *
 * The account gate is the same one the editor uses. Signed out, this shows the
 * sign-in panel rather than an empty list, because an empty list is what a new
 * account looks like and the two must not be confused.
 */

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'

import { AuthPanel } from '@/account/AuthPanel'
import { useSession } from '@/account/session'
import { IconCopy, IconMovie, IconTrash } from '@/editor/icons'
import { formatTimecode } from '@/editor/timeline/scale'
import {
  createProject,
  deleteProject,
  duplicateProject,
  listProjects,
  type ProjectSummary,
} from '@/lib/api/endpoints'

type LoadState = 'loading' | 'ready' | 'error'

export function ProjectsClient() {
  const { status } = useSession()

  if (status === 'restoring') {
    return (
      <Centered>
        <span style={{ color: 'var(--color-ink-2)' }}>Checking your session…</span>
      </Centered>
    )
  }

  if (status === 'signed-out') {
    return (
      <Centered>
        <AuthPanel />
      </Centered>
    )
  }

  return <List />
}

function List() {
  const router = useRouter()
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [state, setState] = useState<LoadState>('loading')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const page = await listProjects({ limit: 50 })
      setProjects(page.items)
      setState('ready')
    } catch {
      setState('error')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  /**
   * Create, then navigate to the id the server gave back.
   *
   * Not to `/editor/scratch`. That route works — a segment that is not a real
   * project id means *open a fresh project* — but it creates the project on the
   * editor's first render and then swaps the id into the address bar. Doing it
   * here means the list is already correct when the user comes back, and a
   * failure to create is reported on the page they are looking at rather than
   * inside an editor that then has nothing to edit.
   */
  async function onCreate() {
    if (busy) return
    setBusy(true)
    try {
      const project = await createProject({ title: 'Untitled project', aspectRatio: '9:16' })
      router.push(`/editor/${project.id}`)
    } catch {
      setState('error')
      setBusy(false)
    }
  }

  return (
    <main className="mx-auto max-w-4xl px-6 py-12">
      <div className="flex items-baseline justify-between gap-4">
        <h1 className="text-2xl font-bold">Projects</h1>
        <button
          type="button"
          onClick={() => void onCreate()}
          disabled={busy}
          className="px-4 py-2 text-sm disabled:opacity-50"
          style={{
            borderRadius: 'var(--radius-pill)',
            background: 'var(--color-accent)',
            color: 'var(--color-accent-ink)',
            fontWeight: 600,
          }}
          data-testid="new-project"
        >
          {busy ? 'Creating…' : 'New project'}
        </button>
      </div>

      {state === 'loading' && (
        <p className="mt-8 text-sm" style={{ color: 'var(--color-ink-3)' }}>
          Loading your projects…
        </p>
      )}

      {state === 'error' && (
        <p className="mt-8 text-sm" role="alert" style={{ color: 'var(--color-danger, #f87171)' }}>
          We could not reach the server. Check it is running, then reload.
        </p>
      )}

      {state === 'ready' && projects.length === 0 && (
        <div className="mt-10 flex flex-col items-start gap-3" data-testid="projects-empty">
          <p style={{ color: 'var(--color-ink-2)' }}>Nothing here yet.</p>
          <p className="text-sm" style={{ color: 'var(--color-ink-3)' }}>
            A project holds a timeline, the clips on it and everything you do to them. Make one and
            drop a video in.
          </p>
        </div>
      )}

      {state === 'ready' && projects.length > 0 && (
        <ul className="mt-8 flex flex-col gap-2" data-testid="projects-list">
          {projects.map((project) => (
            <ProjectRow
              key={project.id}
              project={project}
              onOpen={() => router.push(`/editor/${project.id}`)}
              onChanged={() => void load()}
            />
          ))}
        </ul>
      )}
    </main>
  )
}

function ProjectRow({
  project,
  onOpen,
  onChanged,
}: {
  project: ProjectSummary
  onOpen: () => void
  onChanged: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  const [working, setWorking] = useState(false)

  async function run(action: () => Promise<unknown>) {
    if (working) return
    setWorking(true)
    try {
      await action()
      onChanged()
    } finally {
      setWorking(false)
      setConfirming(false)
    }
  }

  return (
    <li
      className="flex items-center gap-4 px-3 py-3"
      style={{
        border: '1px solid var(--color-rule)',
        borderRadius: 'var(--radius-md, 8px)',
        background: 'var(--color-surface-2)',
        opacity: working ? 0.5 : 1,
      }}
      data-testid="project-row"
      data-project-id={project.id}
    >
      {/* The thumbnail is whatever ingest produced for the first clip. A
          project with nothing on its timeline has none, and a grey tile is a
          better answer than a broken image. */}
      <div
        className="flex h-12 w-20 shrink-0 items-center justify-center overflow-hidden"
        style={{ background: 'var(--color-surface-3)', borderRadius: 'var(--radius-sm)' }}
      >
        {project.thumbnailUrl ? (
          /* A signed URL from object storage on another origin. `next/image`
             would need that host in its config and would proxy every thumbnail
             through our own server — which is bandwidth we pay for to optimise
             an image that is already a 160px ingest thumbnail. */
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={project.thumbnailUrl}
            alt=""
            className="h-full w-full object-cover"
            loading="lazy"
          />
        ) : (
          <IconMovie size={18} aria-hidden="true" style={{ color: 'var(--color-ink-faint)' }} />
        )}
      </div>

      <button
        type="button"
        onClick={onOpen}
        className="flex min-w-0 flex-1 flex-col items-start gap-0.5 text-left"
        data-testid="open-project"
      >
        <span className="truncate" style={{ color: 'var(--color-ink)', fontWeight: 600 }}>
          {project.title}
        </span>
        <span className="tnum text-xs" style={{ color: 'var(--color-ink-3)' }}>
          {project.aspectRatio} · {formatTimecode(project.durationMs)} · edited{' '}
          {relativeTime(project.updatedAt)}
        </span>
      </button>

      <button
        type="button"
        onClick={() => void run(() => duplicateProject(project.id))}
        title="Duplicate this project"
        aria-label={`Duplicate ${project.title}`}
        className="flex h-8 w-8 items-center justify-center"
        style={{ color: 'var(--color-ink-3)', borderRadius: 'var(--radius-sm)' }}
        data-testid="duplicate-project"
      >
        <IconCopy size={15} aria-hidden="true" />
      </button>

      {/* Two presses to delete, and the second one says what it will do.
          Deleting is soft server-side, but the user does not know that and
          should not have to. */}
      {confirming ? (
        <button
          type="button"
          onClick={() => void run(() => deleteProject(project.id))}
          className="px-2 py-1 text-xs"
          style={{
            borderRadius: 'var(--radius-sm)',
            background: 'var(--color-danger, #f87171)',
            color: 'var(--color-surface, #0b0b0f)',
            fontWeight: 600,
          }}
          data-testid="confirm-delete"
        >
          Delete for good?
        </button>
      ) : (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          title="Delete this project"
          aria-label={`Delete ${project.title}`}
          className="flex h-8 w-8 items-center justify-center"
          style={{ color: 'var(--color-ink-3)', borderRadius: 'var(--radius-sm)' }}
          data-testid="delete-project"
        >
          <IconTrash size={15} aria-hidden="true" />
        </button>
      )}
    </li>
  )
}

function Centered({ children }: { children: React.ReactNode }) {
  return <main className="flex min-h-screen items-center justify-center px-6">{children}</main>
}

/**
 * "3 minutes ago", without a date library.
 *
 * Rounded down and capped at a week, after which the date itself is more useful
 * than a count of days — nobody reads "edited 34 days ago" as a time.
 */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const then = Date.parse(iso)
  if (Number.isNaN(then)) return 'recently'

  const seconds = Math.max(0, Math.round((now - then) / 1000))
  if (seconds < 60) return 'just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} day${days === 1 ? '' : 's'} ago`
  return new Date(then).toLocaleDateString()
}

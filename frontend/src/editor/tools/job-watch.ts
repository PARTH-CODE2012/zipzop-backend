/**
 * Watching a job to completion.
 *
 * **The socket is an optimisation. The polling is the contract.**
 * `docs/10-m4-readiness.md` §3 is explicit that `GET /jobs/{id}` must work
 * standalone and is what a client falls back to, so the fallback is built as a
 * first-class path here rather than bolted on after the socket works. A laptop
 * that sleeps, a tunnel, a proxy that drops idle connections — all of them are
 * ordinary, and none of them may lose a result the user has paid for.
 *
 * The rule: **the socket only ever makes the poll happen sooner.** Every event
 * it delivers is treated as a hint to re-read the job, never as the job's new
 * state. That means a missed event costs latency and nothing else, and there is
 * exactly one code path that decides a job is finished.
 */

import { getJob, listJobs } from '@/lib/api/endpoints'
import type { JobResponse } from '@/lib/api/endpoints'

/** Contract §6: the states a job never leaves once it reaches them. */
export const TERMINAL_STATUSES = ['succeeded', 'failed', 'cancelled'] as const

export function isFinished(job: JobResponse): boolean {
  return (TERMINAL_STATUSES as readonly string[]).includes(job.status)
}

/**
 * How often to re-read a running job when nothing else prompts it.
 *
 * Three seconds, from the checklist. Fast enough that a ten-second colour
 * analysis does not feel stalled, slow enough that a hundred idle tabs are not
 * a load problem of their own.
 */
export const POLL_INTERVAL_MS = 3_000

export interface WatchOptions {
  onUpdate?: (job: JobResponse) => void
  signal?: AbortSignal
  intervalMs?: number
}

/**
 * Follow one job until it finishes. Resolves with its final state.
 *
 * Polling only — `watchJobs` adds the socket on top. Kept separate because this
 * is the part that must be correct on its own.
 */
export async function pollJob(jobId: string, options: WatchOptions = {}): Promise<JobResponse> {
  const interval = options.intervalMs ?? POLL_INTERVAL_MS

  for (;;) {
    if (options.signal?.aborted) throw new DOMException('aborted', 'AbortError')
    const job = await getJob(jobId)
    options.onUpdate?.(job)
    if (isFinished(job)) return job
    await delay(interval, options.signal)
  }
}

/** A connection to the event stream, and the way to stop it. */
export interface JobStream {
  close(): void
}

/**
 * Open the WebSocket and call `onHint` whenever it says something changed.
 *
 * Deliberately returns **no job state**. The message carries a job id and a
 * progress number, and the temptation is to apply them directly — but then two
 * paths write the same state, and the one that arrives out of order wins. The
 * hint means "read the job now"; the read is what decides anything.
 *
 * A socket that fails to open is not an error. It is a slower session.
 */
export function openJobStream(
  token: string,
  onHint: (jobId: string) => void,
  options: { baseUrl?: string; onOpen?: () => void; onClose?: () => void } = {},
): JobStream {
  const base = options.baseUrl ?? apiBase()
  const url = `${base.replace(/^http/, 'ws')}/ws?token=${encodeURIComponent(token)}`

  let socket: WebSocket | null = null
  let closed = false

  try {
    socket = new WebSocket(url)
  } catch {
    // No socket. The poll below carries the session on its own.
    return { close: () => {} }
  }

  socket.onopen = () => options.onOpen?.()
  socket.onclose = () => {
    if (!closed) options.onClose?.()
  }
  socket.onerror = () => {
    // Nothing to do and nothing to report: the fallback is already running.
  }
  socket.onmessage = (event) => {
    try {
      const message = JSON.parse(String(event.data)) as { type?: string; jobId?: string }
      // The heartbeat exists to keep proxies from closing an idle connection.
      if (!message.jobId || message.type === 'ping') return
      onHint(message.jobId)
    } catch {
      // A message we cannot parse is a message from a version we do not know.
      // Ignoring it is correct; the poll still finishes the job.
    }
  }

  return {
    close() {
      closed = true
      socket?.close()
    },
  }
}

/**
 * Everything the editor needs while jobs are in flight: the socket for
 * latency, the poll for correctness, and one re-sync on reconnect.
 *
 * `GET /jobs?status=running` on reconnect is the part that makes a dropped
 * connection invisible: whatever finished while the socket was down is found
 * by the catch-up call rather than waited for forever.
 */
export function watchJobs(options: {
  token: string
  projectId?: string
  onUpdate: (job: JobResponse) => void
  intervalMs?: number
}): JobStream {
  const watching = new Set<string>()
  let timer: ReturnType<typeof setInterval> | null = null

  const readOne = async (jobId: string) => {
    try {
      const job = await getJob(jobId)
      options.onUpdate(job)
      if (isFinished(job)) watching.delete(jobId)
    } catch {
      // A failed read is retried by the next tick. Surfacing it would put an
      // error in front of the user for something that fixes itself.
    }
  }

  const resync = async () => {
    try {
      const page = await listJobs(
        options.projectId
          ? { projectId: options.projectId, status: 'queued,running' }
          : { status: 'queued,running' },
      )
      for (const job of page.items) {
        watching.add(job.id)
        options.onUpdate(job)
      }
    } catch {
      /* the next tick tries again */
    }
  }

  const stream = openJobStream(options.token, (jobId) => void readOne(jobId), {
    // Every reconnect re-syncs, because the gap is exactly when something
    // finished without anyone hearing.
    onOpen: () => void resync(),
  })

  timer = setInterval(() => {
    for (const jobId of watching) void readOne(jobId)
  }, options.intervalMs ?? POLL_INTERVAL_MS)

  void resync()

  return {
    close() {
      stream.close()
      if (timer) clearInterval(timer)
    },
  }
}

function apiBase(): string {
  return process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8123/v1'
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms)
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(timer)
        reject(new DOMException('aborted', 'AbortError'))
      },
      { once: true },
    )
  })
}

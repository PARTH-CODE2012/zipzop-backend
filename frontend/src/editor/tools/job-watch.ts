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
 * The server's close code for "the token behind this socket has expired"
 * (contract §8). Not a failure: the socket lives exactly as long as the access
 * token that asked for it (M7-18), so a session longer than fifteen minutes
 * sees this routinely, and answers it with a fresh ticket straight away.
 */
export const TOKEN_EXPIRED_CLOSE = 4001

/** Waits between reconnect attempts after anything else closes the socket. */
export const RECONNECT_DELAYS_MS = [1_000, 2_000, 5_000, 10_000, 30_000] as const

/** A socket that stayed up this long was a working connection, not a flap. */
const STABLE_AFTER_MS = 10_000

export interface StreamOptions {
  baseUrl?: string
  onOpen?: () => void
  onClose?: () => void
  /** The browser's `WebSocket` unless a test hands in its own. */
  createSocket?: (url: string) => WebSocket
}

/**
 * Open the WebSocket and call `onHint` whenever it says something changed.
 *
 * Deliberately returns **no job state**. The message carries a job id and a
 * progress number, and the temptation is to apply them directly — but then two
 * paths write the same state, and the one that arrives out of order wins. The
 * hint means "read the job now"; the read is what decides anything.
 *
 * **Opened with a one-time ticket, never the access token (M7-19).** Whatever
 * authenticates a socket rides in its URL, and URLs are what access logs keep.
 * `getTicket` is asked again for every connection, because a ticket is spent
 * by the handshake that uses it.
 *
 * **It comes back by itself.** Until M7 a closed socket stayed closed and the
 * session carried on at polling speed. Now the server closes it on purpose when
 * the token expires, so reconnecting is part of the contract: at once after a
 * `4001`, with backoff after anything else. A signed-out or suspended account
 * cannot get a ticket, and that is where it stops.
 *
 * A socket that fails to open is not an error. It is a slower session.
 */
export function openJobStream(
  getTicket: () => Promise<string>,
  onHint: (jobId: string) => void,
  options: StreamOptions = {},
): JobStream {
  const base = (options.baseUrl ?? apiBase()).replace(/^http/, 'ws')
  const createSocket = options.createSocket ?? ((url: string) => new WebSocket(url))

  let socket: WebSocket | null = null
  let closed = false
  let failures = 0
  let retryTimer: ReturnType<typeof setTimeout> | null = null

  const retry = (immediately: boolean) => {
    if (closed) return
    const wait = immediately
      ? 0
      : RECONNECT_DELAYS_MS[Math.min(failures, RECONNECT_DELAYS_MS.length - 1)]
    failures += 1
    retryTimer = setTimeout(() => {
      retryTimer = null
      void connect()
    }, wait)
  }

  const connect = async () => {
    let ticket: string
    try {
      ticket = await getTicket()
    } catch (error) {
      // 401 after a failed refresh, or 403 for an account that is no longer
      // active: there is no ticket to be had, and asking again will not change
      // that. Anything else — offline, a 5xx — is worth another try later.
      const status = (error as { status?: number } | null)?.status
      if (status === 401 || status === 403) return
      retry(false)
      return
    }
    if (closed) return

    let opened = 0
    let ws: WebSocket
    try {
      ws = createSocket(`${base}/ws?ticket=${encodeURIComponent(ticket)}`)
    } catch {
      retry(false)
      return
    }
    socket = ws

    ws.onopen = () => {
      opened = Date.now()
      options.onOpen?.()
    }
    ws.onclose = (event) => {
      socket = null
      if (closed) return
      options.onClose?.()
      // A connection that held is a fresh start for the backoff, so a socket
      // closed at token expiry after fifteen good minutes reconnects at once.
      // One closed with 4001 the instant it opened has not held, and backs off
      // like any other flap rather than spinning.
      if (opened > 0 && Date.now() - opened >= STABLE_AFTER_MS) failures = 0
      retry(event.code === TOKEN_EXPIRED_CLOSE && failures === 0)
    }
    ws.onerror = () => {
      // Nothing to do and nothing to report: the fallback is already running,
      // and `onclose` follows with the code that decides what happens next.
    }
    ws.onmessage = (event) => {
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
  }

  void connect()

  return {
    close() {
      closed = true
      if (retryTimer) clearTimeout(retryTimer)
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
  getTicket: () => Promise<string>
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

  const stream = openJobStream(options.getTicket, (jobId) => void readOne(jobId), {
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

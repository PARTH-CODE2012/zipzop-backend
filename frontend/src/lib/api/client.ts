/**
 * API client.
 *
 * Two things this handles that every caller would otherwise repeat: the error
 * envelope, and the one-shot token refresh on a 401.
 *
 * Response types come from `generated.ts`, produced by `pnpm generate:types`
 * from the committed `openapi.json`. Do not hand-write request or response
 * shapes — a drift between the two sides should be a build error, not a bug
 * found at integration.
 */

/**
 * Where the API is.
 *
 * `NEXT_PUBLIC_API_BASE_URL` is exported by `scripts/ports.sh` before the dev
 * server starts, so a port that had to move is followed here rather than
 * guessed. The fallback matches the default in `.env.example`; it is only used
 * by a bare `pnpm dev` started outside the dev flow.
 */
const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8123/v1'

import { assertNotProduction, fixtureFor, isDemo, MISS } from '@/lib/api/fixtures'

assertNotProduction()

/** Mirrors docs/05-api-contract.md §1. `code` is stable; `message` is not. */
export interface ApiErrorBody {
  code: string
  message: string
  details?: Record<string, unknown>
}

export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly details: Record<string, unknown>

  constructor(status: number, body: ApiErrorBody) {
    super(body.message)
    this.name = 'ApiError'
    this.status = status
    this.code = body.code
    this.details = body.details ?? {}
  }

  /** Branch on this, never on `message`. */
  is(code: string): boolean {
    return this.code === code
  }
}

let accessToken: string | null = null
let refreshInFlight: Promise<boolean> | null = null

export function setAccessToken(token: string | null): void {
  accessToken = token
}

/**
 * The current access token, for the one caller that cannot go through
 * `request()`: the unload flush, which builds its own `fetch` with
 * `keepalive` because the helper's refresh-and-retry cannot run while the page
 * is being torn down.
 */
export function getAccessToken(): string | null {
  return accessToken
}

export { BASE_URL as API_BASE_URL }

/**
 * Refresh once, and only once, no matter how many requests 401 at the same
 * moment. Without this, a page that fires six requests on mount performs six
 * refreshes and rotates the token out from under five of them.
 */
async function refreshOnce(): Promise<boolean> {
  refreshInFlight ??= (async () => {
    try {
      const res = await fetch(`${BASE_URL}/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
      })
      if (!res.ok) return false
      const data = (await res.json()) as { accessToken: string }
      accessToken = data.accessToken
      return true
    } catch {
      return false
    } finally {
      refreshInFlight = null
    }
  })()
  return refreshInFlight
}

interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown
  /** Required on POST /jobs and POST /media/uploads — see contract §1. */
  idempotencyKey?: string
  /** Internal: prevents an infinite refresh loop. */
  _retried?: boolean
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, idempotencyKey, _retried, headers, ...rest } = options

  /**
   * The fixture server — off unless `NEXT_PUBLIC_DEMO=1`, and refused outright
   * in a production build (`assertNotProduction`).
   *
   * One branch at one call site, because every request already funnels through
   * here. A route with no fixture returns `MISS` and falls through to the real
   * `fetch` below, so an unmocked endpoint fails visibly against the absent
   * server rather than quietly returning something invented.
   */
  if (isDemo()) {
    const fixture = fixtureFor(rest.method ?? 'GET', path, body)
    if (fixture !== MISS) {
      // A tick of latency, so loading states are visible rather than skipped.
      await new Promise((resolve) => setTimeout(resolve, 120))
      return fixture as T
    }
  }

  const res = await fetch(`${BASE_URL}${path}`, {
    ...rest,
    credentials: 'include',
    headers: {
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}),
      ...headers,
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  })

  if (res.status === 204) return undefined as T

  if (!res.ok) {
    const payload = (await res.json().catch(() => null)) as { error?: ApiErrorBody } | null
    const error = new ApiError(
      res.status,
      payload?.error ?? { code: 'UNKNOWN', message: 'Something went wrong.' },
    )

    // One retry, only for an expired token, only if we have not already tried.
    if (error.is('TOKEN_EXPIRED') && !_retried && (await refreshOnce())) {
      return request<T>(path, { ...options, _retried: true })
    }
    throw error
  }

  return (await res.json()) as T
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) => request<T>(path, { ...options, method: 'GET' }),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'POST', body }),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'PATCH', body }),
  delete: <T>(path: string, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'DELETE' }),
}

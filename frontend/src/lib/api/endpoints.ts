/**
 * Typed calls, one per endpoint in the contract.
 *
 * Every request and response type is an alias into `generated.ts`, which is
 * produced from the committed `openapi.json`. Nothing here hand-writes a
 * shape: when the backend changes the contract, this file stops compiling,
 * which is the whole point of generating the types in the first place.
 */

import { api, request, setAccessToken } from '@/lib/api/client'
import type { components } from '@/lib/api/generated'

type Schemas = components['schemas']

export type SessionResponse = Schemas['SessionResponse']
export type RefreshResponse = Schemas['RefreshResponse']
export type MeResponse = Schemas['MeResponse']
export type AssetResponse = Schemas['AssetResponse']
export type UploadResponse = Schemas['UploadResponse']
export type PeaksResponse = Schemas['PeaksResponse']
export type RegisterRequest = Schemas['RegisterRequest']
export type LoginRequest = Schemas['LoginRequest']

/** contract §3 — `pending_upload` | `probing` | `ready` | `failed`. */
export type AssetStatus = AssetResponse['status']

// --------------------------------------------------------------------- auth

export async function register(body: RegisterRequest): Promise<SessionResponse> {
  const session = await api.post<SessionResponse>('/auth/register', body)
  setAccessToken(session.accessToken)
  return session
}

export async function login(body: LoginRequest): Promise<SessionResponse> {
  const session = await api.post<SessionResponse>('/auth/login', body)
  setAccessToken(session.accessToken)
  return session
}

export async function logout(): Promise<void> {
  try {
    await request<void>('/auth/logout', { method: 'POST' })
  } finally {
    // Clear the local token even if the call failed. Leaving it set after the
    // user pressed sign out is worse than a stale server-side session: the
    // interface would keep behaving as if they were still in.
    setAccessToken(null)
  }
}

/**
 * Exchange the refresh cookie for a new access token.
 *
 * Used on page load to find out whether there is still a session. The cookie
 * is httpOnly, so this request is the only way to ask — the client cannot look.
 */
export async function refresh(): Promise<RefreshResponse> {
  const next = await request<RefreshResponse>('/auth/refresh', { method: 'POST' })
  setAccessToken(next.accessToken)
  return next
}

export function me(): Promise<MeResponse> {
  return api.get<MeResponse>('/me')
}

// -------------------------------------------------------------------- media

export function listMedia(params: { limit?: number; cursor?: string } = {}) {
  const query = new URLSearchParams()
  if (params.limit) query.set('limit', String(params.limit))
  if (params.cursor) query.set('cursor', params.cursor)
  const suffix = query.toString() ? `?${query}` : ''
  return api.get<{ items: AssetResponse[]; nextCursor: string | null }>(`/media${suffix}`)
}

export function getMedia(assetId: string): Promise<AssetResponse> {
  return api.get<AssetResponse>(`/media/${assetId}`)
}

export function getPeaks(assetId: string): Promise<PeaksResponse> {
  return api.get<PeaksResponse>(`/media/${assetId}/peaks`)
}

export function deleteMedia(assetId: string): Promise<void> {
  return api.delete<void>(`/media/${assetId}`)
}

export function reserveUpload(
  body: { filename: string; sizeBytes: number; contentType: string },
  idempotencyKey: string,
): Promise<UploadResponse> {
  return api.post<UploadResponse>('/media/uploads', body, { idempotencyKey })
}

export type CompletedPart = Schemas['CompletedPart']

/**
 * `parts` for a multipart upload, `etag` for a single PUT.
 *
 * There is deliberately no `uploadId` here: the server stored it on the asset
 * row when it reserved the upload, so it does not have to trust the client to
 * hand back an identifier for an upload it started itself.
 */
export function completeUpload(
  assetId: string,
  etag: string | null,
  parts: CompletedPart[] | null = null,
): Promise<AssetResponse> {
  return api.post<AssetResponse>(`/media/${assetId}/complete`, { etag, parts })
}

// ----------------------------------------------------------------- projects

export type ProjectResponse = Schemas['ProjectResponse']
export type ProjectSummary = Schemas['ProjectSummary']
export type ProjectSaveResponse = Schemas['ProjectSaveResponse']
export type ProjectAssetRef = Schemas['ProjectAssetRef']
export type CreateProjectRequest = Schemas['CreateProjectRequest']
export type TimelineDocumentWire = Schemas['TimelineDocument']

export function createProject(body: CreateProjectRequest): Promise<ProjectResponse> {
  return api.post<ProjectResponse>('/projects', body)
}

export function listProjects(params: { limit?: number; cursor?: string } = {}) {
  const query = new URLSearchParams()
  if (params.limit) query.set('limit', String(params.limit))
  if (params.cursor) query.set('cursor', params.cursor)
  const suffix = query.toString() ? `?${query}` : ''
  return api.get<{ items: ProjectSummary[]; nextCursor: string | null }>(`/projects${suffix}`)
}

export function getProject(projectId: string): Promise<ProjectResponse> {
  return api.get<ProjectResponse>(`/projects/${projectId}`)
}

/**
 * Autosave — contract §5.
 *
 * `version` is the one the document was loaded or last saved at. A `409
 * VERSION_CONFLICT` means another tab or device saved first, and its
 * `details.currentVersion` says what to re-fetch. **There is no automatic
 * merge**: two timelines cannot be reconciled without knowing which edit the
 * user meant (`docs/04-frontend-architecture.md` §6.1).
 */
export function saveTimeline(
  projectId: string,
  body: { timeline: TimelineDocumentWire; version: number },
): Promise<ProjectSaveResponse> {
  return api.patch<ProjectSaveResponse>(`/projects/${projectId}`, body)
}

/** Renaming does not touch the timeline and does not bump `version`. */
export function updateProjectMetadata(
  projectId: string,
  body: { title?: string; aspectRatio?: CreateProjectRequest['aspectRatio'] },
): Promise<ProjectSaveResponse> {
  return api.patch<ProjectSaveResponse>(`/projects/${projectId}`, body)
}

export function duplicateProject(projectId: string): Promise<ProjectResponse> {
  return api.post<ProjectResponse>(`/projects/${projectId}/duplicate`, {})
}

export function deleteProject(projectId: string): Promise<void> {
  return api.delete<void>(`/projects/${projectId}`)
}

// --------------------------------------------------------------------------
// Jobs — contract §6
// --------------------------------------------------------------------------

export type JobResponse = Schemas['JobResponse']
export type CreateJobRequest = Schemas['CreateJobRequest']
export type EstimateResponse = Schemas['EstimateResponse']
export type JobStatus = Schemas['JobStatus']

/**
 * What a job would cost, without creating it — contract §6.1.
 *
 * Called when a tool panel opens, so the price is on the button before the user
 * commits. `blockedBy` carries the code `POST /jobs` *would* return, which is
 * what lets the button say "Not enough credits" instead of the click doing it.
 */
export function estimateJob(body: CreateJobRequest): Promise<EstimateResponse> {
  return api.post<EstimateResponse>('/jobs/estimate', body)
}

/**
 * Start a job.
 *
 * **The idempotency key is not optional.** A retry after a network timeout is
 * indistinguishable from a second request, and the difference between the two
 * is whether the user is charged twice (contract §1). One key per user
 * *intention* — generated when the button is pressed, reused by every retry of
 * that press.
 */
export function createJob(body: CreateJobRequest, idempotencyKey: string): Promise<JobResponse> {
  return request<JobResponse>('/jobs', {
    method: 'POST',
    body,
    headers: { 'Idempotency-Key': idempotencyKey },
  })
}

export function getJob(jobId: string): Promise<JobResponse> {
  return api.get<JobResponse>(`/jobs/${jobId}`)
}

/**
 * The catch-up call.
 *
 * *"On reconnect the client calls this to catch up on anything it missed while
 * disconnected."* The WebSocket is an optimisation; this is the source of
 * truth, and a client that only listens to the socket is broken the first time
 * a laptop sleeps.
 */
export function listJobs(
  query: { projectId?: string; status?: string; limit?: number } = {},
): Promise<{ items: JobResponse[]; nextCursor: string | null }> {
  const search = new URLSearchParams()
  if (query.projectId) search.set('projectId', query.projectId)
  if (query.status) search.set('status', query.status)
  if (query.limit) search.set('limit', String(query.limit))
  const suffix = search.size > 0 ? `?${search}` : ''
  return api.get<{ items: JobResponse[]; nextCursor: string | null }>(`/jobs${suffix}`)
}

/** `409 JOB_NOT_CANCELLABLE` if it already finished. Refunds in full. */
export function cancelJob(jobId: string): Promise<JobResponse> {
  return api.post<JobResponse>(`/jobs/${jobId}/cancel`, {})
}

/**
 * The result, wherever it lives — contract §6.3.
 *
 * **Both shapes are real and a client must handle both.** Under 256 KB the
 * result is inline; above it `result` is null and `resultUrl` is a signed link
 * to the same JSON in S3. A caption run on anything over about twenty minutes
 * of speech takes the second path, so this is not an edge case to skip.
 */
export async function readJobResult<T = unknown>(job: JobResponse): Promise<T | null> {
  if (job.result) return job.result as T
  if (!job.resultUrl) return null
  // Not through `api`: the URL is pre-signed and points at object storage, so
  // sending our Authorization header to it would leak the access token to a
  // different origin.
  const response = await fetch(job.resultUrl)
  if (!response.ok) return null
  return (await response.json()) as T
}

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

export function completeUpload(assetId: string, etag: string | null): Promise<AssetResponse> {
  return api.post<AssetResponse>(`/media/${assetId}/complete`, { etag, parts: null })
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

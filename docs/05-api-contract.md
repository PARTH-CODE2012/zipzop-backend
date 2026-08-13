# API Contract

**The interface both teams build against. Frontend and backend can work in parallel once this is agreed — and only once it is agreed.**

| | |
|---|---|
| **Version** | 1.0 |
| **Date** | 12 August 2026 |
| **Audience** | Backend and frontend engineers |
| **Depends on** | [`03-backend-architecture.md`](03-backend-architecture.md) |
| **Base URL** | `https://api.zipzop.app/v1` |

> **This document is a contract.** Changing anything in it after work starts breaks someone else's build. Additions are cheap; changes to existing shapes are not. If something here is wrong, say so now.

---

## 1. Conventions

### Format

- JSON in, JSON out. `Content-Type: application/json` unless stated.
- **Field names are `camelCase`.** The database is `snake_case`; the API is not. Translation happens in the serialisation layer.
- Timestamps are RFC 3339 UTC strings: `"2026-08-12T14:32:07Z"`.
- **All durations and positions are integer milliseconds.** Never seconds, never floats. Floating-point time in a video editor produces drift the user sees as clips that will not butt together.
- Identifiers are UUIDs, prefixed in responses for readability: `ast_`, `prj_`, `job_`, `clp_`, `trk_`. The prefix is part of the string.

### Authentication

Every endpoint except `/auth/register`, `/auth/login` and `/auth/refresh` requires:

```http
Authorization: Bearer <access_token>
```

Access tokens last 15 minutes. On `401 TOKEN_EXPIRED` the client refreshes and retries once. A second 401 means sign in again.

### Errors

Every error has the same shape and an HTTP status that matches its meaning.

```json
{
  "error": {
    "code": "INSUFFICIENT_CREDITS",
    "message": "This export costs 40 credits and you have 12.",
    "details": { "required": 40, "available": 12 }
  }
}
```

`code` is stable and machine-readable — **branch on it, never on `message`.** `message` is English, written to be shown to a user, and may be reworded at any time. `details` varies by code and is documented with it in §8.

### Idempotency

`POST /jobs` and `POST /media/uploads` accept:

```http
Idempotency-Key: <client-generated uuid>
```

Replaying a key returns the original response rather than creating a second job. Clients must send one — a retry after a network timeout is otherwise indistinguishable from a second request, and the user gets charged twice.

### Pagination

Cursor-based. Never offsets — a list that changes while being paged skips rows.

```http
GET /v1/projects?limit=20&cursor=eyJpZCI6...
```

```json
{ "items": [ … ], "nextCursor": "eyJpZCI6…" }
```

`nextCursor` is `null` on the last page.

### Rate limits

`429` with `Retry-After` in seconds. Limits in [`03-backend-architecture.md`](03-backend-architecture.md) §9.

---

## 2. Auth

### `POST /auth/register`

```json
{ "email": "sam@example.com", "password": "…", "displayName": "Sam" }
```

`201`:

```json
{
  "user": { "id": "usr_9b1d…", "email": "sam@example.com", "displayName": "Sam", "creditBalance": 100 },
  "accessToken": "eyJhbG…",
  "refreshToken": "rt_8f2c…",
  "expiresIn": 900
}
```

New accounts receive a signup grant. The amount is open decision B; `100` is a placeholder.

### `POST /auth/login`

Same request minus `displayName`, same response. `401 INVALID_CREDENTIALS` on failure — identical for a wrong password and an unknown email, so the endpoint cannot be used to discover which addresses are registered.

### `POST /auth/refresh`

```json
{ "refreshToken": "rt_8f2c…" }
```

Returns a new pair. **The old refresh token is invalidated** — store the new one before using it. Presenting an already-rotated token revokes the whole chain and forces a fresh sign-in; that pattern means a token leaked.

### `POST /auth/logout`

Revokes the presented refresh token. `204`.

### `GET /me`

```json
{
  "id": "usr_9b1d…",
  "email": "sam@example.com",
  "displayName": "Sam",
  "creditBalance": 340,
  "storageBytesUsed": 4823410176,
  "createdAt": "2026-08-01T09:14:22Z"
}
```

---

## 3. Media

### `POST /media/uploads`

Ask for somewhere to put a file. Returns a presigned S3 URL; **the file itself never passes through this API.**

```json
{ "filename": "interview-raw.mp4", "sizeBytes": 891289600, "contentType": "video/mp4" }
```

`201`:

```json
{
  "assetId": "ast_4c8e…",
  "uploadUrl": "https://zipzop-media.s3.eu-west-1.amazonaws.com/originals/…?X-Amz-Signature=…",
  "method": "PUT",
  "headers": { "Content-Type": "video/mp4" },
  "expiresAt": "2026-08-12T14:47:00Z",
  "multipart": null
}
```

For files over 100 MB, `multipart` carries per-part URLs instead:

```json
"multipart": {
  "uploadId": "2~abc…",
  "partSizeBytes": 8388608,
  "parts": [ { "partNumber": 1, "url": "https://…" }, … ]
}
```

Rejected before presigning: unsupported `contentType`, `sizeBytes` over the limit, insufficient storage quota.

### `PUT <uploadUrl>`

Straight to S3. Not our API — no `Authorization` header, and adding one breaks the signature. Progress comes from the browser's own upload events.

### `POST /media/{assetId}/complete`

```json
{ "etag": "\"9b1deb4d3b7d…\"", "parts": null }
```

For multipart, `parts` is `[{ "partNumber": 1, "etag": "\"…\"" }, …]`.

`202` — the asset moves to `probing` and an ingest job starts. Poll the asset or wait for the `asset.ready` socket event.

### `GET /media/{assetId}`

```json
{
  "id": "ast_4c8e…",
  "kind": "video",
  "status": "ready",
  "originalFilename": "interview-raw.mp4",
  "sizeBytes": 891289600,
  "durationMs": 623480,
  "width": 3840,
  "height": 2160,
  "fps": 29.97,
  "videoCodec": "hevc",
  "audioCodec": "aac",
  "audioChannels": 2,
  "proxyUrl":     "https://cdn.zipzop.app/proxies/…/proxy.mp4?Expires=…",
  "thumbnailUrl": "https://cdn.zipzop.app/thumbs/…/thumb.jpg?Expires=…",
  "peaksUrl":     "https://cdn.zipzop.app/peaks/…/peaks.json?Expires=…",
  "derivedFromAssetId": null,
  "createdAt": "2026-08-12T14:31:02Z"
}
```

`status` is one of `pending_upload`, `probing`, `ready`, `failed`. On `failed`, `failureReason` explains why in a sentence.

**The three URLs are signed and expire in one hour.** Re-fetch the asset to renew them; do not persist them in the timeline document or in client storage.

Playback and scrubbing use `proxyUrl` — 480p H.264. The original is only ever touched by the export renderer.

### `GET /media/{assetId}/peaks`

Convenience redirect to `peaksUrl`. The payload:

```json
{ "version": 1, "bucketsPerSecond": 100, "channels": 1, "peaks": [0.02, 0.31, 0.28, …] }
```

Values are 0–1 amplitudes, one per bucket. A 10-minute file is ~60 000 numbers, about 400 KB — fetch once, cache in the client.

### `GET /media?kind=video&limit=50`

Paginated list of the caller's assets.

### `DELETE /media/{assetId}`

`204`, or `409 ASSET_IN_USE` with the projects still referencing it:

```json
{ "error": { "code": "ASSET_IN_USE", "message": "This file is used in 2 projects.",
             "details": { "projectIds": ["prj_1a2b…", "prj_3c4d…"] } } }
```

---

## 4. The timeline document

**This is the centre of the contract.** The frontend produces it, the backend validates and stores it, the export renderer consumes it. Every field here is a promise in three directions.

### 4.1 Shape

```json
{
  "schemaVersion": 1,
  "tracks": [
    {
      "id": "trk_video",
      "kind": "video",
      "index": 0,
      "muted": false,
      "locked": false,
      "clips": [
        {
          "id": "clp_7f3a",
          "assetId": "ast_4c8e…",
          "startMs": 0,
          "durationMs": 8400,
          "sourceInMs": 1200,
          "speed": 1.0,
          "volume": 1.0,
          "audioFadeInMs": 0,
          "audioFadeOutMs": 250,
          "transform": {
            "scale": 1.0,
            "offsetX": 0.0,
            "offsetY": 0.0,
            "rotation": 0,
            "flipH": false,
            "flipV": false,
            "crop": { "x": 0.16, "y": 0.0, "width": 0.68, "height": 1.0 }
          },
          "effects": [
            { "type": "color_grade", "lut": "cinematic_warm", "strength": 0.75, "sourceJobId": "job_a91f…" }
          ],
          "transitionIn":  { "type": "fade",     "durationMs": 300 },
          "transitionOut": { "type": "dissolve", "durationMs": 500 }
        }
      ]
    },
    {
      "id": "trk_music",
      "kind": "audio",
      "index": 0,
      "muted": false,
      "locked": false,
      "clips": [
        { "id": "clp_mus1", "assetId": "ast_b7d2…", "startMs": 0, "durationMs": 42000,
          "sourceInMs": 0, "speed": 1.0, "volume": 0.25,
          "audioFadeInMs": 1000, "audioFadeOutMs": 2000 }
      ]
    },
    {
      "id": "trk_text",
      "kind": "text",
      "index": 0,
      "clips": [
        {
          "id": "clp_cap_001",
          "kind": "caption",
          "startMs": 340,
          "durationMs": 280,
          "text": "Hello",
          "styleId": "kinetic_bold",
          "style": { "fontSize": 0.062, "color": "#FFFFFF", "strokeColor": "#000000", "strokeWidth": 0.004 },
          "position": { "x": 0.5, "y": 0.78, "anchor": "center" },
          "emphasis": 0.42,
          "sourceJobId": "job_5d0c…"
        }
      ]
    }
  ]
}
```

### 4.2 Field reference

**Track**

| Field | Type | Notes |
|---|---|---|
| `id` | string | Client-generated, stable for the life of the track |
| `kind` | `video` \| `audio` \| `text` | Phase 1 allows **one track of each kind** |
| `index` | int | Layer order within the kind. Higher draws on top |
| `muted`, `locked` | bool | Editor state, but `muted` is honoured by the renderer |
| `clips` | array | Ordered by `startMs` |

**Media clip** (on `video` and `audio` tracks)

| Field | Type | Notes |
|---|---|---|
| `assetId` | string | Must be `ready` and owned by the caller |
| `startMs` | int | Where the clip begins **on the timeline** |
| `durationMs` | int | How long it occupies **on the timeline**. > 0 |
| `sourceInMs` | int | Where playback starts **inside the asset** |
| `speed` | float | 0.25–4.0. Source consumed is `durationMs × speed` |
| `volume` | float | 0.0–2.0 |
| `transform.crop` | object \| null | Normalised 0–1 rectangle of the source frame to keep |
| `effects[]` | array | See §4.4 |
| `transitionIn/Out` | object \| null | `cut`, `fade`, `dissolve`. Overlaps the neighbouring clip |

There is deliberately **no `sourceOutMs`** — it is `sourceInMs + durationMs × speed`. Storing a derivable value invites the two to disagree, and the renderer would have no way to know which is right.

**Text clip** (on `text` tracks)

| Field | Type | Notes |
|---|---|---|
| `kind` | `caption` \| `title` | `caption` came from the Captions tool; `title` was typed |
| `text` | string | The words. Editing this is the whole point of the editor model |
| `styleId` | string | Named style from the catalogue |
| `style` | object | Overrides on top of `styleId` |
| `position` | object | `x`, `y` normalised 0–1; `anchor` is `center`, `left` or `right` |
| `emphasis` | float | 0–1, from vocal emphasis. Drives the animation's intensity |
| `sourceJobId` | string \| null | Provenance — lets the UI say "from Captions" and offer to re-run |

### 4.3 Invariants

The API **rejects** a timeline that breaks any of these with `422 INVALID_TIMELINE`, naming the offending clip.

1. Clips within a track never overlap: for consecutive clips, `startMs + durationMs <= next.startMs`.
2. Clips within a track are ordered by ascending `startMs`.
3. `durationMs > 0` for every clip.
4. `sourceInMs + durationMs × speed <= asset.durationMs` — a clip cannot read past the end of its media.
5. Every `assetId` exists, is `ready`, and belongs to the caller.
6. Every `id` is unique across the whole document.
7. A transition's `durationMs` does not exceed half the shorter of the two clips it joins.
8. Phase 1: at most one track per `kind`.

> **All spatial values are normalised 0–1 relative to the canvas, never pixels.** This is what lets a 480p preview and a 1080p export agree. A caption at `y: 0.78` sits in the same place in both. Pixel coordinates would put every overlay in the wrong position at export, and it would not be noticed until someone watched the output.

### 4.4 Effects

```json
{ "type": "color_grade", "lut": "cinematic_warm", "strength": 0.75, "sourceJobId": "job_a91f…" }
```

Phase 1 defines one effect type. `lut` is a name from the catalogue (`GET /catalog/luts`), `strength` is 0–1. The browser applies it live for preview; the renderer applies the same LUT at the same strength at export. **Both sides must produce the same picture** — the LUT files are shared assets, not two implementations.

---

## 5. Projects

### `POST /projects`

```json
{ "title": "Ep. 42 highlights", "aspectRatio": "9:16" }
```

`201` returns the full project with an empty timeline at `version: 0`. Canvas dimensions are derived from `aspectRatio` (`9:16` → 1080×1920, `16:9` → 1920×1080, `1:1` → 1080×1080).

### `GET /projects/{id}`

```json
{
  "id": "prj_1a2b…",
  "title": "Ep. 42 highlights",
  "aspectRatio": "9:16",
  "width": 1080,
  "height": 1920,
  "fps": 30,
  "durationMs": 42300,
  "version": 12,
  "timeline": { "schemaVersion": 1, "tracks": [ … ] },
  "assets": [ { "id": "ast_4c8e…", "proxyUrl": "…", "peaksUrl": "…", "durationMs": 623480 } ],
  "createdAt": "2026-08-12T10:02:11Z",
  "updatedAt": "2026-08-12T14:38:55Z"
}
```

`assets` is a convenience: every asset the timeline references, with fresh signed URLs, so opening a project is **one request** rather than one per clip.

### `PATCH /projects/{id}`

Autosave. The client sends the whole timeline and the version it was working from.

```json
{ "timeline": { "schemaVersion": 1, "tracks": [ … ] }, "version": 12 }
```

`200`:

```json
{ "version": 13, "durationMs": 44100, "updatedAt": "2026-08-12T14:39:12Z" }
```

**`409 VERSION_CONFLICT`** when `version` is not current — another tab or device saved first:

```json
{ "error": { "code": "VERSION_CONFLICT",
             "message": "This project was changed somewhere else.",
             "details": { "currentVersion": 14 } } }
```

The client then re-fetches and decides what to do. Handling in [`04-frontend-architecture.md`](04-frontend-architecture.md) §6.

Title and canvas are patched separately: `{ "title": "New name" }` does not touch the timeline or bump `version`.

### `GET /projects?limit=20`

List, newest first. Returns summaries — `id`, `title`, `durationMs`, `thumbnailUrl`, `updatedAt` — **without** timelines.

### `POST /projects/{id}/duplicate` · `DELETE /projects/{id}`

Copy (timeline and asset references, not the media) and soft delete.

---

## 6. Jobs

One endpoint creates every kind of server work. `tool` decides the rest.

### `POST /jobs`

```http
POST /v1/jobs
Idempotency-Key: 3f9c2b18-…
```

```json
{
  "tool": "captions",
  "projectId": "prj_1a2b…",
  "input": { "assetId": "ast_4c8e…", "clipId": "clp_7f3a", "language": "auto" }
}
```

`202`:

```json
{
  "id": "job_5d0c…",
  "tool": "captions",
  "family": "analysis",
  "status": "queued",
  "progress": 0,
  "creditsReserved": 22,
  "estimatedSeconds": 45,
  "createdAt": "2026-08-12T14:40:03Z"
}
```

### `GET /jobs/{id}`

Same shape, plus `result` or `outputAssetId` when finished, and `error` when failed.

### `GET /jobs?projectId=prj_1a2b…&status=running`

List. On reconnect the client calls this to catch up on anything it missed while disconnected.

### `POST /jobs/{id}/cancel`

`200` with the updated job, or `409 JOB_NOT_CANCELLABLE` if it already finished. Cancelling refunds the reservation in full.

### 6.1 Cost preview

```http
POST /jobs/estimate
```

Same body as `POST /jobs`, but nothing is created:

```json
{ "credits": 22, "estimatedSeconds": 45, "sufficientBalance": true }
```

Call this to show a price before the user commits. The value is exact, not indicative — both use the same function.

### 6.2 Per-tool payloads

#### `captions`

```json
"input": { "assetId": "ast_4c8e…", "clipId": "clp_7f3a", "language": "auto",
           "rangeMs": { "startMs": 0, "endMs": 623480 } }
```

Result:

```json
{
  "language": "en",
  "durationMs": 623480,
  "wordCount": 1842,
  "words": [
    { "w": "Hello",    "s": 340,  "e": 620,  "c": 0.98, "em": 0.42 },
    { "w": "everyone", "s": 620,  "e": 1180, "c": 0.96, "em": 0.71 }
  ]
}
```

Keys are short because there are thousands of them: `w` word, `s` start ms, `e` end ms, `c` confidence 0–1, `em` emphasis 0–1. A 60-minute transcript is delivered by `resultUrl` instead of inline — see §6.3.

The client turns each word into a text clip on the caption track. `c` below 0.7 is worth flagging in the UI so the user checks it.

#### `smart_trim`

```json
"input": { "assetId": "ast_4c8e…", "clipId": "clp_7f3a", "strength": "medium" }
```

`strength` is `light`, `medium` or `aggressive`.

Result:

```json
{
  "analyzedDurationMs": 623480,
  "keptDurationMs": 501220,
  "removals": [
    { "startMs": 12400, "endMs": 14900, "reason": "silence",  "confidence": 0.99 },
    { "startMs": 31220, "endMs": 31780, "reason": "filler",   "confidence": 0.91 },
    { "startMs": 88010, "endMs": 92400, "reason": "repeat",   "confidence": 0.83 }
  ]
}
```

`reason` is `silence`, `filler`, `stutter` or `repeat`. Ranges are in **asset time**, not timeline time — the client maps them onto the clip. They never overlap and are ordered.

#### `color_analysis`

```json
"input": { "assetId": "ast_4c8e…", "clipId": "clp_7f3a", "preferredLook": null }
```

Result:

```json
{
  "lut": "cinematic_warm",
  "strength": 0.75,
  "scene": { "exposure": "low", "whiteBalance": "cool", "contrast": "flat" },
  "alternatives": [ { "lut": "vlog_clean", "strength": 0.6 }, { "lut": "cyberpunk", "strength": 0.9 } ]
}
```

The client writes this into the clip's `effects` and shows it immediately. No second round-trip — the LUT is applied in the browser.

#### `export`

```json
"input": {
  "timelineVersion": 13,
  "preset": { "resolution": "1080p", "aspectRatio": "9:16", "quality": "high", "format": "mp4" }
}
```

`timelineVersion` must match the project's current version — exporting a stale timeline is always a mistake, so it is rejected with `409 VERSION_CONFLICT` rather than silently rendering the wrong thing.

Result: `outputAssetId`, plus

```json
{ "downloadUrl": "https://cdn.zipzop.app/exports/…?Expires=…",
  "sizeBytes": 48213904, "durationMs": 44100, "expiresAt": "2026-09-11T14:52:00Z" }
```

### 6.3 Large results

When a result exceeds 256 KB, `result` is `null` and `resultUrl` carries a signed link to the same JSON in S3:

```json
{ "id": "job_5d0c…", "status": "succeeded", "result": null,
  "resultUrl": "https://cdn.zipzop.app/results/…/result.json?Expires=…" }
```

**Clients must handle both.** Anything over about twenty minutes of speech will take this path.

---

## 7. WebSocket

```
wss://api.zipzop.app/v1/ws?token=<access_token>
```

The token goes in the query string because browsers cannot set headers on a WebSocket handshake. It is short-lived and the connection is upgraded immediately.

Server → client, one JSON object per message:

```json
{ "type": "job.progress",  "jobId": "job_5d0c…", "progress": 62 }

{ "type": "job.succeeded", "jobId": "job_5d0c…", "tool": "captions",
  "projectId": "prj_1a2b…", "resultInline": false }

{ "type": "job.failed",    "jobId": "job_5d0c…", "tool": "captions",
  "errorCode": "NO_SPEECH_DETECTED",
  "message": "We could not find any speech in this clip." }

{ "type": "asset.ready",   "assetId": "ast_4c8e…", "durationMs": 623480 }

{ "type": "credits.updated", "balance": 318 }
```

`job.succeeded` carries no result payload — the client fetches `GET /jobs/{id}`. Keeping results off the socket means one delivery path for results whether the socket was connected or not.

Client → server: `{"type":"ping"}` every 30 seconds. The server replies `{"type":"pong"}`. A missed pong means reconnect.

**The socket is an optimisation, never the source of truth.** After any reconnect the client calls `GET /jobs?projectId=…&status=running` to resynchronise. Everything works with the socket permanently closed, just less pleasantly.

---

## 8. Error codes

| Code | HTTP | When | `details` |
|---|---|---|---|
| `INVALID_CREDENTIALS` | 401 | Wrong email or password | — |
| `TOKEN_EXPIRED` | 401 | Access token past `exp` | — |
| `TOKEN_REVOKED` | 401 | Refresh token reused after rotation — sign in again | — |
| `FORBIDDEN` | 403 | Resource belongs to another user | — |
| `NOT_FOUND` | 404 | No such resource, or not the caller's | — |
| `VERSION_CONFLICT` | 409 | Timeline or export version is stale | `currentVersion` |
| `ASSET_IN_USE` | 409 | Deleting an asset a project still uses | `projectIds` |
| `JOB_NOT_CANCELLABLE` | 409 | Job already finished | `status` |
| `INVALID_TIMELINE` | 422 | An invariant in §4.3 is broken | `violations[]` with `clipId` and `rule` |
| `UNSUPPORTED_MEDIA` | 422 | Codec or container we cannot read | `detectedFormat` |
| `MEDIA_TOO_LONG` | 422 | Over the duration limit | `durationMs`, `maxDurationMs` |
| `FILE_TOO_LARGE` | 422 | Over the size limit | `sizeBytes`, `maxSizeBytes` |
| `INSUFFICIENT_CREDITS` | 402 | Balance below job cost | `required`, `available` |
| `STORAGE_QUOTA_EXCEEDED` | 402 | Account storage full | `usedBytes`, `limitBytes` |
| `CONCURRENCY_LIMIT` | 429 | Too many jobs of this family running | `family`, `limit` |
| `RATE_LIMITED` | 429 | Too many requests | `retryAfterSeconds` |
| `NO_SPEECH_DETECTED` | — | Job failure: nothing to transcribe or trim | — |
| `MEDIA_UNREADABLE` | — | Job failure: file corrupt or truncated | — |
| `RENDER_FAILED` | — | Job failure: export pipeline error | `stage` |
| `INTERNAL_ERROR` | 500 | Ours. Always logged with a `requestId` | `requestId` |

The last four appear as a job's `error`, not as an HTTP response — the request that created the job already succeeded.

---

## 9. Endpoint summary

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/auth/register` | Create an account |
| `POST` | `/auth/login` | Sign in |
| `POST` | `/auth/refresh` | Rotate tokens |
| `POST` | `/auth/logout` | Revoke a refresh token |
| `GET` | `/me` | Current user and balance |
| `POST` | `/media/uploads` | Get a presigned upload URL |
| `POST` | `/media/{id}/complete` | Finish an upload, start ingest |
| `GET` | `/media/{id}` | Asset with signed playback URLs |
| `GET` | `/media` | List assets |
| `DELETE` | `/media/{id}` | Delete an asset |
| `POST` | `/projects` | Create a project |
| `GET` | `/projects` | List projects |
| `GET` | `/projects/{id}` | Project with timeline and assets |
| `PATCH` | `/projects/{id}` | Autosave the timeline |
| `POST` | `/projects/{id}/duplicate` | Copy a project |
| `DELETE` | `/projects/{id}` | Delete a project |
| `POST` | `/jobs` | Run any AI tool, or export |
| `POST` | `/jobs/estimate` | Price a job without creating it |
| `GET` | `/jobs/{id}` | Job status and result |
| `GET` | `/jobs` | List jobs |
| `POST` | `/jobs/{id}/cancel` | Cancel a job |
| `GET` | `/credits/ledger` | Credit history |
| `GET` | `/catalog/luts` | Available colour looks |
| `GET` | `/catalog/caption-styles` | Available caption styles |
| `WS` | `/ws` | Live job and credit events |

---

## 10. Building against this before the backend exists

The frontend does not wait. Two things make that work:

1. **An OpenAPI schema generated from FastAPI** is committed to the repository from day one, even while every endpoint returns a stub. The frontend generates its client types from it, so a mismatch is a build error rather than a bug found in testing.
2. **A mock server** — Prism or MSW against the same schema — serves realistic fixtures, including a captions result with a few thousand words and at least one deliberately misheard name, a smart-trim result with overlapping-looking ranges, and a job that fails.

Most of phase 1's frontend — timeline, playback, editing, undo — touches no server at all. Build it against mocks, connect it when the endpoints land.

---

*AI Video Editor · API Contract v1.0 · 12 August 2026*

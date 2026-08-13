# Backend Architecture

**How the server side is built: data model, job pipeline, media handling, rendering and infrastructure.**

| | |
|---|---|
| **Version** | 1.0 |
| **Date** | 12 August 2026 |
| **Audience** | Backend engineers |
| **Depends on** | [`01-product-vision.md`](01-product-vision.md) · [`02-scope-v1.md`](02-scope-v1.md) |
| **Pairs with** | [`05-api-contract.md`](05-api-contract.md) — the wire format · [`04-frontend-architecture.md`](04-frontend-architecture.md) — the client |
| **Stack** | Python 3.12 · FastAPI · PostgreSQL 16 · Redis 7 · Celery · S3 + CloudFront · FFmpeg |

**Diagrams:** [system overview](diagrams/system-overview.md) · [data model](diagrams/data-model.md) · [job lifecycle](diagrams/job-lifecycle.md)

---

## 1. What changed from the original architecture draft

The source *System Architecture & Backend Design* document described a pipeline that takes a video in and gives a different video back. That is the right design for a face-swap service. It is not the right design for an editor, and the difference is not cosmetic.

| | Original draft | This design |
|---|---|---|
| **Central object** | A task with an input URL and an output URL | A **project** holding a timeline the user returns to over days |
| **What the client sends** | A request for a finished video | Edits to a document; jobs are one kind of edit |
| **What a job returns** | Always a rendered video | Usually **decisions** (cut points, transcripts); sometimes media |
| **Compute needed on day one** | GPU cluster | Modest CPU workers — GPU arrives with phase 2 |
| **Credits** | An integer on the user row | A **ledger**, with reservation and settlement |
| **Rendering** | Part of every job | A **separate export pipeline**, run once at the end |

Everything the original draft got right is kept: FastAPI at the edge, Celery over Redis for queuing, Postgres for state, S3 for media, JWT auth, presigned URLs. The queue-based, non-blocking shape was correct. What changes is what flows through it.

---

## 2. Principles

These are the rules the rest of the document follows. When something below looks surprising, one of these is the reason.

1. **The client owns the timeline; the server owns the truth about media, jobs and money.** The editor holds the whole timeline in memory and autosaves it. The server does not compute edits.
2. **Original media is immutable.** Nothing ever overwrites an upload. Every AI result that changes pixels produces a *new* asset that records what it came from. This is what makes "revert" always possible.
3. **Nothing is baked until export.** Cuts, grades, captions and transitions live as data in the timeline document. Pixels change once, at the end.
4. **Jobs return the smallest thing that works.** If a tool can express its result as data instead of a video file, it must. Data is cheaper to produce, faster to deliver, and stays editable.
5. **The API is client-agnostic.** No HTML, no cookie-dependent state, no assumption that the caller is a browser. The web app and a future native app are the same kind of client. This is what makes "reuse the backend for mobile" true rather than aspirational.
6. **Money is double-entry.** Credits move through an append-only ledger. A balance is a cached projection of it, never the source of truth.

---

## 3. System components

```
Browser (editor)
   │
   │  REST over HTTPS  ·  WebSocket for live job updates
   ▼
API Gateway — FastAPI, stateless, N replicas
   │
   ├── Postgres        projects, timelines, jobs, ledger, assets
   ├── Redis           Celery broker · cache · pub/sub for WebSocket fan-out
   └── S3              originals, proxies, derived media, exports
                            ▲
Celery workers ─────────────┘
   ├── ingest queue     probe, proxy, thumbnail, waveform peaks     (fast, CPU)
   ├── analysis queue   captions, smart trim, colour analysis        (seconds, CPU/small GPU)
   ├── render queue     export: timeline document → finished file    (minutes, CPU-heavy)
   └── inference queue  face mapping, lip sync            (phase 2, GPU, scales to zero)
```

**The API gateway never does heavy work.** It validates, writes to Postgres, enqueues, and returns. Nothing that touches media happens in a request handler.

**Workers never talk to the client.** They write results to Postgres and S3, then publish an event to Redis. WebSocket servers relay it. This is what lets any API replica notify any connected user.

---

## 4. Data model

Full ER diagram: [`diagrams/data-model.md`](diagrams/data-model.md).

### 4.1 The one decision that shapes everything: how a timeline is stored

A timeline is tracks containing clips, each clip pointing at a piece of media with in/out points, transforms and effects. There are two obvious ways to store it, and the choice affects every part of the system.

| | Normalised — `tracks` and `clips` tables | Document — one JSONB blob per project |
|---|---|---|
| Dragging a clip | `UPDATE clips SET start_ms = …` | Client mutates memory, autosaves the document |
| Loading a project | Several joins, assembled into a tree | One row, one read |
| Editing 40 clips at once | 40 statements, or a bulk upsert with ordering care | One write |
| Undo/redo | Rebuild prior state from a log the server must also understand | Client-side, free — the server never needs to know |
| "Which projects use this asset?" | Trivial query | Needs a JSON index or a side table |
| Schema evolution | Migration per change | Version field, handled in code |

**Decision: the timeline is a JSONB document on the project, plus a side table for asset references.**

The reasoning: an editing session produces hundreds of small mutations per minute — every drag, trim and nudge. Sending each as a database write is both slow and pointless, because the client already holds authoritative state and can replay it. Normalised rows would buy queryability we do not need — nothing in the product asks "find all clips longer than 5 seconds across all projects". What we *do* need is to know which assets a project references, for storage accounting, deletion and cleanup, and that is one small table maintained on save.

This also makes undo/redo entirely a client concern, which is where it belongs, and makes the frontend's autosave a single idempotent `PATCH`.

**Consequence to accept:** the timeline document is schema-less to Postgres, so it must be validated in the application layer on every write. [`05-api-contract.md`](05-api-contract.md) §4 defines the schema, and the API rejects documents that do not match it. This is not optional — without it, a client bug can persist a timeline the renderer cannot read.

### 4.2 Schema

```sql
CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "citext";     -- case-insensitive email
-- CREATE EXTENSION vector;                  -- phase 2, face embeddings
```

#### users

```sql
CREATE TYPE user_status AS ENUM ('active', 'suspended', 'deleted');

CREATE TABLE users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email               CITEXT      NOT NULL UNIQUE,
    hashed_password     TEXT        NOT NULL,           -- bcrypt, cost 12
    display_name        TEXT,
    status              user_status NOT NULL DEFAULT 'active',

    -- Cached projection of credit_ledger. Never write without a matching
    -- ledger row in the same transaction. Reconciled nightly.
    credit_balance      INTEGER     NOT NULL DEFAULT 0 CHECK (credit_balance >= 0),

    storage_bytes_used  BIGINT      NOT NULL DEFAULT 0,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ
);
```

#### refresh_tokens

```sql
CREATE TABLE refresh_tokens (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash   TEXT NOT NULL UNIQUE,        -- SHA-256, never the token itself
    expires_at   TIMESTAMPTZ NOT NULL,
    revoked_at   TIMESTAMPTZ,
    replaced_by  UUID REFERENCES refresh_tokens(id),   -- rotation chain
    user_agent   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON refresh_tokens (user_id) WHERE revoked_at IS NULL;
```

Refresh tokens rotate: each use issues a new one and marks the old replaced. If a token that was already replaced is presented, the whole chain is revoked — that is a stolen-token signal.

#### media_assets

Every file the system holds. **Immutable once ready.**

```sql
CREATE TYPE asset_kind   AS ENUM ('video', 'audio', 'image');
CREATE TYPE asset_status AS ENUM ('pending_upload', 'probing', 'ready', 'failed', 'deleted');

CREATE TABLE media_assets (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind                asset_kind   NOT NULL,
    status              asset_status NOT NULL DEFAULT 'pending_upload',

    -- Storage
    storage_key         TEXT NOT NULL,          -- S3 key of the original
    proxy_key           TEXT,                   -- 480p H.264, for browser preview
    thumbnail_key       TEXT,
    peaks_key           TEXT,                   -- waveform peaks JSON
    size_bytes          BIGINT,
    checksum_sha256     TEXT,

    -- Probe results (ffprobe, on ingest)
    original_filename   TEXT,
    mime_type           TEXT,
    duration_ms         INTEGER,
    width               INTEGER,
    height              INTEGER,
    fps                 NUMERIC(7,3),
    video_codec         TEXT,
    audio_codec         TEXT,
    audio_channels      SMALLINT,
    sample_rate         INTEGER,

    -- Provenance: set when this asset was produced by a job from another asset.
    -- This is how "revert to original" works — follow the chain back.
    derived_from_asset_id UUID REFERENCES media_assets(id) ON DELETE SET NULL,
    derived_by_job_id     UUID,                 -- FK added after jobs table

    failure_reason      TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ
);

CREATE INDEX ON media_assets (user_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX ON media_assets (derived_from_asset_id) WHERE derived_from_asset_id IS NOT NULL;
CREATE INDEX ON media_assets (user_id, checksum_sha256) WHERE status = 'ready';
```

The checksum index lets a re-upload of the same file reuse the existing asset instead of storing it twice.

#### projects

```sql
CREATE TABLE projects (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title           TEXT NOT NULL DEFAULT 'Untitled project',

    -- Canvas
    aspect_ratio    TEXT    NOT NULL DEFAULT '9:16',   -- '9:16' | '16:9' | '1:1'
    width           INTEGER NOT NULL DEFAULT 1080,
    height          INTEGER NOT NULL DEFAULT 1920,
    fps             SMALLINT NOT NULL DEFAULT 30,

    -- The timeline. Schema in 05-api-contract.md §4. Validated on every write.
    timeline        JSONB   NOT NULL DEFAULT '{"tracks": []}'::jsonb,

    -- Optimistic concurrency. Incremented on every accepted timeline write;
    -- a PATCH carrying a stale version is rejected with 409.
    version         INTEGER NOT NULL DEFAULT 0,

    duration_ms     INTEGER NOT NULL DEFAULT 0,        -- derived on save, for listings
    thumbnail_key   TEXT,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX ON projects (user_id, updated_at DESC) WHERE deleted_at IS NULL;
```

#### project_assets

The side table that makes the document choice safe. Rebuilt by the API on every timeline write by walking the document for asset ids.

```sql
CREATE TABLE project_assets (
    project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    asset_id    UUID NOT NULL REFERENCES media_assets(id) ON DELETE RESTRICT,
    PRIMARY KEY (project_id, asset_id)
);
CREATE INDEX ON project_assets (asset_id);
```

`ON DELETE RESTRICT` is deliberate: an asset still used by a project cannot be deleted out from under it. The API turns that into a clear error rather than a broken timeline.

#### jobs

Every unit of server work — analysis, inference, **and export** — is a job. One table, one lifecycle, one progress mechanism, one notification path.

```sql
CREATE TYPE job_family AS ENUM ('analysis', 'render', 'inference');
CREATE TYPE job_status AS ENUM ('queued', 'running', 'succeeded', 'failed', 'cancelled');
CREATE TYPE job_tool   AS ENUM (
    -- phase 1
    'captions', 'smart_trim', 'color_analysis', 'export',
    -- phase 2
    'face_map', 'lip_sync', 'denoise', 'dereverb',
    -- phase 3
    'clip_finder', 'template_suggest', 'upscale', 'stabilize'
);

CREATE TABLE jobs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id        UUID REFERENCES projects(id) ON DELETE CASCADE,

    tool              job_tool   NOT NULL,
    family            job_family NOT NULL,
    status            job_status NOT NULL DEFAULT 'queued',
    progress          SMALLINT   NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),

    -- What to do. Asset ids, clip ranges, tool parameters.
    input             JSONB NOT NULL,

    -- Analysis results live here when small. Larger payloads (a 60-minute
    -- transcript is megabytes) go to S3 and this holds {"ref": "s3://…"}.
    result            JSONB,
    result_key        TEXT,

    -- Render and inference jobs produce media instead of data.
    output_asset_id   UUID REFERENCES media_assets(id) ON DELETE SET NULL,

    -- Money. Reserved at creation, released on failure. See §5.4.
    credits_reserved  INTEGER NOT NULL DEFAULT 0,
    credits_settled   INTEGER,

    -- Reliability
    idempotency_key   TEXT,
    attempts          SMALLINT NOT NULL DEFAULT 0,
    error_code        TEXT,
    error_message     TEXT,

    -- Reproducibility: which model produced this result.
    model_version     TEXT,
    worker_id         TEXT,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ
);

CREATE UNIQUE INDEX ON jobs (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX ON jobs (user_id, created_at DESC);
CREATE INDEX ON jobs (project_id) WHERE project_id IS NOT NULL;
CREATE INDEX ON jobs (status) WHERE status IN ('queued', 'running');

ALTER TABLE media_assets
    ADD CONSTRAINT fk_derived_by_job
    FOREIGN KEY (derived_by_job_id) REFERENCES jobs(id) ON DELETE SET NULL;
```

#### credit_ledger

Append-only. Never updated, never deleted.

```sql
CREATE TYPE ledger_reason AS ENUM (
    'signup_grant',   -- free credits on registration
    'purchase',       -- bought
    'reserve',        -- held when a job starts        (negative)
    'refund',         -- released when a job fails     (positive)
    'admin_grant',
    'admin_adjust'
);

CREATE TABLE credit_ledger (
    id             BIGSERIAL PRIMARY KEY,
    user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    delta          INTEGER NOT NULL,            -- signed; never zero
    reason         ledger_reason NOT NULL,
    job_id         UUID REFERENCES jobs(id) ON DELETE SET NULL,
    balance_after  INTEGER NOT NULL,            -- running balance, for audit
    note           TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON credit_ledger (user_id, created_at DESC);
CREATE UNIQUE INDEX ON credit_ledger (job_id, reason)
    WHERE job_id IS NOT NULL;   -- one reserve and at most one refund per job
```

That last index is the guard that makes double-refund impossible even if a worker retries its completion handler.

#### Phase 2 tables

Not built in phase 1 — no facial data exists until then — but the shape is settled now so the phase 1 model does not have to be reworked.

```sql
CREATE TYPE face_subject AS ENUM ('self', 'third_party_asserted');

CREATE TABLE consent_records (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject             face_subject NOT NULL,
    consent_text_version TEXT NOT NULL,   -- exactly which wording was accepted
    accepted_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip_address          INET,
    user_agent          TEXT
);

CREATE TABLE face_profiles (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    consent_record_id   UUID NOT NULL REFERENCES consent_records(id),
    label               TEXT,
    status              TEXT NOT NULL DEFAULT 'building',
    embedding           vector(512),
    mesh_key            TEXT,
    reference_keys      JSONB,             -- the three source images
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ
);
```

`consent_record_id` is `NOT NULL`: a face profile cannot exist without the record of what was agreed. That is the one piece of the compliance work worth building into the schema now, because retrofitting consent onto existing rows is the part that goes wrong.

---

## 5. Jobs

### 5.1 The two families

| | **Analysis** | **Render / Inference** |
|---|---|---|
| Returns | Data — `jobs.result` | Media — `jobs.output_asset_id` |
| Tools | captions, smart_trim, color_analysis, clip_finder, template_suggest | export, face_map, lip_sync, denoise, upscale, stabilize |
| Typical duration | 5–60 s | 1–15 min |
| Hardware | CPU, or one small GPU for transcription | CPU (export) · GPU (inference) |
| Effect on the timeline | Client applies the result as an undoable edit | A new asset replaces a clip's media |
| Cost to us | Cents | Dollars |
| Phase | 1 | export in 1, inference in 2 |

They share this table and lifecycle deliberately: one status model, one progress bar, one WebSocket event shape, one credit path. Phase 2 adds a worker, not a subsystem.

### 5.2 Lifecycle

```
                  ┌───────────► cancelled
                  │              (user cancels before a worker claims it)
   POST /v1/jobs  │
        │         │
        ▼         │
     queued ──────┴──► running ──┬──► succeeded
        ▲                        │
        └──── retry (transient) ──┴──► failed
```

Full sequence with all participants: [`diagrams/job-lifecycle.md`](diagrams/job-lifecycle.md).

**Creation**, in one transaction:

1. Validate the payload and resolve the input assets (must belong to the caller, must be `ready`).
2. Compute the cost from the tool and the media duration (§5.5).
3. `SELECT … FOR UPDATE` on the user row; reject with `INSUFFICIENT_CREDITS` if the balance is short.
4. Insert the `reserve` ledger row and update the cached balance.
5. Insert the job as `queued`.
6. Enqueue the Celery task on the queue matching the family.
7. Return `202` with the job id.

Steps 3–5 are the only place credits move on the way out, and they are inside the same transaction as the job insert. A job can never exist without its reservation, and a reservation can never exist without its job.

**Execution**, in the worker:

1. Claim the job — `UPDATE … SET status='running', started_at=now(), attempts=attempts+1 WHERE id=$1 AND status='queued'`. If zero rows update, another worker already has it; stop.
2. Download inputs from S3 to local scratch.
3. Work, publishing progress to Redis at meaningful checkpoints — not on a timer.
4. Write the result: `result`/`result_key` for analysis, or a new `media_assets` row plus `output_asset_id` for render and inference.
5. Mark `succeeded`, set `credits_settled = credits_reserved`, publish completion.
6. Clean scratch.

**Failure.** Transient failures — network, a worker killed, an S3 timeout — retry up to 3 times with exponential backoff, staying `queued`. Permanent failures — unreadable media, no speech found, unsupported codec — go straight to `failed` with an `error_code` the client can translate into a sentence. A job that reaches `failed` refunds its reservation (§5.4).

**Cancellation.** Allowed while `queued`, and while `running` for render and inference jobs where the saving is real. The worker checks a Redis cancellation flag between stages and aborts cleanly. Cancelled jobs refund in full.

### 5.3 Concurrency and fairness

Per-user limits, enforced at creation:

| Family | Concurrent jobs per user |
|---|---|
| analysis | 3 |
| render (export) | 2 |
| inference | 1 |

Beyond the limit, jobs stay `queued` and start as slots free up — the request still succeeds, so the client never has to handle "try again later". These limits exist to stop one user monopolising a worker pool, and they matter most for the GPU pool in phase 2, where a single user could otherwise occupy every card.

### 5.4 Credits

Reserve on start, settle on success, refund on failure.

| Event | Ledger row | Cached balance |
|---|---|---|
| Job created, cost 40 | `reserve`, delta −40 | −40 |
| Job succeeds | none — the reservation *is* the charge | unchanged |
| Job fails or is cancelled | `refund`, delta +40 | +40 |

Two properties matter here. First, a user cannot start work they cannot pay for, because the money moves before the job is queued. Second, a failure on our side never costs the user anything, and it happens automatically rather than through support.

`users.credit_balance` is a cache. Every write to it happens in the same transaction as its ledger row. A nightly job re-sums the ledger per user and alerts on any drift — if that alarm ever fires, there is a bug in a transaction boundary.

### 5.5 What things cost

Cost is a function of the tool and the media duration, computed at creation time from the probed `duration_ms`, so the client can show the price before the user commits.

```python
COST_PER_MINUTE = {
    "captions":       2,
    "smart_trim":     1,
    "color_analysis": 1,
    "export":         2,   # open decision G — may be free
    # phase 2
    "denoise":        3,
    "face_map":      25,
    "lip_sync":      20,
}

def cost(tool: str, duration_ms: int) -> int:
    minutes = math.ceil(duration_ms / 60_000)
    return COST_PER_MINUTE[tool] * max(1, minutes)
```

🟠 **These numbers are placeholders.** The ratios are meaningful — face mapping is roughly an order of magnitude above captioning, which reflects real hardware cost — but the absolute values wait on open decision B, the commercial model. Keep them in one module so pricing changes without touching job logic.

---

## 6. Media

### 6.1 Upload

Files never pass through the API. Bandwidth through a request handler is wasted money and a needless failure mode.

1. `POST /v1/media/uploads` with filename, size and content type. The server checks quota and limits, creates a `media_assets` row as `pending_upload`, and returns a **presigned S3 PUT URL** valid for 15 minutes.
2. The browser uploads straight to S3, with progress from the XHR itself. Files over 100 MB use multipart.
3. `POST /v1/media/{id}/complete`. The server verifies the object exists and its size matches, sets `probing`, and enqueues an ingest job.

### 6.2 Ingest

Runs on the `ingest` queue, typically under 30 seconds for a 10-minute video, and produces four things:

| Output | What | Why |
|---|---|---|
| **Probe** | duration, dimensions, fps, codecs, channels — via `ffprobe` | Needed to price jobs, lay out the timeline, and reject bad input |
| **Proxy** | 480p, H.264, faststart MP4 | The browser cannot scrub a 4K file. Preview plays proxies; export uses originals. |
| **Thumbnail** | JPEG from ~10% in | Media bin and project listings |
| **Peaks** | Min/max amplitude pairs at ~100 buckets/second, JSON | The timeline waveform. Computing this in the browser means downloading and decoding the whole audio track. |

The asset becomes `ready` only when all four exist. Anything unreadable becomes `failed` with a reason the interface can show.

**Proxies are not an optimisation, they are the reason browser preview is possible at all.** A 4K H.265 file will not scrub in a browser; a 480p H.264 proxy will. This is what makes the vision document's "nothing round-trips to see an edit" true.

### 6.3 Storage layout

One bucket, prefixed by purpose so lifecycle rules can differ.

```
s3://zipzop-media/
  originals/{user_id}/{asset_id}/source.{ext}      never auto-deleted
  proxies/{user_id}/{asset_id}/proxy.mp4           regenerable
  thumbs/{user_id}/{asset_id}/thumb.jpg            regenerable
  peaks/{user_id}/{asset_id}/peaks.json            regenerable
  derived/{user_id}/{asset_id}/output.mp4          job output media
  exports/{user_id}/{job_id}/final.mp4             expires after 30 days
  results/{user_id}/{job_id}/result.json           large analysis payloads
  scratch/{job_id}/…                               expires after 1 day
```

Everything is private. Delivery is through CloudFront with signed URLs, one hour for playback, so a leaked URL expires on its own.

🟠 **Retention on `originals/` is open** — it is the single largest recurring cost in the system and the answer is commercial, not technical.

### 6.4 Deriving media

When a job rewrites pixels or audio, the output is a **new asset** pointing back at its source:

```
asset A  (original upload)
   └── asset B  derived_from_asset_id = A,  derived_by_job_id = job 42   (denoised)
```

The client swaps the clip's `assetId` from A to B. Reverting swaps it back. A is never touched, which is what makes principle 2 hold.

---

## 7. Export

Export is where everything becomes pixels. It is a job like any other — `tool = 'export'`, `family = 'render'` — so it inherits progress, cancellation, notification and credit handling for free.

**Input:** the project id and the timeline version being exported, plus a preset (resolution, aspect ratio, quality). The renderer reads the timeline document, not the client's request — the client cannot ask for something the saved project does not contain.

**Steps:**

1. Resolve the timeline: every clip, its source asset, in/out points, transforms, effects, transitions, text.
2. Fetch **original** media — never the proxies.
3. Build one FFmpeg filter graph for the whole composition: trims, concat, scaling, crop and reframe, colour LUTs at their configured strength, text overlays with their per-word timings, transitions, audio mix with per-clip volume and fades.
4. Encode to H.264/AAC at the requested preset.
5. Upload to `exports/`, create a `media_assets` row, set `output_asset_id`.
6. Publish completion; the client offers a download link.

**Why server-side.** Browser export via WebCodecs is uneven across browsers and unusable on Safari for our purposes; a phone would take longer to export than to record. More importantly, this is the same pipeline that phase 2's inference jobs plug into — building it now means face mapping arrives as a new filter stage rather than a new subsystem. One render path also means a video looks the same wherever it was made, which matters the first time a user reports a colour difference.

**Progress** comes from parsing FFmpeg's `-progress` output against the known total duration, so the bar reflects real work rather than a guess.

---

## 8. Live updates

Jobs finish while the user is doing something else. They must find out without polling.

```
worker ──publish──► Redis channel "user:{user_id}" ──► every WS server subscribed
                                                             │
                                                             ▼
                                                    the user's open sockets
```

- The client opens `wss://…/v1/ws` and authenticates with its access token.
- The server subscribes that connection to `user:{id}` on Redis pub/sub.
- Workers publish; whichever API replicas hold that user's sockets relay the event.
- Redis pub/sub is fire-and-forget, so **the socket is an optimisation, not the source of truth**. On reconnect the client re-fetches any job it was watching. `GET /v1/jobs/{id}` always works and is the fallback when a socket cannot be opened.

Events: `job.progress`, `job.succeeded`, `job.failed`, `credits.updated`. Shapes in [`05-api-contract.md`](05-api-contract.md) §7.

---

## 9. Security

| | |
|---|---|
| **Passwords** | bcrypt, cost 12. Never logged, never returned. |
| **Access tokens** | JWT, 15 minutes, signed RS256. Carry `sub`, `exp`, `jti`. No permissions embedded — they are read from the database, so a change takes effect immediately. |
| **Refresh tokens** | Opaque, 30 days, stored as a SHA-256 hash, rotated on every use. Reuse of a rotated token revokes the whole chain. |
| **Transport** | HTTPS only, HSTS. |
| **Media access** | Every S3 object private. Presigned PUT for upload (15 min), CloudFront signed URLs for playback (1 h). |
| **Ownership** | Every query filters on `user_id` at the repository layer, not the route handler. A route that forgets is a data leak; a repository that cannot express a cross-user query is not. |
| **Rate limits** | 100 requests/minute per user on the API; 20/minute on auth endpoints per IP; job creation additionally bounded by the concurrency caps in §5.3. |
| **Upload validation** | Content type and size checked before presigning; the real format is verified by probing after upload, because a client-declared content type proves nothing. |
| **Deletion** | Soft delete first (`deleted_at`), hard delete on a schedule. Account deletion cascades to projects, assets and — in phase 2 — face profiles and every asset derived from them. |

---

## 10. Infrastructure

| Component | Choice | Notes |
|---|---|---|
| **API** | FastAPI on ECS Fargate, ≥2 replicas behind an ALB | Stateless; WebSocket connections are sticky per connection but need no shared session |
| **Database** | RDS PostgreSQL 16, Multi-AZ | pgvector enabled in phase 2 |
| **Cache / broker** | ElastiCache Redis 7 | Celery broker, cache, pub/sub |
| **Workers** | Celery on ECS, **one service per queue** | Independent scaling; a backlog of exports must not starve captioning |
| **GPU workers** | Phase 2 — g5.xlarge or equivalent, autoscaling to zero | Idle GPUs are the most expensive mistake available to us |
| **Storage** | S3 + CloudFront | Lifecycle rules per prefix (§6.3) |
| **Migrations** | Alembic | Every schema change reviewed; no destructive migration without a backfill plan |
| **Config** | Environment variables, secrets in Secrets Manager | Nothing credential-shaped in the repository |

**Observability**, because a job pipeline that fails silently is worse than no pipeline:

- Structured JSON logs carrying `request_id`, `user_id`, `job_id` on every line
- Metrics: queue depth per queue, job duration by tool, failure rate by `error_code`, credits reserved vs settled, storage per user
- Alerts: queue depth over threshold, failure rate above 5% for any tool, ledger reconciliation drift, any job `running` longer than its family's ceiling
- Tracing across API → queue → worker on the job id

### Local development

`docker compose` brings up Postgres, Redis, MinIO (S3-compatible) and the worker pool. No AWS account is needed to run the whole system locally, including uploads and exports — MinIO speaks the same presigned-URL protocol.

---

## 11. What phase 2 and 3 add

The point of this design is that later phases add workers, not architecture.

| | Adds | Changes |
|---|---|---|
| **Phase 2** — face mapping, lip sync, denoise | GPU worker pool on the `inference` queue · `face_profiles` and `consent_records` · pgvector · consent flow at upload · watermarking in the export renderer | Nothing structural. New `job_tool` values, new worker services, one new filter stage in the renderer. |
| **Phase 3** — clip finder, templates, upscaling | Speaker tracking in the analysis worker · template definitions · a licensed music catalogue | Timeline document gains a `templateId`; clip finder creates projects via the existing project API. |
| **Mobile** | Nothing on the server | The API is already client-agnostic (principle 5). A native client is a new consumer of the same contract. |

The one thing to guard: **every new tool must be pushed into the analysis family if it possibly can be.** A tool that returns decisions is cheaper to run, faster for the user, and leaves the result editable. Reach for the GPU only when pixels genuinely have to change.

---

## 12. Decisions recorded here

Short list of the calls this document makes, so they can be challenged individually rather than as a whole.

| # | Decision | Instead of | Because |
|---|---|---|---|
| 1 | Timeline stored as a JSONB document + asset side table | Normalised `tracks`/`clips` tables | Editing is hundreds of small mutations the client already owns; we need asset references, not clip queries (§4.1) |
| 2 | One `jobs` table for analysis, render and inference | Separate pipelines per kind of work | One lifecycle, one progress path, one credit path; phase 2 adds a worker, not a subsystem |
| 3 | Export is a job, not its own table | An `exports` table | Identical needs: queue, progress, cancel, notify, charge |
| 4 | Credit ledger with reserve/refund | An integer balance | Auditable, automatic refunds, no double-spend under concurrency (§5.4) |
| 5 | Presigned direct-to-S3 upload | Upload through the API | Bandwidth cost and a needless failure mode |
| 6 | Proxies generated at ingest | Preview from originals | Browsers cannot scrub 4K; this is what makes local preview work at all (§6.2) |
| 7 | Server-side export | Browser-side export | Consistency across browsers, and it is the pipeline phase 2 plugs into (§7) |
| 8 | Colour grading is an analysis job | A render job per grade | Applying a LUT is free in the browser and baked at export anyway; only the analysis needs a server |
| 9 | Optimistic concurrency on the timeline (`version` + 409) | Last-write-wins | Two tabs on one project must not silently destroy each other's work |
| 10 | Media is immutable; jobs derive new assets | Overwriting in place | "Revert to original" has to be free and always available (§6.4) |

---

*AI Video Editor · Backend Architecture v1.0 · 12 August 2026*

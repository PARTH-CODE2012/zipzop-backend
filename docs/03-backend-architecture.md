# Backend Architecture

**How the server side is built: data model, job pipeline, media handling, rendering and infrastructure.**

| | |
|---|---|
| **Version** | 1.1 — billing and subscriptions added |
| **Date** | 13 August 2026 |
| **Audience** | Backend engineers |
| **Depends on** | [`01-product-vision.md`](01-product-vision.md) · [`02-scope-v1.md`](02-scope-v1.md) |
| **Pairs with** | [`05-api-contract.md`](05-api-contract.md) — the wire format · [`04-frontend-architecture.md`](04-frontend-architecture.md) — the client |
| **Stack** | Python 3.12 · FastAPI · PostgreSQL 16 · Redis 7 · Celery · S3 + CloudFront · FFmpeg |
| **Cloud** | AWS, on a company account (confirmed 13 August 2026) |
| **Payments** | Stripe (global, USD) + Razorpay (India, INR) — both from launch |

> **What changed in 1.1.** The commercial model was settled on 13 August 2026: four subscription tiers, monthly credit allowances that expire, purchased top-up credits that do not, a separate metered allowance for face mapping, and two payment providers live at launch. That adds §8 (billing), three balances instead of one in §5.4, a priority field on jobs, and plan gating in the export renderer. Nothing already built changes shape.

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
6. **Money is double-entry.** Credits move through an append-only ledger. A balance is a cached projection of it, never the source of truth. Because credits now come in kinds that expire differently (§5.4), every ledger row names the bucket it moved — a balance that cannot say *which* credits it holds cannot expire them correctly.
7. **A plan is data, not code.** What a tier includes — credits, resolution ceiling, watermark, queue priority — lives in a table, not in `if` statements. Pricing will change more often than anything else in this system.

---

## 3. System components

```
Browser (editor)                        Stripe · Razorpay
   │                                          │
   │  REST · WebSocket                        │  signed webhooks
   ▼                                          ▼
API Gateway — FastAPI, stateless, N replicas
   │
   ├── Postgres   projects, timelines, jobs, ledger, assets, subscriptions, payments
   ├── Redis      Celery broker · cache · pub/sub for WebSocket fan-out
   └── S3         originals, proxies, derived media, exports
                       ▲
Celery workers ────────┘
   ├── ingest queue     probe, proxy, thumbnail, waveform peaks    (fast, CPU)
   ├── analysis queue   captions, smart trim, colour analysis      (seconds, CPU/small GPU)
   ├── render queue     export: timeline document → finished file  (minutes, CPU-heavy)
   ├── inference queue  face mapping, lip sync           (phase 2, GPU, scales to zero)
   └── billing queue    webhook processing, plan grants            (fast, CPU)

Celery beat  ── hourly renewal sweep · nightly ledger reconciliation · storage lifecycle
```

Each of the first four queues is split into priority bands by plan (§5.3); one worker service per queue drains its bands in order.

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

    -- Cached projections of credit_ledger, one per bucket. Never write any of
    -- these without a matching ledger row in the same transaction. Reconciled
    -- nightly against the ledger. See §5.4 for what each bucket means.
    plan_credits        INTEGER     NOT NULL DEFAULT 0 CHECK (plan_credits >= 0),
    topup_credits       INTEGER     NOT NULL DEFAULT 0 CHECK (topup_credits >= 0),
    facemap_seconds     INTEGER     NOT NULL DEFAULT 0 CHECK (facemap_seconds >= 0),

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

    -- Queue band, copied from the user's plan at creation time. Copied rather
    -- than joined so that a downgrade mid-job does not demote work already
    -- queued, and so the queue router never has to touch the users table.
    priority          SMALLINT   NOT NULL DEFAULT 0,

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

Append-only. Never updated, never deleted. Every row names the **bucket** it moved, because the buckets expire on different schedules and a balance that cannot say which credits it holds cannot expire them correctly.

```sql
CREATE TYPE credit_bucket AS ENUM (
    'plan',      -- monthly subscription allowance — expires at period end
    'topup',     -- purchased à la carte           — never expires
    'facemap'    -- face-mapping seconds           — expires at period end
);

CREATE TYPE ledger_reason AS ENUM (
    'signup_grant',    -- free credits on registration
    'plan_grant',      -- monthly allowance at renewal          (positive)
    'plan_expiry',     -- unused allowance swept at period end   (negative)
    'topup_purchase',  -- bought à la carte                     (positive)
    'reserve',         -- held when a job starts                (negative)
    'refund',          -- released when a job fails             (positive)
    'admin_grant',
    'admin_adjust'
);

CREATE TABLE credit_ledger (
    id             BIGSERIAL PRIMARY KEY,
    user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    bucket         credit_bucket NOT NULL,
    delta          INTEGER NOT NULL,            -- signed; never zero
    reason         ledger_reason NOT NULL,
    job_id         UUID REFERENCES jobs(id) ON DELETE SET NULL,
    payment_id     UUID,                        -- FK added after payments table
    balance_after  INTEGER NOT NULL,            -- running balance of THIS bucket
    note           TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON credit_ledger (user_id, created_at DESC);

-- One reserve and at most one refund per job PER BUCKET. A single job may draw
-- from two buckets (§5.4), so the bucket is part of the key.
CREATE UNIQUE INDEX ON credit_ledger (job_id, reason, bucket)
    WHERE job_id IS NOT NULL;
```

That last index is the guard that makes a double refund impossible even if a worker retries its completion handler.

#### plans

A tier is a row, not a branch in code. Pricing changes more often than anything else here, and a price change should not require a deploy.

```sql
CREATE TYPE plan_code AS ENUM ('free', 'pro', 'business', 'studio');
CREATE TYPE watermark_mode AS ENUM ('forced', 'none', 'custom');

CREATE TABLE plans (
    code                plan_code PRIMARY KEY,
    display_name        TEXT NOT NULL,

    monthly_credits     INTEGER NOT NULL,      -- granted at each renewal, expires
    facemap_seconds     INTEGER NOT NULL,      -- GPU seconds included, expires
    fair_use_credits    INTEGER,               -- hard ceiling for 'unlimited' tiers

    max_export_height   INTEGER NOT NULL,      -- 720 / 1080 / 2160
    watermark           watermark_mode NOT NULL,
    queue_priority      SMALLINT NOT NULL,     -- higher runs first

    price_usd_cents     INTEGER,               -- NULL for free
    price_inr_paise     INTEGER,

    is_public           BOOLEAN NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### subscriptions

```sql
CREATE TYPE sub_status       AS ENUM ('active', 'past_due', 'cancelled', 'expired');
CREATE TYPE payment_provider AS ENUM ('stripe', 'razorpay');

CREATE TABLE subscriptions (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan                     plan_code NOT NULL REFERENCES plans(code),
    status                   sub_status NOT NULL DEFAULT 'active',

    provider                 payment_provider,   -- NULL for the free plan
    provider_customer_id     TEXT,
    provider_subscription_id TEXT,
    currency                 CHAR(3),            -- 'USD' | 'INR'

    current_period_start     TIMESTAMPTZ NOT NULL,
    current_period_end       TIMESTAMPTZ NOT NULL,
    cancel_at_period_end     BOOLEAN NOT NULL DEFAULT false,

    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One live subscription per user, whichever provider it came through.
-- This is what stops someone holding a Stripe and a Razorpay plan at once.
CREATE UNIQUE INDEX one_live_subscription ON subscriptions (user_id)
    WHERE status IN ('active', 'past_due');

CREATE UNIQUE INDEX ON subscriptions (provider, provider_subscription_id)
    WHERE provider_subscription_id IS NOT NULL;

CREATE INDEX ON subscriptions (current_period_end)
    WHERE status IN ('active', 'past_due');   -- drives the renewal sweep
```

Every user has a subscription row, including free users (`plan = 'free'`, `provider = NULL`). Uniform rows mean the renewal and grant logic has no special case for the free tier — free users get their monthly allowance through exactly the same path as paying ones.

#### payments

```sql
CREATE TYPE payment_kind   AS ENUM ('subscription', 'topup');
CREATE TYPE payment_status AS ENUM ('pending', 'succeeded', 'failed', 'refunded');

CREATE TABLE payments (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subscription_id     UUID REFERENCES subscriptions(id) ON DELETE SET NULL,

    provider            payment_provider NOT NULL,
    provider_payment_id TEXT NOT NULL,
    kind                payment_kind NOT NULL,
    status              payment_status NOT NULL DEFAULT 'pending',

    amount_minor        INTEGER NOT NULL,       -- cents or paise, never floats
    currency            CHAR(3) NOT NULL,
    credits_granted     INTEGER,                -- top-ups only

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    settled_at          TIMESTAMPTZ
);

CREATE UNIQUE INDEX ON payments (provider, provider_payment_id);

ALTER TABLE credit_ledger
    ADD CONSTRAINT fk_ledger_payment
    FOREIGN KEY (payment_id) REFERENCES payments(id) ON DELETE SET NULL;
```

Money is stored in **minor units as integers** — cents and paise. Never floats, and never a shared column for two currencies without the currency beside it.

#### provider_events

Both providers retry webhooks, sometimes for days, and both can deliver out of order. Without this table a retried `invoice.paid` grants the allowance twice.

```sql
CREATE TABLE provider_events (
    provider     payment_provider NOT NULL,
    event_id     TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    payload      JSONB NOT NULL,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ,
    error        TEXT,
    PRIMARY KEY (provider, event_id)
);
```

The handler inserts the event first and processes second. A duplicate delivery collides on the primary key and is acknowledged without being replayed.

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

### 5.3 Concurrency, priority and fairness

Per-user concurrency limits, enforced at creation and scaled by plan:

| Family | free | pro | business | studio |
|---|---|---|---|---|
| analysis | 1 | 3 | 5 | 8 |
| render (export) | 1 | 2 | 3 | 5 |
| inference | 0 | 1 | 2 | 3 |

Beyond the limit, jobs stay `queued` and start as slots free up — the request still succeeds, so the client never has to handle "try again later". These limits stop one user monopolising a worker pool, and they matter most for the GPU pool, where a single account could otherwise occupy every card.

**Queue priority** is the other half of what the tiers sell. Each family's queue is split into bands, and workers drain the highest band first:

```
analysis.p30   studio          ─┐
analysis.p20   business         ├─ one worker service,
analysis.p10   pro              │  consuming in priority order
analysis.p0    free            ─┘
```

`jobs.priority` is copied from the plan at creation and never re-read, so a downgrade mid-flight does not demote work already queued, and the router never touches the `users` table.

> **Priority is relative, not a promise.** "Fast queue" means ahead of free users, not a guaranteed completion time. Nothing in the product should show an SLA we have not measured — under light load every band is instant, and under heavy load the honest statement is the ordering, not a duration.

`studio` is described to customers as a *dedicated priority queue*, which is what band 30 is. It is not a dedicated machine: a reserved GPU instance costs several times the plan price on its own, so selling one at that price would lose money on every subscriber.

### 5.4 Credits

#### The three buckets

Credits are not one pool. They arrive by different routes and expire on different schedules, and conflating them produces either a user who loses credits they paid for or a business that gives away GPU time.

| Bucket | Where it comes from | Expires | Pays for |
|---|---|---|---|
| **`plan`** | The monthly allowance, granted at each renewal | **Yes** — swept at period end | Any tool |
| **`topup`** | Bought à la carte | **Never** | Any tool |
| **`facemap`** | Included GPU seconds, granted at each renewal | **Yes** — swept at period end | Face mapping and lip sync only |

#### Spend order: soonest to expire, first

When a job needs 50 credits and the user holds 20 `plan` and 500 `topup`, it takes **20 from `plan` and 30 from `topup`** — never 50 from `topup`.

This ordering is not a detail. Drawing from `topup` first would mean a user's monthly allowance quietly expires unused every month while the credits they *paid extra for* drain away. It looks like sharp practice, it generates refund requests, and it is trivially avoidable.

So a single job may produce **two** `reserve` rows, one per bucket — which is why `bucket` is part of the ledger's unique index.

```python
def allocate(user, cost: int) -> dict[str, int]:
    """Draw from the bucket that expires soonest. Raises if short."""
    take_plan  = min(user.plan_credits, cost)
    take_topup = cost - take_plan
    if take_topup > user.topup_credits:
        raise InsufficientCredits(required=cost,
                                  available=user.plan_credits + user.topup_credits)
    return {"plan": take_plan, "topup": take_topup}
```

Face mapping draws from `facemap` first and falls back to general credits at an overage rate once it is exhausted (§5.5) — so the meter caps our exposure without hard-blocking a paying customer mid-project.

#### Reserve, settle, refund

| Event | Ledger rows | Cached balances |
|---|---|---|
| Job created, cost 50 | `reserve` −20 `plan`, `reserve` −30 `topup` | −20 / −30 |
| Job succeeds | none — the reservation *is* the charge | unchanged |
| Job fails or is cancelled | `refund` +20 `plan`, `refund` +30 `topup` | +20 / +30 |

A refund returns credits **to the buckets they were taken from**, read back from the job's `reserve` rows rather than recomputed — recomputing could allocate differently if balances moved in between.

**One edge case worth handling explicitly.** If the billing period rolls over while a job is running and the job then fails, refunding to `plan` would credit a bucket that has already been swept and re-granted. In that case the refund goes to **`topup`** instead, which never expires. The user is made whole, and it cannot be farmed — starting a job costs the same credits either way.

Two properties hold throughout. A user cannot start work they cannot pay for, because the money moves inside the same transaction that queues the job. And a failure on our side never costs the user anything, automatically, without anyone contacting support.

The three balances on `users` are caches. Every write happens in the same transaction as its ledger row. A nightly job re-sums the ledger per user per bucket and alerts on any drift — if that alarm fires, there is a bug in a transaction boundary.

#### Fair use

`studio` is sold as unlimited. Unlimited against usage-based infrastructure has no floor, so `plans.fair_use_credits` is a hard monthly ceiling, set well above any plausible real use. Crossing it does not cut anyone off silently: the API returns `FAIR_USE_EXCEEDED`, and it is a conversation, not an outage.

### 5.5 What things cost

Cost is a function of the tool and the media duration, computed at creation from the probed `duration_ms`, so the client can show the price before the user commits.

```python
COST_PER_MINUTE = {
    "captions":       2,
    "smart_trim":     1,
    "color_analysis": 1,
    "export":         2,
    # phase 2
    "denoise":        3,
    "dereverb":       3,
    "face_map":      25,   # billed in facemap seconds first, then credits
    "lip_sync":      20,
}

FACEMAP_OVERAGE_CREDITS_PER_SECOND = 0.5   # once the included meter is spent

def cost(tool: str, duration_ms: int) -> int:
    minutes = math.ceil(duration_ms / 60_000)
    return COST_PER_MINUTE[tool] * max(1, minutes)
```

#### The plan catalogue

Proposed starting values, derived from the tiers agreed on 13 August. A ten-minute video taken through captions, smart trim, a grade and an export costs **60 credits**, which is the unit the "≈ videos per month" figure is calibrated against.

| | free | pro | business | studio |
|---|---|---|---|---|
| Price | $0 | $19.99 / ₹999 | $49.99 / ₹1,999 | $99.99 / ₹2,999 |
| `monthly_credits` | 300 | 2,500 | 8,000 | 30,000 |
| Shown as | ≈3 videos | ≈30 videos | ≈100 videos | Unlimited |
| `facemap_seconds` | 0 | 300 | 1,200 | 3,600 |
| `fair_use_credits` | — | — | — | 30,000 |
| `max_export_height` | 720 | 1080 | 2160 | 2160 |
| `watermark` | forced | none | none | custom |
| `queue_priority` | 0 | 10 | 20 | 30 |

Each tier carries roughly 25–40% more credits than its headline video count needs. That headroom is deliberate: re-running captions because the first pass misheard a name must not feel like burning a whole video, which is the failure mode that made "videos per month" unusable as an internal unit in the first place.

🟠 **The credit numbers are a proposal, the ratios are not.** Face mapping being an order of magnitude above captioning reflects real hardware cost and should survive any repricing. The absolute values should be recalibrated once we have measured cost-per-job on real hardware — everything lives in one module and one table, so a repricing is a data change.

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
| **Peaks** | One amplitude per bucket at 100 buckets/second, JSON | The timeline waveform. Computing this in the browser means downloading and decoding the whole audio track. |

The asset becomes `ready` only when all four exist. Anything unreadable becomes `failed` with a reason the interface can show.

**Proxies are not an optimisation, they are the reason browser preview is possible at all.** A 4K H.265 file will not scrub in a browser; a 480p H.264 proxy will. This is what makes the vision document's "nothing round-trips to see an edit" true.

> **Corrected 17 August, during M2.** This table previously read "min/max amplitude pairs", which would be two numbers per bucket and contradicts [`05-api-contract.md`](05-api-contract.md) §3 — whose own arithmetic ("a 10-minute file is ~60 000 numbers") only works at one value per bucket. The contract is what both sides build against, so one value per bucket is what ships: the **peak** in that hundredth of a second, not an RMS. RMS would flatten exactly the transients a waveform is read for.

> **The proxy never upscales.** `scale=-2:'min(480,ih)'`, not a flat 480 — spending encode time to make a 240p upload into a blurrier, larger 480p file helps nobody. `-2` keeps the computed width even, which `yuv420p` requires.

> **`crossOrigin` on the playback element is not optional.** The browser composites proxies through WebGL, and a frame fetched without CORS taints the canvas: `texImage2D` throws and the picture is never drawn, while the element itself loads and plays perfectly. Storage must answer with `Access-Control-Allow-Origin` — MinIO does by default; CloudFront needs a response-headers policy that says so. This cost M2 a real bug (see [`frontend/e2e/README.md`](../frontend/e2e/README.md)).

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

**The plan is enforced here, not in the client.** Two of the four things a tier sells are decided at this moment:

```python
plan = user.subscription.plan
height = min(requested_height, plan.max_export_height)   # 720 / 1080 / 2160
watermark = {
    'forced': STANDARD_WATERMARK,          # free
    'none':   None,                        # pro, business
    'custom': user.custom_watermark_key,   # studio
}[plan.watermark]
```

A client asking for 4K on the free plan is rejected with `PLAN_LIMIT_EXCEEDED` rather than silently downgraded — a user who chose 4K and received 720p files a bug report. Watermarking is never optional client-side, for the obvious reason.

**Steps:**

1. Resolve the timeline: every clip, its source asset, in/out points, transforms, effects, transitions, text.
2. Fetch **original** media — never the proxies.
3. Build one FFmpeg filter graph for the whole composition: trims, concat, scaling, crop and reframe, colour LUTs at their configured strength, text overlays with their per-word timings, transitions, audio mix with per-clip volume and fades.
4. Apply the watermark overlay if the plan requires one.
5. Encode to H.264/AAC at the resolved preset.
6. Upload to `exports/`, create a `media_assets` row, set `output_asset_id`.
7. Publish completion; the client offers a download link.

**Why server-side.** Browser export via WebCodecs is uneven across browsers and unusable on Safari for our purposes; a phone would take longer to export than to record. More importantly, this is the same pipeline that phase 2's inference jobs plug into — building it now means face mapping arrives as a new filter stage rather than a new subsystem. One render path also means a video looks the same wherever it was made, which matters the first time a user reports a colour difference.

**Progress** comes from parsing FFmpeg's `-progress` output against the known total duration, so the bar reflects real work rather than a guess.

---

## 8. Billing

Two providers run side by side from launch: **Stripe** for global cards in USD, **Razorpay** for Indian cards and UPI in INR. This is a launch-strategy decision — global creators are the primary market and India is a priority second, and Stripe's India coverage plus the RBI rules on recurring mandates make one provider insufficient for both.

The cost is real: two webhook flows, two subscription state machines, two reconciliation paths. The design below exists to keep that cost contained to one module.

### 8.1 One internal model, two adapters

Nothing outside `billing/providers/` knows which provider a user came through. Each adapter implements the same interface and translates provider events into our vocabulary:

```
billing/
  providers/
    base.py        create_checkout · create_portal_session · parse_webhook · verify_signature
    stripe.py
    razorpay.py
  service.py       plan changes, grants, expiry — provider-agnostic
  webhooks.py      one route per provider, both funnel into service.py
```

`subscriptions.provider` records the origin; everything else — allowances, gating, priority — reads `plans` and never branches on it. A third provider later is a new adapter, not a new code path through the application.

### 8.2 Currency and provider routing

The user's IP **suggests** a country; the user can override it at checkout, and the choice is stored on the subscription.

| Suggested | Provider | Currency |
|---|---|---|
| India | Razorpay | INR |
| Everywhere else | Stripe | USD |

IP alone is unreliable — VPNs, travellers, expatriates — so it is a default, never a lock. Someone in London paying with an Indian card must be able to choose INR, and someone in Mumbai billing a foreign company must be able to choose USD.

**Once chosen, the provider is fixed for the life of the subscription.** Switching means cancelling and resubscribing, because no provider can migrate another's mandate. The prices differ between currencies (regional pricing), so a switch is a genuine plan change and should be presented as one.

### 8.3 Subscription lifecycle

```
                    checkout completed
   free ────────────────────────────────► active
     ▲                                    │  │  ▲
     │                                    │  │  │ payment recovered
     │              period ends,          │  │  │
     │              not renewed           │  ▼  │
     └──────────── expired ◄───────────── past_due
                      ▲                      │
                      │  period ends         │ dunning exhausted
                      └───── cancelled ◄─────┘
                              (cancel_at_period_end)
```

- **Cancelling** sets `cancel_at_period_end`. Access and credits continue until `current_period_end`, then the account drops to `free` and the remaining `plan` and `facemap` balances are swept. Purchased `topup` credits survive — they were paid for separately and never expire.
- **`past_due`** is a failed renewal. The provider retries on its own schedule; we keep the plan active during that window rather than cutting service off on a first decline, which is usually an expired card rather than an unwilling customer.
- **Upgrades** apply immediately and grant the difference in allowance pro rata. **Downgrades** apply at the next period boundary, so nobody loses credits they are mid-way through using.

### 8.4 Renewal and expiry

Every renewal does two things in one transaction: sweep what expired, then grant the new period.

```sql
BEGIN;
  -- 1. sweep — plan and facemap only; topup is untouched
  INSERT INTO credit_ledger (user_id, bucket, delta, reason, balance_after)
       VALUES (:uid, 'plan',    -:remaining_plan,    'plan_expiry', 0),
              (:uid, 'facemap', -:remaining_facemap, 'plan_expiry', 0);

  -- 2. grant the new allowance
  INSERT INTO credit_ledger (user_id, bucket, delta, reason, balance_after)
       VALUES (:uid, 'plan',    :plan_credits,   'plan_grant', :plan_credits),
              (:uid, 'facemap', :facemap_seconds,'plan_grant', :facemap_seconds);

  UPDATE users SET plan_credits = :plan_credits, facemap_seconds = :facemap_seconds
   WHERE id = :uid;

  UPDATE subscriptions
     SET current_period_start = :new_start, current_period_end = :new_end
   WHERE id = :sub_id;
COMMIT;
```

**Two triggers, deliberately.** The provider webhook (`invoice.paid` / `subscription.charged`) is the primary path and fires within seconds of payment. An hourly sweep over `subscriptions.current_period_end` is the safety net for webhooks that never arrive — they do get lost, and a user whose allowance silently failed to renew is a support ticket we should never receive. Both paths are idempotent through `provider_events` and a period-boundary check, so the sweep firing after the webhook is a no-op.

Free-tier users have no provider and no webhook. Their allowance is granted entirely by the hourly sweep, through the same code path.

### 8.5 Webhook handling

Both providers retry, sometimes for days, and both can deliver out of order.

1. **Verify the signature** before parsing anything. An unsigned or mis-signed request is dropped with `400` and logged — this endpoint is public.
2. **Insert into `provider_events`.** A duplicate collides on `(provider, event_id)`; acknowledge `200` and stop. Never process twice.
3. **Acknowledge immediately, process asynchronously.** Both providers treat a slow response as a failure and retry. The route returns `200` as soon as the event is stored; a worker does the work.
4. **Ignore events for periods already applied.** Out-of-order delivery is normal; a grant for a period we have already granted is dropped rather than doubled.

The endpoints are unauthenticated by necessity and excluded from the normal rate limiter, but rate-limited separately by source IP.

### 8.6 What is not built here

Named so nobody assumes otherwise: invoicing documents, tax calculation and remittance (GST in India, VAT in the EU), and accounting export. Both providers offer tax products that cover most of this, but **choosing and configuring them is not a backend task** and needs an owner outside the development team.

---

## 9. Live updates

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

## 10. Security

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

## 11. Infrastructure

| Component | Choice | Notes |
|---|---|---|
| **API** | FastAPI on ECS Fargate, ≥2 replicas behind an ALB | Stateless; WebSocket connections are sticky per connection but need no shared session |
| **Database** | RDS PostgreSQL 16, Multi-AZ | pgvector enabled in phase 2 |
| **Cache / broker** | ElastiCache Redis 7 | Celery broker, cache, pub/sub |
| **Workers** | Celery on ECS, **one service per queue** | Independent scaling; a backlog of exports must not starve captioning. Each service consumes its priority bands in order (§5.3) |
| **Scheduler** | Celery beat, single instance with a lock | Hourly renewal sweep, nightly ledger reconciliation, storage lifecycle. Must not run twice — the lock is not optional |
| **GPU workers** | Phase 2 — g5.xlarge or equivalent, autoscaling to zero | Idle GPUs are the most expensive mistake available to us |
| **Storage** | S3 + CloudFront | Lifecycle rules per prefix (§6.3) |
| **Migrations** | Alembic | Every schema change reviewed; no destructive migration without a backfill plan |
| **Config** | Environment variables, secrets in Secrets Manager | Nothing credential-shaped in the repository |

**Observability**, because a job pipeline that fails silently is worse than no pipeline:

- Structured JSON logs carrying `request_id`, `user_id`, `job_id` on every line
- Metrics: queue depth **per priority band**, job duration by tool, failure rate by `error_code`, credits reserved vs settled per bucket, storage per user, **cost per job by tool**
- Alerts: queue depth over threshold, failure rate above 5% for any tool, ledger reconciliation drift, any job `running` longer than its family's ceiling, **unprocessed `provider_events` older than 15 minutes**, **renewals missed by the hourly sweep**
- Tracing across API → queue → worker on the job id

**Cost per job is a metric, not a spreadsheet.** The plan credit values in §5.5 are estimates until real hardware says otherwise, and the only way to know whether a tier is profitable is to measure what a job actually costs and compare it to what was charged. Instrument this from the first deploy — retrofitting it means guessing for another quarter.

### Local development

`docker compose` brings up Postgres, Redis, MinIO (S3-compatible) and the worker pool. No AWS account is needed to run the whole system locally, including uploads and exports — MinIO speaks the same presigned-URL protocol.

---

## 12. What phase 2 and 3 add

The point of this design is that later phases add workers, not architecture.

| | Adds | Changes |
|---|---|---|
| **Phase 2** — face mapping, lip sync, denoise | GPU worker pool on the `inference` queue · `face_profiles` and `consent_records` · pgvector · consent flow at upload | Nothing structural. New `job_tool` values, new worker services, and the `facemap` bucket — already in the schema — starts being spent. |
| **Phase 3** — clip finder, templates, upscaling | Speaker tracking in the analysis worker · template definitions · a licensed music catalogue | Timeline document gains a `templateId`; clip finder creates projects via the existing project API. |
| **Mobile** | Nothing on the server | The API is already client-agnostic (principle 5). A native client is a new consumer of the same contract. |

The one thing to guard: **every new tool must be pushed into the analysis family if it possibly can be.** A tool that returns decisions is cheaper to run, faster for the user, and leaves the result editable. Reach for the GPU only when pixels genuinely have to change.

---

## 13. Decisions recorded here

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
| 11 | Three credit buckets, spent soonest-to-expire first | One pool | Subscription credits expire and purchased ones do not; spending the wrong one first silently destroys value the user paid for (§5.4) |
| 12 | Plans are rows in a table | Constants in code | Pricing changes more often than anything else here, and a price change should not need a deploy |
| 13 | Every user has a subscription row, free included | A nullable plan on `users` | Renewal, grant and expiry then have no special case for free users |
| 14 | Provider-agnostic core, one adapter per provider | Branching on provider throughout | Two providers at launch and probably a third later; the cost has to stay in one module (§8.1) |
| 15 | Webhooks stored before they are processed | Processing inline | Both providers retry for days and deliver out of order; without this a retried renewal grants the allowance twice (§8.5) |
| 16 | Job priority copied from the plan at creation | Joined from the user at dispatch | A downgrade mid-flight must not demote queued work, and the router should never touch `users` |
| 17 | Plan limits enforced in the renderer | Enforced in the client | A client-side watermark is not a watermark |

---

*AI Video Editor · Backend Architecture v1.0 · 12 August 2026*

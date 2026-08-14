# Phase 1 — Working checklist

**Temporary working file.** Not part of the documentation set in [`docs/`](docs/) — this is the day-to-day task list, meant to be edited constantly and deleted when phase 1 ships.

Scope comes from [`docs/02-scope-v1.md`](docs/02-scope-v1.md). Where a task is ambiguous, that document wins.

---

## How this is ordered

Built for **one full-stack developer**, not parallel teams. That changes the ordering in two ways:

1. **Riskiest thing first.** The compositor is the only part with no library to fall back on. If it resists, everything after it moves — better to know in week one.
2. **Vertical slices, not layers.** Each milestone ends with something that visibly works end to end. Building all the backend then all the frontend means three months before anything runs.

**Legend** — `⚠️` risky or unknown · `🔗` blocks something later · `💤` can be deferred if time is short

---

## M0 · Foundations

*Ends when: `docker compose up` gives you a database, a queue, object storage and two dev servers.*

### Repositories

- [ ] Merge `docs/` from `dev` into `main`
- [ ] Decide repo layout: monorepo, or `zipzop-backend` + `zipzop-frontend` separately
- [ ] `.gitignore`, `README` pointing at `docs/`, branch protection on `main`
- [ ] `.env.example` in both, with every variable named and nothing secret committed

### Backend skeleton

- [ ] FastAPI project — `app/`, `api/`, `models/`, `services/`, `workers/`, `tests/`
- [ ] Poetry or uv, pinned Python 3.12
- [ ] SQLAlchemy 2.0 + Alembic wired, one empty migration proving it runs
- [ ] Settings via Pydantic, all config from environment
- [ ] Structured JSON logging with `request_id` middleware
- [ ] `GET /health` returning database and Redis status

### Frontend skeleton

- [ ] Next.js + TypeScript, App Router
- [ ] Note in the README: **the editor is client-only** (`'use client'` throughout — WebGL, video elements and timeline state cannot be server-rendered). Next.js earns its place on the marketing and pricing pages
- [ ] Zustand + Immer, TanStack Query, styling choice made and committed to
- [ ] Route shells: `/`, `/login`, `/projects`, `/editor/[id]`, `/pricing`, `/settings/billing`

### The thing that keeps both sides honest 🔗

- [ ] FastAPI generating `openapi.json`, committed, even with every endpoint a stub
- [ ] Frontend types generated from it (`openapi-typescript`), wired into the build
- [ ] Mock server (Prism or MSW) serving fixtures from the same schema
- [ ] Fixtures written early: a 2,000-word caption result **with a deliberately misspelled name**, a smart-trim result, a failing job, an account with credits split across two buckets, a free account hitting `PLAN_LIMIT_EXCEEDED`

### Local infrastructure

- [ ] `docker-compose.yml`: Postgres 16, Redis 7, MinIO
- [ ] MinIO bucket + presigned URL flow working — **this is what makes AWS unnecessary for now**
- [ ] Celery worker and beat containers, one no-op task proving dispatch works
- [ ] `make dev` / `make test` / `make migrate` so nothing is remembered by hand
- [ ] CI: lint, type-check, tests on every push

---

## M1 · Compositor spike ⚠️

*Ends when: two clips play back to back with a colour grade and a text overlay, in Chrome and Safari.*

**Throwaway code. No state management, no UI, no cleanliness.** The only question is whether the browser can do this. Do it before anything else in the frontend.

- [ ] Two hidden `<video>` elements on hardcoded proxy files
- [ ] WebGL2 canvas drawing the current frame
- [ ] Clock driven by the playing element's `currentTime`, **not** `performance.now()`
- [ ] `requestVideoFrameCallback` loop, with a `requestAnimationFrame` fallback
- [ ] Cut from clip A to clip B with no black flash — preload B, seek it, `play()` then immediately `pause()`
- [ ] LUT applied as a `TEXTURE_3D` in the fragment shader, with a strength uniform
- [ ] Text overlay on a 2D canvas layered above
- [ ] Crossfade between the two clips
- [ ] **Test on Safari** — different codec support, autoplay rules, WebGL quirks
- [ ] Measure: does it hold 60 fps at 1080p preview?

> **If this takes more than a week, stop and say so.** It is the one item on this list that can change the shape of the project.

---

## M2 · Sign up, upload, see it on a timeline

*Ends when: you can register, upload a real video, and see it as a clip with a waveform, and scrub it smoothly.*

### Backend — schema 🔗

- [ ] Migration: `users`, `refresh_tokens`
- [ ] Migration: `media_assets` (including `derived_from_asset_id`, `derived_by_job_id`)
- [ ] Migration: `projects`, `project_assets`
- [ ] Migration: `jobs` (including `priority`)
- [ ] Migration: `plans`, `subscriptions`, `payments`, `provider_events`
- [ ] Migration: `credit_ledger` with `bucket`, and the unique index on `(job_id, reason, bucket)`
- [ ] Seed the four plans with the values from [`docs/03-backend-architecture.md`](docs/03-backend-architecture.md) §5.5
- [ ] Repository layer where **every query filters on `user_id`** — a route that forgets is a data leak

### Backend — auth

- [ ] `POST /auth/register` — bcrypt cost 12, free plan + signup allowance granted in the same transaction
- [ ] `POST /auth/login` — identical response for wrong password and unknown email
- [ ] `POST /auth/refresh` — rotating tokens; reuse of a rotated token revokes the whole chain
- [ ] `POST /auth/logout`, `GET /me` with three balances and plan limits
- [ ] JWT middleware, 15-minute access tokens
- [ ] Rate limiting: 100/min general, 20/min on auth per IP

### Backend — media 🔗

- [ ] `POST /media/uploads` — quota check, presigned PUT, 15-minute expiry
- [ ] Multipart for files over 100 MB
- [ ] `POST /media/{id}/complete` — verify object exists and size matches, enqueue ingest
- [ ] Ingest worker: `ffprobe` → duration, dimensions, fps, codecs
- [ ] Ingest worker: 480p H.264 faststart **proxy** ⚠️ — the whole editor depends on this
- [ ] Ingest worker: thumbnail at ~10%
- [ ] Ingest worker: waveform peaks JSON, 100 buckets/second
- [ ] Asset only becomes `ready` when all four exist
- [ ] `GET /media/{id}` with signed CDN URLs, 1-hour expiry
- [ ] `GET /media`, `DELETE /media/{id}` with `ASSET_IN_USE` guard
- [ ] Reject unreadable media with a reason a person can read

### Frontend

- [ ] Register / login / logout, token refresh on 401 with one retry
- [ ] Upload with real progress from the browser's own events
- [ ] Media bin showing ingest status, thumbnails, durations
- [ ] Timeline shell: ruler, playhead, zoom, one video track
- [ ] Waveform drawn to `<canvas>` from peaks — **never one DOM node per peak**
- [ ] Clip rendered on the track, scrubbable
- [ ] Compositor from M1 cleaned up and wired to the real proxy

---

## M3 · Editing that survives a reload

*Ends when: you can cut, arrange, undo, close the tab, and come back to exactly what you left.*

### Frontend — the document

- [ ] Timeline document type generated from the OpenAPI schema — **same shape as the API**, no client variant
- [ ] Zustand store: `timeline`, `selection`, `playhead`, `zoom`, `version`, `isDirty`
- [ ] `commit(label, recipe)` using `produceWithPatches` — undo/redo from Immer patches, not hand-written inverses
- [ ] Undo/redo stacks capped at 200, keyboard bound
- [ ] Memoised selectors for anything derived — nothing derived stored in the document

### Frontend — editing

- [ ] Split at playhead, trim both ends, move, reorder, duplicate, delete
- [ ] **Drags use local state and commit once on drop** — never per pointer move
- [ ] Snapping to clip edges, playhead and zero, with a modifier to suppress
- [ ] Selection: click, shift-click, marquee
- [ ] Keyboard: space, `S`, arrow nudge by one frame
- [ ] Clip properties: volume, speed, rotate, flip, crop and reframe
- [ ] Audio track: music clip, per-clip volume, fades — Web Audio gain automation
- [ ] Text track: add a title, font, size, colour, position
- [ ] Transitions: cut, fade to black, cross dissolve
- [ ] Timeline virtualised by time window ⚠️ — must stay smooth at 500 clips

### Backend — projects

- [ ] `POST /projects`, `GET /projects`, `GET /projects/{id}` with assets and fresh signed URLs
- [ ] `PATCH /projects/{id}` — timeline + version, `409` on stale version
- [ ] **Timeline validation on every write** — all eight invariants from [`docs/05-api-contract.md`](docs/05-api-contract.md) §4.3, rejecting with the offending clip named
- [ ] `project_assets` rebuilt from the document on each save
- [ ] `duration_ms` derived on save
- [ ] Duplicate, soft delete

### Frontend — persistence

- [ ] Autosave debounced 2 s, plus on blur and `visibilitychange`
- [ ] One save in flight at a time; never mid-drag
- [ ] `409` → dialog: "Keep mine" / "Load the other version". **No automatic merge**
- [ ] IndexedDB mirror on every commit, restore offer on open 💤
- [ ] `sendBeacon` flush on `pagehide`

---

## M4 · The first AI tool works

*Ends when: you run captions on a real clip, watch progress, see the words appear in time, and fix a misspelled name.*

### Backend — job pipeline 🔗

- [ ] `POST /jobs` — validate, resolve assets, price, reserve credits, insert, enqueue, **all in one transaction**
- [ ] `allocate()` — plan credits first, then top-up. Two ledger rows when it spans both
- [ ] Idempotency key handling, replay returns the original job
- [ ] Per-plan concurrency caps
- [ ] Priority bands, queues split per family
- [ ] Worker claim: `UPDATE ... WHERE status='queued'`, stop if zero rows
- [ ] Progress published to Redis at real checkpoints, not on a timer
- [ ] Retry transient failures 3× with backoff; permanent failures go straight to `failed`
- [ ] Refund on failure, **to the buckets it took from**, read back from the reservation rows
- [ ] Period-rollover edge case: refund to `topup` instead
- [ ] `GET /jobs/{id}`, `GET /jobs`, `POST /jobs/{id}/cancel`, `POST /jobs/estimate`
- [ ] Results over 256 KB go to S3, `result_key` instead of `result`
- [ ] WebSocket `/ws` + Redis pub/sub fan-out
- [ ] Nightly ledger reconciliation, alerting on drift

### Backend — the three tools

- [ ] **Captions** — transcribe, word-level timings, emphasis detection. Language list confirmed
- [ ] **Smart trim** — silence, filler, stutter, repeat detection. Three strengths. Ranges in **asset time**
- [ ] **Colour analysis** — returns LUT name + strength + alternatives
- [ ] Ship 3 caption styles and 5 LUTs, with the `.cube` files **shared with the frontend** ⚠️

### Frontend — tools

- [ ] Estimate on panel open, price on the button
- [ ] Invoke with idempotency key, badge on the clip, editing continues
- [ ] WebSocket progress, polling fallback every 3 s
- [ ] Re-sync via `GET /jobs?status=running` on reconnect
- [ ] **Captions → one text clip per word, in a single `commit`** — one undo step for 1,800 clips
- [ ] Smart trim → splits and removals, one `commit`
- [ ] Colour grade → one `effects` entry, picture changes instantly
- [ ] ⚠️ **Asset time → timeline time conversion**, unit-tested on a trimmed, sped-up clip
- [ ] Caption editing: correct a word, retime, restyle, delete
- [ ] Low-confidence words flagged visually
- [ ] Failure states mapped from `errorCode`, with retry, saying the credits came back

---

## M5 · A file comes out

*Ends when: you export a 1080p 9:16 MP4 and it looks exactly like the preview.*

- [ ] `POST /jobs {tool: export}` — reject stale `timelineVersion` with `409`
- [ ] Render worker: resolve timeline, fetch **originals**
- [ ] One FFmpeg filter graph: trims, concat, transform, crop, LUT at strength, text with per-word timings, transitions, audio mix with fades
- [ ] Plan gating: resolution ceiling, `PLAN_LIMIT_EXCEEDED` rather than silent downgrade
- [ ] Watermark applied server-side when the plan requires it
- [ ] Progress parsed from FFmpeg `-progress`
- [ ] Upload to `exports/`, create asset, 30-day expiry
- [ ] ⚠️ **Frame comparison test: browser output vs FFmpeg output on the same grade.** If these drift, users edit one picture and download another
- [ ] Frontend: export dialog, presets gated by plan, progress, download

---

## M6 · Money

*Ends when: you hit the free limit, subscribe, and the new allowance appears within seconds.*

- [ ] Stripe and Razorpay **accounts opened** 🔗 — external lead time, start this now
- [ ] `billing/providers/` — one adapter per provider, identical interface
- [ ] `GET /plans` — public, currency suggested by IP, overridable
- [ ] `POST /billing/checkout` → hosted checkout URL
- [ ] `POST /billing/topup`, `/portal`, `/cancel` with the "you will lose X credits" response
- [ ] Webhooks: **verify signature → store in `provider_events` → 200 immediately → process async**
- [ ] Duplicate events collide on the primary key and are dropped
- [ ] Renewal: sweep `plan` + `facemap`, grant new allowance, **never touch `topup`**, one transaction
- [ ] Celery beat hourly sweep as the safety net — and the only path for free users
- [ ] Upgrade immediate + pro rata; downgrade at period end
- [ ] `GET /credits/ledger` with buckets
- [ ] Cost-per-job metric instrumented ⚠️ — the tiers get re-priced on this
- [ ] Frontend: pricing page, checkout redirect, **confirming state on return** (never trust the redirect), 30 s polling fallback
- [ ] Frontend: balances — one number everywhere except billing, cancellation and face mapping
- [ ] Frontend: paywalls that name the unblock and link to it. **No dead ends**
- [ ] Frontend: running out mid-project never blocks plain editing and never loses work

---

## Before calling it done

Walk [`docs/02-scope-v1.md`](docs/02-scope-v1.md) §6 end to end, as someone who has never seen the product. All fourteen steps, no explanations allowed.

- [ ] Safari, Chrome, Firefox
- [ ] A 60-minute upload
- [ ] A project with 500 clips
- [ ] Slow network, dropped socket, closed tab mid-job
- [ ] An account that runs out of credits halfway through

---

## Deliberately not in phase 1

Face mapping · lip sync · GPU cluster · noise removal · upscaling · stabilisation · clip finder · speaker tracking · templates · music · mobile · multiple video tracks · caption translation · subtitle files · custom LUTs · teams · publishing to TikTok/YouTube/Instagram · invoicing documents · tax configuration

If one of these starts feeling necessary, it is a scope conversation, not a task.

---

## Order to actually work in

1. **M0 foundations** — a day or two, and everything after depends on it
2. **M1 spike** — before any real frontend work
3. **M2 backend before M2 frontend** — the frontend needs real proxies to be worth building against
4. **M3 is the long one.** It is most of the frontend, and it is where a schedule slips quietly
5. **M4, M5, M6** are each self-contained and can be reordered if something external forces it

Start the Stripe and Razorpay applications during M0 — they take calendar time nobody here controls, and M6 stalls without them.

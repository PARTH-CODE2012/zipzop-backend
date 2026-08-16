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
- [x] Decide repo layout — **monorepo**: `backend/` and `frontend/` inside the existing repository. Shared `openapi.json` and one commit per contract change; splitting later is straightforward
- [x] `.gitignore`, root `README` pointing at `docs/`, `.env.example` with every variable named
- [ ] Branch protection on `main`  *(GitHub setting — needs repo admin)*

### Backend skeleton — done, verified running

- [x] FastAPI project — `app/`, `api/`, `models/`, `services/`, `workers/`, `scripts/`, `tests/`
- [x] pip + venv, `requires-python = ">=3.12"`, ruff and mypy strict configured
- [x] SQLAlchemy 2.0 + Alembic wired, baseline migration `0001_baseline` installing `pgcrypto` and `citext`
- [x] Settings via `pydantic-settings`, everything from the environment, `assert_production_safe()` refusing to boot production with dev defaults
- [x] Structured JSON logging, `request_id` middleware, `X-Request-ID` echoed back
- [x] `GET /health/live` (never touches dependencies) and `GET /health` (reports Postgres + Redis, 503 when degraded)
- [x] One error envelope for the whole API — every code from the contract §9 defined
- [x] Celery app, queues routed by family, beat schedule for renewals and reconciliation
- [x] `pytest` — 6 tests green · `ruff` clean · `mypy --strict` clean

### Frontend skeleton — done, build verified

- [x] Next.js 15 + TypeScript, App Router, Tailwind v4
- [x] README stating **the editor is client-only**, with the reasoning
- [x] TanStack Query provider; Zustand + Immer declared *(the timeline store lands in M3, once its types can be generated rather than hand-written)*
- [x] Route shells: `/`, `/login`, `/projects`, `/editor/[id]`, `/pricing`, `/settings/billing`
- [x] API client with the error envelope and single-flight token refresh
- [x] `pnpm install` · `pnpm lint` · `pnpm typecheck` · `pnpm build` — all green, 7 routes built
- [x] ESLint flat config built from the plugins directly — `eslint-config-next` loads `@rushstack/eslint-patch`, which fails against ESLint 9, and `next lint` is removed in Next 16

### The thing that keeps both sides honest 🔗

- [x] FastAPI generating `openapi.json`, committed at the repository root
- [x] `make openapi` / `make types` / `make contract-check`, and CI failing on a stale schema
- [x] Frontend types generated from it into `src/lib/api/generated.ts`
- [ ] Mock server (Prism or MSW) serving fixtures from the same schema
- [ ] Fixtures written early: a 2,000-word caption result **with a deliberately misspelled name**, a smart-trim result, a failing job, an account with credits split across two buckets, a free account hitting `PLAN_LIMIT_EXCEEDED`

### Local infrastructure

- [x] `docker-compose.yml`: Postgres 16, Redis 7, MinIO + bucket bootstrap, api, worker, beat
- [x] Backend `Dockerfile` — multi-stage, ffmpeg included, non-root in production
- [x] `make` targets: setup, up, down, migrate, dev, test, lint, openapi, check
- [x] CI: lint, type-check, migrations apply, tests, contract freshness, frontend build
- [x] Toolchain installed: Docker 28.5, Compose 2.40, ffmpeg 8.1, npm 11 / Node 24
- [x] `make docker-ok` guard — fails early with the actual fix instead of a socket error
- [x] Native fallback documented and `make native-check` added — **Docker Hub is too slow on this connection**, and apt-installed Postgres 18 and Redis 8 behave identically up to M2
- [x] `make migrate` applied `0001_baseline` against the real database — `citext` and `pgcrypto` installed
- [x] Celery `ingest.ping` dispatched and returned `SUCCESS` end to end, queues declared in Redis
- [x] `/health` returning **`ok`** with both dependencies live (was `degraded`/503 with them down — both paths verified)
- [ ] MinIO presigned URL flow — **deferred to M2**, when uploads first need object storage. Nothing before then touches S3

> **M0 is complete and verified.** Both quality gates green, contract fresh, infrastructure live, queue proven. The only unexercised piece is object storage, which nothing needs yet.

### Docker: parked, not blocking

Pulling four images at once timed out — one Docker Hub address answers in 4.4 s against 0.8 s over IPv6, and the parallel pull blew past the deadline. Interrupting a pull is harmless: layers are cached and a retry resumes.

Not worth fighting now, because **M0 needs nothing Docker provides that apt does not**. Revisit before M2, when MinIO becomes necessary.

- [ ] `sudo usermod -aG docker maxime` is done and recorded; still needs **a new login session** to take effect
- [ ] `make pull` on a better connection, or overnight

---

## M1 · Compositor spike ⚠️

*Ends when: two clips play back to back with a colour grade and a text overlay, in Chrome and Safari.*

**Throwaway code. No state management, no UI, no cleanliness.** The only question is whether the browser can do this. Do it before anything else in the frontend.

Runs at `/spike/compositor` after `make spike-media`. Findings, measurements and the two bugs it caught are written up in [`frontend/src/spike/compositor/README.md`](frontend/src/spike/compositor/README.md).

- [x] Two hidden `<video>` elements on hardcoded proxy files
- [x] WebGL2 canvas drawing the current frame
- [x] Clock driven by the playing element's `currentTime`, **not** `performance.now()`
- [x] `requestVideoFrameCallback` loop, with a `requestAnimationFrame` fallback — both paths exercised, with a stall watchdog so a callback that never fires cannot kill the loop
- [x] Cut from clip A to clip B with no black flash — **zero black frames** across full playbacks in both modes, sampled after every draw. Needed two fixes: skip the draw rather than paint black when a clip has no frame yet, and hold the playhead instead of sliding on the wall clock while media catches up
- [x] LUT applied as a `TEXTURE_3D` in the fragment shader, with a strength uniform — returns the `.cube` table **exactly** at every grid point (worst delta 0/255)
- [x] Text overlay on a 2D canvas layered above, redrawn only when the visible words change
- [x] Crossfade between the two clips — both on screen at the midpoint, equal-power audio, no discontinuity
- [ ] **Test on Safari** — different codec support, autoplay rules, WebGL quirks. *Not testable from Linux. The code is written for it — H.264 only, `playsinline`, muted priming, rAF fallback — but that is not the same as tested. **Open the page on a Mac and an iPhone before M2 starts.*** ⚠️
- [x] Measure: does it hold 60 fps at 1080p preview? — **yes, 85 fps**, ~40 % headroom on integrated graphics. 1 dropped frame in 454 at source rate. The cost is the video→texture upload, not the shader: 720p, 1080p and 4K are within a millisecond of each other

### Also landed with it

- [x] `make spike-media` — ffmpeg generates two 480p H.264 faststart proxies and a 17³ `.cube` LUT, gitignored and reproducible
- [x] 45 unit tests on the pure parts: asset-time ↔ timeline-time on trimmed and sped-up clips, the `.cube` parser, letterboxing, and the §4.3 invariants over the spike's own document
- [x] `pnpm test` fixed — it was `vitest` in watch mode, so `make test-frontend` could never have passed in CI

> **Not proven, deliberately:** Safari, the wall-clock fallback (this timeline has no gaps), the three-element decoder pool (two clips, two elements), real ingest proxies, and Web Audio. Each is listed in the spike's README with when it gets covered.

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

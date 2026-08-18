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

- [x] Decide repo layout — **monorepo**: `backend/` and `frontend/` inside the existing repository. Shared `openapi.json` and one commit per contract change; splitting later is straightforward
- [x] `.gitignore`, root `README` pointing at `docs/`, `.env.example` with every variable named
- [x] **`dev` is the working branch and stays it.** Merging `docs/` into `main` is dropped outright — nobody builds against `main`, so keeping it in sync is ceremony. **Branch protection on `main` is not dropped, it moves to M7**, which already claims it and is right to: it is a supply-chain control ([`docs/07-security.md`](docs/07-security.md) §5.3). It buys nothing while one person pushes to `dev`, and it matters before anything deploys from `main`

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

> **The mock server and its fixtures moved to M4**, where they are first used. They were listed here on the principle of writing fixtures early, and the principle is right, but the schema they would be written against still has no jobs, no timeline and no projects in it. Writing them now means writing them twice.

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
- [x] MinIO presigned URL flow — **done in M2.** Presigned PUT and GET verified against the real server, including that the signature covers `Content-Type` (a mismatch is a 403) and that the bucket is private without one

> **M0 is complete and verified.** Both quality gates green, contract fresh, infrastructure live, queue proven. The only unexercised piece is object storage, which nothing needs yet.

### Docker: parked, not blocking

Pulling four images at once timed out — one Docker Hub address answers in 4.4 s against 0.8 s over IPv6, and the parallel pull blew past the deadline. Interrupting a pull is harmless: layers are cached and a retry resumes.

Not worth fighting now, because **M0 needs nothing Docker provides that apt does not**. Revisit before M2, when MinIO becomes necessary.

- [x] ~~`sudo usermod -aG docker maxime` is done and recorded; still needs **a new login session** to take effect~~ — the group is in `/etc/group`; a shell that has not picked it up can use `sg docker -c '…'` without logging out
- [x] ~~`make pull` on a better connection, or overnight~~ — **the connection is no longer the problem.** Measured 17 August: `alpine` in 5 s, `minio/minio` + `minio/mc` in 56 s. MinIO is up, the bucket is bootstrapped, and the presigned upload flow is exercised by the test suite and the end-to-end run

---

## M1 · Compositor spike ⚠️

*Ends when: two clips play back to back with a colour grade and a text overlay, in Chrome and Safari.*

**Throwaway code. No state management, no UI, no cleanliness.** The only question is whether the browser can do this. Do it before anything else in the frontend.

Runs at `/spike/compositor` after `make spike-media`. Findings, measurements and the two bugs it caught are written up in [`frontend/src/editor/playback/README.md`](frontend/src/editor/playback/README.md).

- [x] Two hidden `<video>` elements on hardcoded proxy files
- [x] WebGL2 canvas drawing the current frame
- [x] Clock driven by the playing element's `currentTime`, **not** `performance.now()`
- [x] `requestVideoFrameCallback` loop, with a `requestAnimationFrame` fallback — both paths exercised, with a stall watchdog so a callback that never fires cannot kill the loop
- [x] Cut from clip A to clip B with no black flash — **zero black frames** across full playbacks in both modes, sampled after every draw. Needed two fixes: skip the draw rather than paint black when a clip has no frame yet, and hold the playhead instead of sliding on the wall clock while media catches up
- [x] LUT applied as a `TEXTURE_3D` in the fragment shader, with a strength uniform — returns the `.cube` table **exactly** at every grid point (worst delta 0/255)
- [x] Text overlay on a 2D canvas layered above, redrawn only when the visible words change
- [x] Crossfade between the two clips — both on screen at the midpoint, equal-power audio, no discontinuity
- [~] **Test on Safari** — **iPhone verified 2026-08-16**, from a screen recording and a HUD capture:
  - `driver rvfc` · `clock video` · **29.5 fps** · frame cost **8.18 ms** (against 8.42 ms on desktop AMD) · **0 / 89 dropped** · **0 draws skipped** · `primed A✓ B✓` · `play errors none` · GPU `Apple GPU` · canvas 1920×1080
  - **No black frame at the cut**, confirmed across 841 recorded frames of the canvas: minimum luminance **106.6/255**, nothing below 30. The only two picture changes are the cut (117.0 → 107.7) and the loop wrap (106.8 → 117.6), each a single clean step
  - In-point arithmetic exact on device — after the loop wrap the playhead reads 766 ms and the burnt-in timecode reads 1267 ms, against 500 + 766 = **1266 ms**
  - So the three iOS unknowns are answered: `requestVideoFrameCallback` exists, muted priming is not refused, and autoplay does not block
- [x] **Crossfade on iOS** — **verified 2026-08-16.** The engine really is in crossfade mode, not just the button: total duration reads `0:11.100`, which is 12 000 − 900 ms of overlap. Playhead 10 000 ms against a burnt-in `00:00:05.233` on clip B, where 300 + (10 000 − 5 100) = 5 200 ms — one frame. **No black frame across 519 recorded frames**, minimum luminance 63.3/255 and that is the first frame replacing the loading state; steady playback stays above 100. **iOS's cap on simultaneous video playback did not bite** — the risk that could have forced crossfades off mobile entirely
- [ ] **Safari on macOS** 💤 — **blocked on hardware, not on time.** Nobody on the project has a Mac; this waits on borrowing one. Low risk now that iOS, the harder case, passes — but "low risk" is a prediction, and iOS passing is evidence about a different build of the engine, so it stays open rather than being closed on inference
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

**Done and proven, 17 August 2026.** 92 backend tests, 96 frontend tests, and a 29-check end-to-end run driving a real Chromium from an empty database to a clip playing back — three times, no flake. Write-up in [`frontend/e2e/README.md`](frontend/e2e/README.md).

### Backend — schema 🔗

- [x] Migration: `users`, `refresh_tokens`
- [x] Migration: `media_assets` (including `derived_from_asset_id`, `derived_by_job_id`)
- [x] Migration: `projects`, `project_assets`
- [x] Migration: `jobs` (including `priority`)
- [x] Migration: `plans`, `subscriptions`, `payments`, `provider_events`
- [x] Migration: `credit_ledger` with `bucket`, and the unique index on `(job_id, reason, bucket)`
- [x] Seed the four plans with the values from [`docs/03-backend-architecture.md`](docs/03-backend-architecture.md) §5.5
- [x] Repository layer where **every query filters on `user_id`** — enforced structurally: a `ScopedRepository` cannot be built without a user and every query starts from `_select()`, which already carries the filter. Proved from the outside with two accounts against every media endpoint
- [x] `0002_m2_schema` written by hand, not autogenerated — autogenerate silently re-creates native enum types, drops `postgresql_where` from partial indexes, and cannot order the circular FK between `media_assets` and `jobs`. All twelve partial indexes verified present in the live schema
- [x] `alembic check` added to CI — the guard that catches a model and a migration drifting apart. It found two real drifts on the first run: check constraints taking a double `ck_` prefix, and `postgresql_ops` being used for sort order when it carries operator classes

### Backend — auth

- [x] `POST /auth/register` — bcrypt cost 12, free plan + signup allowance granted in the same transaction
- [x] `POST /auth/login` — identical status, code **and message** for wrong password and unknown email, plus a dummy verify so the two take the same time
- [x] `POST /auth/refresh` — rotating tokens; reuse of a rotated token revokes the whole chain
- [x] `POST /auth/logout`, `GET /me` with three balances and plan limits
- [x] JWT middleware, 15-minute access tokens
- [x] Rate limiting: 100/min general, 20/min on auth per IP, with `Retry-After`
- [x] **Contract change:** the refresh token is now an httpOnly cookie rather than a body field — [`docs/05-api-contract.md`](docs/05-api-contract.md) §2, version 1.2. The frontend client written in M0 already assumed a cookie, so the two could not both be right
- [x] Passwords are SHA-256'd before bcrypt, so a passphrase over 72 bytes is neither truncated nor rejected

### Backend — media 🔗

- [x] `POST /media/uploads` — quota check, presigned PUT, 15-minute expiry, idempotency key honoured
- [x] Multipart for files over 100 MB *(server side; the browser still refuses over 100 MB — see "Deliberately not done" below)*
- [x] `POST /media/{id}/complete` — verify object exists and size matches, enqueue ingest
- [x] Ingest worker: `ffprobe` → duration, dimensions, fps, codecs — with display dimensions, so a portrait phone recording does not land on the timeline on its side
- [x] Ingest worker: 480p H.264 faststart **proxy** ⚠️ — and it never upscales: `scale=-2:'min(480,ih)'`
- [x] Ingest worker: thumbnail at ~10%, with a fallback to frame zero for files too short to seek
- [x] Ingest worker: waveform peaks JSON, 100 buckets/second, cross-checked against ffmpeg's own `volumedetect`
- [x] Asset only becomes `ready` when all four exist — and kind-aware, so an audio upload is not held forever waiting for a thumbnail it can never have
- [x] `GET /media/{id}` with signed URLs, 1-hour expiry
- [x] `GET /media` (cursor-paged), `DELETE /media/{id}` with `ASSET_IN_USE` guard
- [x] Reject unreadable media with a reason a person can read
- [x] **`docker-compose.yml` corrected**: it made `proxies/`, `thumbs/` and `peaks/` anonymously readable, against [`docs/03`](docs/03-backend-architecture.md) §6.3 — *"Everything is private."* Verified: anonymous GET now 403, signed GET 200

### Frontend

- [x] Register / login / logout, token refresh on 401 with one retry
- [x] Upload with real progress from the browser's own events (`XMLHttpRequest` — `fetch` has no upload progress event)
- [x] Media bin showing ingest status, thumbnails, durations; polls only while something is unfinished
- [x] Timeline shell: ruler, playhead, zoom, one video track
- [x] Waveform drawn to `<canvas>` from peaks — **never one DOM node per peak**. Measured in the browser: 3 DOM nodes for 600 peaks, 1679 lit pixels across 239 of 240 columns
- [x] Clip rendered on the track, scrubbable
- [x] Compositor from M1 lifted into `src/editor/playback/` and wired to the real proxy. The 45 M1 tests moved with it and still pass
- [x] **The timeline has no visual identity, deliberately.** No palette, typography or visual states have been delivered, so every colour goes through a token in `globals.css` and every token is a neutral grey. Applying the charter means editing that one block

### What the browser found that nothing else did

Three defects survived a green unit suite, a strict type-check and a clean lint. Each is recorded where it was fixed:

1. **Infinite render loop** — `selectClips` returned a fresh `[]` when there was no video track, so Zustand saw a new reference on every read. Correct in isolation; broken the moment React subscribes, which is the editor's first paint.
2. **The compositor could not draw a real proxy** ⚠️ — `texImage2D` threw `SecurityError: the video element contains cross-origin data`. The proxy comes from storage on another origin and `crossOrigin` was never set on the `<video>`. The element loads, plays and reports `readyState 4`; only the texture upload fails. **Preview against real ingest output is the whole point of M2's frontend, and it was completely broken while every other signal was green.**
3. **The ingest worker died on its second job** — the Celery task calls `asyncio.run()` per job while sharing the module-level pooled engine, so job two got a connection bound to job one's dead loop. A test that ingests one file never reaches the second.

### Deliberately not done in M2

- **No project persistence.** `POST/GET/PATCH /projects` stay in M3, which is titled *"Editing that survives a reload"*. M2's timeline lives in the browser and is gone on reload — the end-to-end run asserts that, so the boundary is checked rather than assumed.
- **Multi-part upload in the browser.** The server issues the per-part URLs; the client refuses over 100 MB with a clear message rather than failing silently at 101 MB.
  ⚠️ **The 17 August plan limits invalidated the assumption this rested on.** When it was written no tier had a stated per-file size, so a 100 MB client ceiling was a safe placeholder. Pro is now 1 GB and Studio 5 GB ([`docs/02-scope-v1.md`](docs/02-scope-v1.md) §3.2), which means **a paying user cannot upload a file their own plan permits.** The server side is already done — this is the browser half of the same feature, and it now has a deadline it did not have before. It is not M3 work by title, but it must not reach launch unlogged.
- **Storage quota values.** 🟠 The enforcement path is built and tested, but no document states the limit for any tier. The numbers in `app/services/plans.py` are marked `PLACEHOLDER` and must not ship. Same commercial question as the retention policy, and the same owner.
- **The peaks disagreement.** [`docs/03`](docs/03-backend-architecture.md) §6.2 says "min/max amplitude pairs"; the contract §3 says one value per bucket, and its own arithmetic only works that way. The contract is what both sides build against, so one value per bucket ships. §6.2's wording is the one to correct.

---

## M3 · Editing that survives a reload

*Ends when: you can cut, arrange, undo, close the tab, and come back to exactly what you left.*

> **Visual baseline settled 17 August 2026: A2 Studio** — [`docs/ui-directions/ui-directions-modern/`](docs/ui-directions/ui-directions-modern/index.html), written up as [`docs/08-ui-charter.md`](docs/08-ui-charter.md). Dark surfaces, neon yellow accents, rounded translucent chrome, and a deliberately technical grid timeline. The blocker recorded against M3 in `docs/README.md` is cleared.

> 🟢 **Backend projects shipped 18 August. The frontend half is unblocked.** `openapi.json` now carries the five project routes and the whole timeline document — `TimelineDocument`, `MediaTrack`, `TextTrack`, `MediaClip`, `TextClip`, `Transform`, `Crop`, `Transition`, `ColorGradeEffect` — and `make types` has regenerated `frontend/src/lib/api/generated.ts` from it. `tracks` comes out as `MediaTrack | TextTrack`, which a `switch (track.kind)` narrows, so the first task below is generation rather than authorship. **Do not hand-write the timeline type.**

### Frontend — visual charter 🟢

- [x] [`docs/08-ui-charter.md`](docs/08-ui-charter.md) written from A2 — palette, type scale, spacing, radii, the six component states, motion durations and curves. §13 is the block that replaces `@theme`
- [x] `frontend/src/styles/globals.css` `@theme` block replaced with the charter tokens — **no component file touched**. The property held: applying a whole visual identity was one file
- [ ] The four states the timeline needs proven against the charter: clip at rest, selected, dragging, muted track — none of them distinguished by hue alone. **Tokens for all four exist; the component renders rest and selected only.** Hover and dragging land with the drag handles
- [x] Blur and translucency confined to the chrome ⚠️ — nothing in the timeline carries a `backdrop-filter`, and the rule and its reason are written into `globals.css` where the next person to add one will read it

### Frontend — the document

- [x] Timeline document type generated from the OpenAPI schema — **same shape as the API**, no client variant. The hand-written M2 subset is gone and swapping it touched no caller, which is what it was written for
- [x] Zustand store: `timeline`, `selection`, `playhead`, `zoom`, `version`, `isDirty`
- [x] `commit(label, recipe)` using `produceWithPatches` — undo/redo from Immer patches, not hand-written inverses. A commit is one undo step however much it changed, which is what M4 needs for 1,800 caption clips
- [x] Undo/redo stacks capped at 200, keyboard bound. The keyboard map is a pure function in `editor/keyboard.ts`, so it is tested without a DOM
- [x] Memoised selectors for anything derived — nothing derived stored in the document

### Frontend — editing

- [x] Split at playhead, trim both ends, move, reorder, duplicate, delete — pure recipes in `editor/state/operations.ts`, each leaving §4.3 satisfied. **Speed is what makes split and trim non-obvious** and it has its own tests
- [x] **Drags use local state and commit once on drop** — never per pointer move. Proven by a test that fires eleven moves and asserts the history is unchanged until the drop
- [ ] Snapping to clip edges, playhead and zero, with a modifier to suppress — `snapTo` and `snapCandidates` are written and tested; **wiring them to the drag handles is outstanding**
- [ ] Selection: click, shift-click, marquee — click and shift-click done, **marquee outstanding**
- [x] Keyboard: space, `S`, arrow nudge by one frame, plus undo/redo, duplicate, delete, save and escape. Nothing fires while focus is in a text field
- [ ] Clip properties: volume, speed, rotate, flip, crop and reframe — the operations exist and clamp to the contract's ranges; **the inspector panel is outstanding**
- [ ] Audio track: music clip, per-clip volume, fades — Web Audio gain automation
- [ ] Text track: add a title, font, size, colour, position
- [ ] Transitions: cut, fade to black, cross dissolve — `setTransition` exists and clamps to invariant 7; **no interface for it yet**
- [ ] Timeline virtualised by time window ⚠️ — must stay smooth at 500 clips

### Backend — projects

- [x] `POST /projects`, `GET /projects`, `GET /projects/{id}` with assets and fresh signed URLs
- [x] `PATCH /projects/{id}` — timeline + version, `409` on stale version. **The check and the bump are one `UPDATE … WHERE version = :expected`** — reading, comparing in Python and then writing leaves a window where two tabs both read 12 and both write 13, which is the exact failure the 409 exists to make visible
- [x] **Timeline validation on every write** — all eight invariants from [`docs/05-api-contract.md`](docs/05-api-contract.md) §4.3, rejecting with the offending clip named. Structure first and with no query at all, then one batched asset lookup for invariants 4 and 5
- [x] `project_assets` rebuilt from the document on each save — diffed rather than deleted-and-reinserted, because autosave runs every two seconds
- [x] `duration_ms` derived on save, never sent by the client
- [x] Duplicate, soft delete. `project_assets` survives a soft delete, so a restore inside the retention window still finds its footage

### Frontend — persistence

- [x] Autosave debounced 2 s, plus on blur and `visibilitychange`
- [x] One save in flight at a time; never mid-drag. Both are tested on fake timers — neither would show up in a click-through
- [x] `409` → a bar with "Keep mine" and "Load the other version", and autosave goes quiet until one is chosen. **No automatic merge**
- [ ] IndexedDB mirror on every commit, restore offer on open 💤
- [x] Last-chance flush on `pagehide` — **`fetch` with `keepalive`, not `sendBeacon`**, which only issues POST and cannot send a PATCH. Correction recorded in [`docs/04-frontend-architecture.md`](docs/04-frontend-architecture.md) §6, including the 64 KB body cap that makes it best-effort

> **Where M3's frontend stands, 18 August.** The document, the history, the editing operations
> and persistence are done and covered by 155 frontend tests. What is left is the **interface**
> over them: drag handles and snapping, the marquee, the inspector panel, transitions, the audio
> and text tracks, and virtualisation. The core was built first on purpose — every one of those
> is a component over an operation that already exists and is already tested, rather than a
> component that has to invent the operation as it goes.

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

- [ ] Mock server (Prism or MSW) serving fixtures from the same schema *(moved from M0 — this is the first milestone that consumes a fixture)*
- [ ] Fixtures: a 2,000-word caption result **with a deliberately misspelled name**, a smart-trim result, a failing job, an account with credits split across two buckets, a free account hitting `PLAN_LIMIT_EXCEEDED`
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

## M7 · Cybersecurity ⚠️

*Ends when: no critical or high finding is open, every fix has a regression test, and the scanners run on every pull request.*

**The last milestone before launch, and the only one whose job is to break what the previous seven built.** Full plan — scope, rules of engagement, threat model, severity scale — in [`docs/07-security.md`](docs/07-security.md). This is the checklist; that document says why each line is here.

**Runs on staging, deployed from the release commit, with synthetic data only.** Never production, never real footage, never real cards. Stripe and Razorpay are not targets — their sandboxes are.

### Rules of engagement 🔗

- [ ] Staging stack deployed from the release commit, seeded with synthetic accounts and generated media
- [ ] Scope and dates agreed in writing with the project lead, including who can call a stop
- [ ] AWS testing policy re-read **the week the test runs** — it changes, and the DoS carve-out is the part that bites
- [ ] Private findings register created at `security/findings.md` — never a public issue

### Part A — code review

- [ ] **Every route**: authenticated, ownership enforced at the repository, no cross-user identifier accepted
- [ ] **The `ScopedRepository` claim re-proved** for every repository added in M3–M6, not just media
- [ ] Auth: rotation, reuse-revokes-chain, logout revoking, equal-time login, bcrypt cost, the SHA-256 pre-hash
- [ ] **The timeline validator read as a security control** — it parses attacker-controlled JSON that M5 turns into a filter graph. All eight §4.3 invariants, plus bounds on every numeric field
- [ ] No f-string SQL, no `text()` with interpolation, no `dict[str, Any]` reaching a query
- [ ] `gitleaks` over the **full history**; `assert_production_safe()` verified to actually refuse dev defaults
- [ ] Logs carry no password, token or **presigned URL** — a signed URL in a log is a credential with an hour to live
- [ ] Frontend: httpOnly + `Secure` + `SameSite` on the refresh cookie, a decided CSRF story, no `dangerouslySetInnerHTML` near user or model text
- [ ] Frontend: CSP, `frame-ancestors`, `Referrer-Policy`; bucket CORS an origin list and **not** `*`; user media not served from the app origin
- [ ] Infra: Redis authenticated and private, containers non-root with no Docker socket, **IMDSv2 required with hop limit 1**, OIDC instead of long-lived AWS keys in CI
- [ ] **Branch protection on `main`** — moved here from M0 on 17 August, where it protected a branch nobody pushes to. Owned here because it is a supply-chain control
- [ ] Dependencies: lockfiles installed from in CI, `pip-audit` / `pnpm audit` clean or every exception dated, **FFmpeg pinned and current**, Actions pinned by SHA

### Part B — penetration test

- [ ] **Cross-account isolation, every resource type** — project, asset, job, ledger, subscription, payment, socket. The most damaging finding available in this product, so it gets the most time
- [ ] Auth: rotated-token reuse, post-logout refresh, `alg: none`, key-confusion, user enumeration by timing
- [ ] Storage: anonymous GET on all four derivative kinds, expired PUT, PUT for another key, **5 GB through a URL issued for 5 MB**, content-type mismatch, key traversal via filename
- [ ] ⚠️ **Ingest worker — the sharp boundary.** HLS / `concat` / external-reference containers pointed at `169.254.169.254` and `file:///`, decode bombs, 50 at once from one free account, traversal filenames, a script named `.mp4`, temp-file cleanup after repeated kills, egress from inside the container
- [ ] ⚠️ **Fuzz `ffprobe`** on mutated MP4/MOV/MKV/WAV headers — timeboxed, crash triage only. The output is a decision about upgrading or sandboxing, not an exploit
- [ ] ⚠️ **Export filter graph**: caption text containing `:` `\` `'` `%` and newlines, a font path from user input, speed `0`, negative crop, NaN. Escaping must live in one builder, never in string concatenation
- [ ] ⚠️ **Credits** — 20 concurrent jobs against a balance of 1; the same with one idempotency key; cancel at the instant of success; the period-rollover refund; a client-sent price. **The ledger must never go negative**, and reserved = settled + refunded per bucket across the whole run
- [ ] Webhooks: unsigned, tampered, replayed, another user's subscription, out of order, oversized, a plan that does not exist
- [ ] Jobs and WebSocket: subscribe to someone else's job, connect with an expiring token, cancel a job that is not yours
- [ ] Browser: stored and reflected XSS in names, filenames and caption text; CSRF on `/auth/refresh`; open redirect on the checkout return; **and, with script running on our origin, can the refresh token be extracted?** If yes, contract 1.2's justification is wrong
- [ ] Rate limits: `X-Forwarded-For` spoofing behind the ALB, account rotation, the socket, the presigned PUT. Then **measure the cost of an abusive free account** — that number decides whether email verification ships at launch

### Automated gates — these stay after M7

- [ ] `semgrep` (python · fastapi · react · owasp) on every pull request
- [ ] `bandit` / ruff `S` rules on every pull request
- [ ] `pip-audit` · `pnpm audit` · `osv-scanner` daily
- [ ] `gitleaks` on every pull request
- [ ] `trivy` on every image build
- [ ] `make security` running the lot locally, and a ZAP baseline before each release

### Fix, retest, hand over

- [ ] Every critical and high fixed — **launch is blocked while one is open**
- [ ] Every fix carries a test that fails without it
- [ ] Retest from the register; the finding closes on evidence, not on a commit message
- [ ] Any accepted risk written down, time-boxed and signed by the project lead
- [ ] `docs/08-m7-notes.md` written, in the shape of [`docs/06-m2-notes.md`](docs/06-m2-notes.md)
- [ ] `security.txt` published with a disclosure address, and a named person who answers it

> **Not in M7, on purpose:** facial data and consent (phase 2 — none of it exists yet), GDPR/DPA paperwork, any certification, a bug bounty, and DDoS resilience beyond rate limits. An external penetration test is **recommended once there is revenue** and is the only real correction for M7 being a review of one's own code. Reasoning in [`docs/07-security.md`](docs/07-security.md) §11.

---

## Before calling it done

Walk [`docs/02-scope-v1.md`](docs/02-scope-v1.md) §6 end to end, as someone who has never seen the product. All fourteen steps, no explanations allowed.

- [ ] Safari, Chrome, Firefox
- [ ] A 60-minute upload
- [ ] A project with 500 clips
- [ ] Slow network, dropped socket, closed tab mid-job
- [ ] An account that runs out of credits halfway through
- [ ] **No critical or high finding open in `security/findings.md`** — M7's gate, and the one that cannot be waived quietly

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
6. **M7 runs last and cannot move**, because it reviews the whole system and the whole system only exists at the end

Start the Stripe and Razorpay applications during M0 — they take calendar time nobody here controls, and M6 stalls without them.

M7 runs last, but the scanners in its "automated gates" block are cheap to switch on during M0 and expensive to switch on at the end, when the first run returns two hundred findings across six milestones of code. Same for [`docs/07-security.md`](docs/07-security.md) §2 and §8 — the rules and the severity gate are worth agreeing early, because agreeing them after a finding appears is a negotiation.

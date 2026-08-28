# Phase 1 — Working checklist

**Temporary working file.** Not part of the documentation set in [`docs/`](docs/) — this is the day-to-day task list, meant to be edited constantly and deleted when phase 1 ships.

Scope comes from [`docs/02-scope-v1.md`](docs/02-scope-v1.md), **plus [`docs/13-mvp-direction.md`](docs/13-mvp-direction.md) since 25 August**. Where a task is ambiguous, those two win.

> **The Discord launch, added 25 August — nothing is cut.** *MVP* means phase 1 as listed here, so export and M7 are untouched. What is added lands almost entirely in **M6**: a fifth **`beta` plan at $3.99**, temporary; a **promo-code and commission** block that is new work with no schema behind it; and **templates**, which is small and belongs to the editor rather than the tools. Razorpay ships first, Stripe is deferred. Every scope question was decided the same day — [`docs/13-mvp-direction.md`](docs/13-mvp-direction.md).

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
- [x] Multipart for files over 100 MB — **both halves, since 28 August.** The server side shipped with M2 and its completion step was broken the whole time: it passed the request's `etag` where S3 wanted the upload id, so every multipart completion failed with `NoSuchUpload`. Nothing caught it because no test went past the reservation. Fixed by keeping the upload id on the asset row (migration `0004`), and the browser now transfers the parts instead of refusing the file. [`docs/19-multipart-and-ci.md`](docs/19-multipart-and-ci.md)
- [x] `POST /media/{id}/complete` — verify object exists and size matches, enqueue ingest
- [x] Ingest worker: `ffprobe` → duration, dimensions, fps, codecs — with display dimensions, so a portrait phone recording does not land on the timeline on its side
- [x] Ingest worker: 480p H.264 faststart **proxy** ⚠️ — and it never upscales: `scale=-2:'min(480,ih)'`
- [x] Ingest worker: thumbnail at ~10%, with a fallback to frame zero for files too short to seek
- [x] Ingest worker: waveform peaks JSON, 100 buckets/second, cross-checked against ffmpeg's own `volumedetect`
- [x] Asset only becomes `ready` when all four exist — and kind-aware, so an audio upload is not held forever waiting for a thumbnail it can never have
- [x] `GET /media/{id}` with signed URLs, 1-hour expiry
- [x] `GET /media` (cursor-paged), `DELETE /media/{id}` with `ASSET_IN_USE` guard
- [x] Reject unreadable media with a reason a person can read
- [x] 🔴 **Fixed 26 August — the ingest worker's retry was decorative.** `process_asset` declared `max_retries=2` but `run_ingest` caught every exception, including infrastructure blips, and wrote `failed` immediately — no code path ever called `self.retry()`. Now mirrors `analysis.py`'s `TransientFailureError` pattern exactly: bad media stays a permanent, immediate `failed`; an S3 or database blip is retried 3× with backoff before giving up. See [`docs/16-pipeline-reliability-notes.md`](docs/16-pipeline-reliability-notes.md)
- [x] 🔴 **Fixed 26 August — `POST /media/{id}/complete` enqueued before its own commit.** A worker could claim the asset on another connection and read it still `pending_upload`, the exact race `POST /jobs`'s own docstring warns against. Commit now happens before `process_asset.delay()`, matching the pattern jobs already used
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
- ~~**Multi-part upload in the browser.**~~ ✅ **Done 28 August.** The warning below stood for eleven days and was right: the 17 August plan limits made a 100 MB client ceiling mean *a paying user cannot upload a file their own plan permits*, with Pro at 1 GB and Studio at 5 GB. The browser now uploads the parts, retries a failed one rather than the whole file, and asks for fresh part URLs when the fifteen-minute signatures expire mid-transfer. The server half turned out to have been broken since M2 as well — [`docs/19-multipart-and-ci.md`](docs/19-multipart-and-ci.md).
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
- [x] The four states the timeline needs proven against the charter: clip at rest, hover, selected, dragging, plus the muted lane — none distinguished by hue alone. Selected also changes weight, dragging also lifts and rings, a muted lane also shows an `M` in its header
- [x] Blur and translucency confined to the chrome ⚠️ — nothing in the timeline carries a `backdrop-filter`, and the rule and its reason are written into `globals.css` where the next person to add one will read it

### Frontend — the document

- [x] Timeline document type generated from the OpenAPI schema — **same shape as the API**, no client variant. The hand-written M2 subset is gone and swapping it touched no caller, which is what it was written for
- [x] Zustand store: `timeline`, `selection`, `playhead`, `zoom`, `version`, `isDirty`
- [x] `commit(label, recipe)` using `produceWithPatches` — undo/redo from Immer patches, not hand-written inverses. A commit is one undo step however much it changed, which is what M4 needs for 1,800 caption clips
- [x] Undo/redo stacks capped at 200, keyboard bound. The keyboard map is a pure function in `editor/keyboard.ts`, so it is tested without a DOM
- [x] Memoised selectors for anything derived — nothing derived stored in the document. **Memoised on the document's identity, not merely written as functions**: a selector that builds a fresh array each call is an infinite render loop, which this project has now shipped twice. `selector stability` in `store.test.ts` is the test that fails instead of the browser

### Frontend — editing

- [x] Split at playhead, trim both ends, move, reorder, duplicate, delete — pure recipes in `editor/state/operations.ts`, each leaving §4.3 satisfied. **Speed is what makes split and trim non-obvious** and it has its own tests
- [x] **Drags use local state and commit once on drop** — never per pointer move. Proven by a test that fires eleven moves and asserts the history is unchanged until the drop
- [x] Snapping to clip edges, playhead and zero, `alt` to suppress. **The tolerance is in pixels, not milliseconds** — 100 ms is a fifth of a pixel when zoomed out and half the screen when zoomed in, and what the hand judges is distance on screen
- [x] Selection: click, shift-click, marquee. The marquee catches what it *touches* rather than what it encloses — a lasso that only took fully-enclosed clips would miss the long one you dragged across, which is usually the one you meant
- [x] Keyboard: space, `S`, arrow nudge by one frame, plus undo/redo, duplicate, delete, save and escape. Nothing fires while focus is in a text field
- [x] Clip properties: volume, speed, fades, rotate, flip, and reframe — in `editor/inspector/`. ⚠️ **Reframe is two presets (full frame / centre 9:16), not a drag-to-crop.** The operation takes any normalised rectangle; only the handle to draw one is missing
- [x] Audio track: music clip, per-clip volume, fades. An audio-only asset routes to the music lane from the media bin — sending it to the video track would put a soundtrack on the picture track. ⚠️ **Web Audio gain automation is not wired**: the values are in the document and the renderer will honour them, the browser preview does not yet
- [ ] Text track — **add, edit and position a title are done**; font, size and colour are not. The `style` override object is in the document and generated into the client types, so each is a control rather than a change of shape
- [x] Transitions: cut, fade to black, cross dissolve, per side, clamped to invariant 7 — and **re-clamped after any edit that changes a duration or a neighbour**, because the bound is a property of two clips and trimming one of them breaks it without touching the transition. A cut is stored as *no* transition rather than a zero-length one, so the renderer never has to ask what a zero-length dissolve means. ⚠️ **The preview draws every join as a cut**: the engine derives a crossfade from overlapping clips and the document forbids overlap, so rendering one needs a contract decision about which side gives up the frames — see [`docs/09-m3-notes.md`](docs/09-m3-notes.md) §5
- [x] Timeline virtualised by time window ⚠️ — clips outside the window plus half a screen of overscan are not rendered at all, and the grid is a repeating background rather than one element per line. Tested at 500 clips

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

> **M3 is complete bar three things, 18 August — audited and repaired 20 August.** The
> document, the history, the editing operations, persistence and the whole interface over
> them are done, covered by 201 frontend tests and exercised end to end: register, create a
> project, edit, reload, and the edit is still there.
>
> Left open: **the text track's font, size and colour controls** (the style object is in the
> document, so each is a control rather than a change of shape); the **IndexedDB mirror**,
> which the checklist already marked 💤 and which is also the proper answer to the 64 KB cap
> on the unload flush; and **transitions in the preview**, which is not an omission anyone
> chose — it needs a contract decision first.
>
> The audit that followed found nine defects, two of which silently lost the user's work: a
> trim past the end of its media, and a transition left over its bound by a later trim. Both
> were rejected by the server on the next autosave, with the editor then stuck on "Could not
> save". All nine are fixed and written up in [`docs/09-m3-notes.md`](docs/09-m3-notes.md)
> §6, with a test each that fails on the old code.

---

## M4 · The first AI tool works

*Ends when: you run captions on a real clip, watch progress, see the words appear in time, and fix a misspelled name.*

> **Read [`docs/10-m4-readiness.md`](docs/10-m4-readiness.md) first.** Written 19 August
> from a full read of the schema, the workers and the contract — what M4 inherits already
> built (the tables, the enums, the queues, the concurrency limits, the seeded plan
> catalogue), what the contract already specifies in full so it does not need re-deciding
> (every per-tool payload, the large-result split, the WebSocket's fallback contract), and
> **two real open decisions found while preparing this** — which transcription engine
> powers Captions, and the still-blocked language list — with a recommendation for the
> first rather than a decision made without you.

### Backend — job pipeline 🔗

- [x] `POST /jobs` — validate, resolve assets, price, reserve credits, insert, **all in one transaction**. ⚠️ The enqueue is deliberately **after** the commit: sending the task inside the transaction hands a worker an id no other connection can see yet, and the job fails before the user is told it exists
- [x] `allocate()` — plan credits first, then top-up. Two ledger rows when it spans both. Both of the documented examples are tests
- [x] Idempotency key handling, replay returns the original job and charges once
- [x] Per-plan concurrency caps — applied **at the claim**, not at creation: the contract promises the request still succeeds and the job waits for a slot
- [x] Priority bands, queues split per family. Redis has no native priority, so Celery's `priority_steps` are set to the four plans' `queue_priority` values and `apply_async(priority=…)` needs no translation
- [x] Worker claim: `UPDATE ... WHERE status='queued'`, stop if zero rows
- [x] Progress published to Redis at real checkpoints, not on a timer
- [x] Retry transient failures 3× with backoff; permanent failures go straight to `failed`. Bad media is **not** transient — the same file gives the same answer three times. ⚠️ **This was only ever true for analysis.** The identical claim in M2 for ingest was decorative until 26 August — see below
- [x] Refund on failure, **to the buckets it took from**, read back from the reservation rows
- [x] Period-rollover edge case: refund to `topup` instead — with a tolerance, because `jobs.created_at` is the database's clock and `current_period_start` is written by whatever granted it
- [x] `GET /jobs/{id}`, `GET /jobs`, `POST /jobs/{id}/cancel`, `POST /jobs/estimate`
- [x] Results over 256 KB go to S3, `result_key` instead of `result`
- [x] WebSocket `/ws` + Redis pub/sub fan-out. Each socket takes its **own** Redis connection — a `SUBSCRIBE` held for the life of a browser tab would otherwise starve the shared pool that every rate-limit and idempotency call uses
- [x] Nightly ledger reconciliation, alerting on drift. **Reports; does not repair** — silently correcting a balance would hide the bug that caused the drift and destroy the evidence of how much was lost and to whom

### Backend — the three tools

- [x] **Captions** — transcribe, word-level timings, emphasis detection. Engine decided 21 August: **self-hosted `faster-whisper`**, behind one function so a swap is a function body. Emphasis is measured from per-word loudness against the speaker's own baseline, not an absolute level. **Languages confirmed 21 August: English, French and Hindi** — a language accepted is a language we are claiming works, which is why the list is three and not thirty
- [x] **Smart trim** — silence, filler, stutter, repeat detection. Three strengths. Ranges in **asset time**. A transcript that fails does not fail the job: silence detection alone is a useful answer. A filler list per accepted language, Hindi in **both scripts** because real Indian speech is often Hinglish and comes back in Latin
- [x] **Colour analysis** — returns LUT name + strength + alternatives. Sampled frames through `signalstats`, no external dependency, which is why the readiness doc put it first
- [x] Ship 5 LUTs, with the `.cube` files **shared with the frontend** — generated by `make luts`, and a test asserts the server's catalogue and the browser's agree, because a look the server recommends and the browser cannot draw reads as a broken tool rather than a missing file. ⚠️ **One caption style, not three**: `caption_bold`. The other two are design work, not engineering

### Frontend — tools

- [x] ~~Mock server (Prism or MSW)~~ → **`src/lib/api/fixtures.ts`**, answering inside `request()` behind `NEXT_PUBLIC_DEMO=1`. Neither Prism nor MSW: both are a dependency and a second process, and what was needed was for one function to answer from a table. **Typed against `generated.ts`**, so a fixture that drifts from the contract fails `pnpm typecheck` — which it did, four times, while being written
- [~] Fixtures: **a caption result with a deliberately misspelled name** ✅ ("Sara" for Sarah), a **graded clip**, an **asset still ingesting**, and **credits split across two buckets** ✅. Still missing: a smart-trim result, a failing job, and a free account hitting `PLAN_LIMIT_EXCEEDED`
- [x] Estimate on panel open, price on the button — including `blockedBy`, so "Not enough credits" is on the button rather than behind a failed click
- [x] Invoke with idempotency key, editing continues while it runs
- [x] WebSocket progress, polling fallback every 3 s. **The socket only ever makes the poll happen sooner** — every event is a hint to re-read the job, never the job's new state, so a missed event costs latency and nothing else
- [x] Re-sync via `GET /jobs?status=running` on reconnect
- [x] **Captions → one text clip per word, in a single `commit`** — one undo step for 1,800 clips. Re-running replaces the previous run rather than doubling it, and a hand-typed title is never touched
- [x] Smart trim → splits and removals, one `commit`, with the ripple that closes the gaps behind them
- [x] Colour grade → one `effects` entry, replacing any grade already there
- [x] ⚠️ **Asset time → timeline time conversion**, unit-tested on a clip that is both trimmed *and* sped up — the only clip where the two clocks differ enough for a mistake to show
- [x] Caption editing: correct a word, retime, move, split, duplicate and delete. ⚠️ Restyling is the missing caption styles, above. 🔴 **Moving and retiming a text clip did nothing at all until 22 August** — every one of those operations went through a lookup that searched video and audio only, so the gesture ran and the document was never touched. Nine tests now cover the text path that had none
- [x] Low-confidence words flagged visually. **Session state, not document state**: the contract's `TextClip` has no confidence field, because confidence describes how a word was produced rather than what it is
- [ ] Failure states mapped from `errorCode`, with retry, saying the credits came back

---

## M4.5 · The interface pass ✅

*Ends when: the editor can be reached, driven and understood by someone who did not build it.*

**Done 25 August.** Seven items from [`docs/12-m4-5-interface-pass.md`](docs/12-m4-5-interface-pass.md),
all of them found by *using* the editor rather than testing it. Written up in
[`docs/14-m4-5-notes.md`](docs/14-m4-5-notes.md), including the two defects this
pass introduced and the one it found in passing.

- [x] **1 · `/projects` was a dead end** 🔴 — a stub reading *"Built in M3."*, and the only link into the product. It is now the real list: open, create, duplicate, delete-with-confirm, relative times, empty state, and the same account gate the editor uses. The home page also links straight to `/editor/scratch`, which was previously reachable only by knowing the URL
- [x] **2 · The transport moved** out of the application header to under the picture, between the frame it plays and the playhead it moves. Frame-step, jump-to-start and jump-to-end existed as shortcuts with **no visible control**; each has a button now, each naming its shortcut
- [x] **3 · Manual colour and audio control** — a picker over the five shipped `.cube` looks plus a strength field, so warming an image no longer means spending credits on an analysis job and accepting what it recommends. Volume, speed and the fades moved out of the inspector into the Audio mode. ⚠️ **No new effect type**, per the 22 August decision: anything the export renderer does not already implement would make the preview and the file disagree
- [x] **4 · The left panel became a mode rail** — Media · Titles · Audio · Colour · Captions · Smart trim, one active at a time. The right panel went back to being the inspector and nothing else; the stacked tools panel is gone. **The rail grows by one icon per tool** — phase 2's four are one entry each in `rail/modes.ts`
- [x] **5 · Compact numeric fields** replacing full-width range inputs — a row is 34 px instead of 56, every value is typeable, and *"Fade in"* finally says it is the audio ramp and not a video transition
- [x] **6 · The precise zoom is discoverable** — `ctrl + scroll to zoom here` sits beside the slider. Behaviour unchanged per the 22 August decision: plain wheel still scrolls
- [x] **7 · The timeline is part of the page** — its own surface and rule, and **a draggable divider** with a keyboard path, an `Escape` cancel, a double-click reset and a remembered height. Bounded so it can never squeeze the picture off screen
- [x] Gates: **301 tests** (up from 236) · `tsc --noEmit` clean · `eslint` clean · production build green
- [x] Driven in a real browser on Windows through the fixture server — every mode, the grade, the typed field, undo/redo
- [x] **The vivid frame, 28 August** — a neon cyan → magenta → green ring with a bloom around the picture and around the timeline, from a mockup by the project lead. One class in `globals.css`, three tokens, no colour in any component. ⚠️ **It contradicts charter §3.3** — *"one accent, one hex… no secondary brand colour and no gradient"* — so the charter is amended rather than quietly broken: the vivid colours are decoration only and may never carry state, yellow stays the sole meaningful accent ([charter §3.3, v1.1](docs/08-ui-charter.md)). Checked against the mockup in a real browser through the fixture server

### Found by looking, not by testing

- [x] 🔴 **Typing `42` into a field showing `66 %` set the strength to 100 %.** The field's displayed unit and its stored unit had diverged, so the parse clamped to the maximum and wrote a value nobody asked for — silently, from the control added *specifically* so values could be set exactly. Fixed with an explicit scale, and `toDisplay`/`toDocument` are pure and tested, including an exact round trip at all 101 steps
- [x] **The toolbar still read *"AI tools are in the panel on the right"*** after the tools moved to the left rail. A label pointing at a panel that no longer exists, which only opening the editor finds

---

## Pipeline reliability — fixed 26 August ✅

*An outside audit's headline finding, verbatim: "Make the upload → processing →
job pipeline reliable and self-recovering... database state, file storage, and
background workers can get out of sync."*

Read [`docs/16-pipeline-reliability-notes.md`](docs/16-pipeline-reliability-notes.md)
for the full account — what was verified against the running code before
anything was changed, what each fix does, and what is deliberately still open.

- [x] 🔴 **Ingest's retry was decorative** — `run_ingest` caught every exception, including infrastructure blips, and wrote `failed` immediately. `max_retries=2` on the Celery task was never once exercised. Now raises `TransientFailureError` for anything that is not bad media, and `process_asset` retries it 3× with backoff before giving up — the exact pattern `analysis.py` already had
- [x] 🔴 **The upload-complete endpoint enqueued before its own commit** — the same race `POST /jobs`'s own docstring warns against, just never fixed here. `session.commit()` now happens before `process_asset.delay()`
- [x] **`POST /jobs`'s enqueue now logs loudly on failure** rather than only raising — the job and its credit reservation are already committed durable at that point; the log line is what tells anyone the send itself failed, distinct from every other exception trace
- [x] **A pipeline sweep, every 5 minutes** (`app/services/pipeline_reconciliation.py`, its own `reconciliation` queue) — re-sends the Celery message for a job stuck `queued` with no `started_at`, requeues-then-resends a job stuck `running` past a generous ceiling, and fails an upload reservation nobody ever completed. All three re-sends are safe because `claim()`'s `WHERE status='queued'` makes acting on a job that was never actually stuck a harmless no-op
- [x] Thirteen tests — twelve in `tests/test_pipeline_reconciliation.py`, every "acted on" case paired with a "left alone" case at a fresher timestamp since a threshold that is too eager is the actual risk here, plus one in `tests/test_ingest.py` proving a storage blip raises `TransientFailureError` rather than writing `failed`. ✅ **Run, 27 August** — all thirteen pass. See [`docs/17-first-real-test-run.md`](docs/17-first-real-test-run.md) for what the run found, including one of the thirteen that was wrong
- [x] 🔴 **`make watch` never consumed the `reconciliation` queue** — every other worker invocation gained it in this pass (Makefile ×3, `docker-compose.yml`), but `scripts/dev-up.sh`, which is what the README tells you to run, did not. Beat enqueued `sweep_pipeline` every five minutes into a queue with no consumer: the recovery job this whole pass exists to add was silently dead in the main dev flow, which is the same "message nobody is listening for" bug it was written to catch. Found by running the toolchain, not by reading it
- [x] 🔴 **Two defects in this pass's own new code, caught by re-reading it** — `sweep_stuck_running_jobs` sent its Celery messages *before* committing the requeue, the exact bug being fixed two files away; and `sweep()` claimed each check was isolated while running them unguarded in sequence, so one failure would have skipped the rest. Both fixed, both now covered by tests
- [x] ✅ **`media_assets` has the atomic claim now** — the gap above, closed 27 August before M5 rather than after it, because a stuck render costs far more to duplicate than a stuck 480p proxy. Migration `0003_media_asset_claim` adds `worker_id`, `ingest_started_at` and `ingest_attempts`; `run_ingest` claims through `media.claim_for_ingest` and releases on a transient failure; the sweep's report is replaced by two checks mirroring the two job checks — a message that never arrived, and a worker that died holding the claim — bounded by `MAX_INGEST_ATTEMPTS`. **The guard could not be `WHERE status='probing'` as this item assumed**: an asset is already `probing` when its message is sent, so the status matches for every worker. `worker_id IS NULL` carries the claim instead. Full reasoning in [`docs/18-media-asset-claim.md`](docs/18-media-asset-claim.md); 9 tests added, 240 green
- [ ] 🟠 **The `probing` thresholds are guesses until something real runs through them.** 20 minutes for a dead worker and 10 for a message that never arrived are argued from phase-1 tool timeouts, not measured. Revisit with M5, where a render legitimately runs longer than anything ingest does
- [ ] 🟠 Worth deciding once traffic exists: should `pipeline_sweep_ran` page someone, the way ledger drift does, or is a log line enough at phase-1 scale?

---

## M5 · A file comes out

*Ends when: you export a 1080p 9:16 MP4 and it looks exactly like the preview.*

> **Read [`docs/15-m5-readiness.md`](docs/15-m5-readiness.md) first.** Written 25
> August from the code: what export inherits already working, what the contract
> already settles, and 🔴 **the problem to solve in the first hour** — the
> renderer has no way to read a `.cube` file. They live in
> `frontend/public/luts/`, the backend has no path to one, and the container is
> built from the `backend/` context so they are not in the image. `lut3d=file=…`
> has nothing to point at, and it fails at the first graded export rather than at
> build time. Recommendation and two alternatives are in §3.

- [ ] 🔴 **Give the renderer the LUTs, and a backend test that every look in `color_analysis.LOOKS` has a readable `.cube`** — the current catalogue test compares the client's list against a hand-copied constant and never reads the server ([readiness §3](docs/15-m5-readiness.md))
- [ ] Add `export` to `PHASE_1_TOOLS` and write `ExportInput` — `POST /jobs {"tool":"export"}` is rejected today by design
- [ ] `storage.export_key()` — the `exports/` prefix is in the architecture doc and nowhere in the code
- [ ] Decide the frame-comparison tolerance **before** writing the comparison ([readiness §4.1](docs/15-m5-readiness.md)) — the preview composites a 480p proxy and the export uses the original, so an exact match is the wrong target
- [ ] Decide transitions: the renderer will draw a dissolve the preview never shows ([readiness §4.2](docs/15-m5-readiness.md)). Either the preview learns them, or the editor says so. **Silently differing is the wrong option**
- [x] ⚠️ **Infrastructure first.** Nothing in M5 can be verified without Docker or a native Postgres + Redis + MinIO: export is ffmpeg, object storage and a worker, and there is no fixture-server version of it. **Done 27 August** — Docker Desktop 29.7, ffmpeg 9.0.1, Postgres + Redis + MinIO up. `make migrate` and `make test-backend` green (231 passed, 2 skipped); `make e2e` is **still not run**, see [`docs/17-first-real-test-run.md`](docs/17-first-real-test-run.md) §5

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

> **Grew on 25 August — read [`docs/13-mvp-direction.md`](docs/13-mvp-direction.md).**
> Everything below still ships. **Razorpay first** removes the second adapter for
> now; the **fifth `beta` plan** and the **promo-code and commission** block at
> the end of this section are added. Net: bigger than it was.

- [~] ~~Stripe and~~ **Razorpay account opened** 🔗 — **test key pair received 25 August**, in the developer's `.env` and nowhere else. Two things still outstanding:
  - [ ] **The webhook secret**, which is a *third* secret and does not exist until a webhook endpoint is created in the dashboard. Until then the signature check cannot be exercised, and an untested signature check is indistinguishable from none. ✅ The application now **refuses to boot in production without it**
  - [ ] ⚠️ **Confirm the account may charge USD.** $3.99 is a dollar price on an Indian processor, and currency availability is an account-activation matter rather than an API capability. ✅ `make razorpay-check ARGS=--currency` probes it — **a refusal in test mode is conclusive and arrives now**; an acceptance leaves it open until the account goes live
- [x] **Production cannot boot with test keys** — `assert_production_safe()` refuses `rzp_test_…` in production, a key with no secret, and a key with no webhook secret. Only checked when a key is present, so today's empty configuration still starts. Eight cases in `tests/test_config.py`
- [ ] Stripe is deferred, so its application can wait
- [ ] `billing/providers/` — ~~one adapter per provider~~ **the Razorpay adapter first**, against the interface that was designed for two. Stripe stays addable without reshaping anything ([`docs/03-backend-architecture.md`](docs/03-backend-architecture.md) §8.1)
- [ ] `GET /plans` — public, currency suggested by IP, overridable. ⚠️ **Must filter on `plans.is_public`** — the column exists in migration `0002` and **nothing reads it today**. It is the whole mechanism for retiring `beta` later without touching anyone already on it, and an endpoint that ignores it makes the retirement a no-op
- [ ] `POST /billing/checkout` → hosted checkout URL
- [ ] `POST /billing/topup`, `/portal`, `/cancel` with the "you will lose X credits" response
- [ ] Webhooks: **verify signature → store in `provider_events` → 200 immediately → process async**
- [ ] Duplicate events collide on the primary key and are dropped
- [ ] Renewal: sweep `plan` + `facemap`, grant new allowance, **never touch `topup`**, one transaction
- [ ] Celery beat hourly sweep as the safety net — and the only path for free users
- [ ] Upgrade immediate + pro rata; downgrade at period end — **five plans now, so `beta` → `pro` is the upgrade path that will actually get used**
- [ ] `GET /credits/ledger` with buckets
- [ ] Cost-per-job metric instrumented ⚠️ — **more urgent at $3.99 than it was at $19.99.** The allowance was derived from a price five times higher, and `SECONDS_PER_MINUTE_OF_MEDIA` in `pricing.py` is a heuristic, not a measurement ([`docs/11-m4-notes.md`](docs/11-m4-notes.md) §8). Net of the 15% commission and processing, one subscription clears about **$3.28** to cover a month of transcription, trimming, storage and export
- [ ] Frontend: pricing page, checkout redirect, **confirming state on return** (never trust the redirect), 30 s polling fallback
- [ ] Frontend: balances — one number everywhere except billing, cancellation and face mapping
- [ ] Frontend: paywalls that name the unblock and link to it. **No dead ends**
- [ ] Frontend: running out mid-project never blocks plain editing and never loses work

### The `beta` plan — new on 25 August 🔗

$3.99 / ₹199, added beside the four tiers and retired once the Discord campaign
ends. Values and the reasoning for each are in
[`docs/13-mvp-direction.md`](docs/13-mvp-direction.md) §3.

- [ ] Migration: `ALTER TYPE plan_code ADD VALUE 'beta'` and seed the row — **800 credits · 1080p · watermark `none` · `queue_priority` 0 · 399 cents / 19900 paise · `facemap_seconds` 0 · `fair_use_credits` NULL**
- [ ] ⚠️ **`queue_priority` is 0, not 5.** Celery's `priority_steps` are `[0, 10, 20, 30]` and `apply_async(priority=…)` passes the plan's value through untranslated. A value between bands is silently mapped to a neighbour
- [ ] 🔴 **Add `beta` to both dictionaries in [`app/services/plans.py`](backend/app/services/plans.py)** — `CONCURRENCY_LIMITS` and `STORAGE_QUOTA_BYTES` are read with a direct subscript, so a plan missing from either raises `KeyError` on the claim path and the upload path. Use `{"analysis": 2, "render": 1, "inference": 0}` and 25 GB, and **keep the `PLACEHOLDER` marker** — the storage question is still unanswered for every tier
- [ ] `test_the_four_plans_are_seeded_with_the_documented_values` in `test_schema.py` asserts an exact five-key dictionary — **it will fail, and that is the test working.** Update it with the new row

### Discord referrals — new on 25 August 🔗

Nothing here exists yet: `promo`, `referral`, `coupon` and `affiliate` all return
zero matches across the backend. **The field is the visible tenth of it** — the
attribution has to outlive the session, because the commission is owed on a
subscription that happens later.

- [ ] `promo_codes` table — one code per Discord server owner, activatable and deactivatable
- [ ] Promo-code field at sign-up, and the attribution **stored on the user permanently**, not in the session. An attribution lost at signup is a commission that can never be paid
- [ ] The code grants **+300 bonus credits, one off — not a discount.** A discount and a 15% commission on the same $3.99 leave almost nothing, and the free tier already gives 300 credits away, so a code that granted nothing would give the user no reason to type it and the owner nothing to announce
- [ ] Commission accrual as **ledger rows**, **recomputed on every renewal** rather than once at signup — a one-off commission on a recurring product misaligns the owner's incentive from month two. Money moving is already double-entry and append-only ([`docs/03-backend-architecture.md`](docs/03-backend-architecture.md) §2 principle 6), so this is a new counterparty, not a new financial model
- [ ] Owner-facing figures: how many signed up, how many subscribed, what is owed
- [ ] Payout: **accrue from day one, pay the first cohort by hand.** 🔴 The real process — schedule, threshold, channel, tax — is still unowned and is needed by the tenth server owner, not the first
- [ ] ⚠️ Abuse: self-referral, codes shared outside the server, and a chargeback landing after a commission is paid. One paragraph of thought before launch, not after

### Templates — small, and not an AI tool 🔗

Decided 25 August as **the user's own settings, saved and reapplied** — caption
style, colour grade, transition defaults, title styling. Not a supplied library:
that reading is [`vision.md`](vision.md) §Features 04 & 05 and carries a licensed
music library and the real-person-naming exposure, neither of which has an owner.

- [ ] Save the current project's settings as a named template, on the account
- [ ] Apply one to another project as **a single `commit`**, so it undoes in one step like every other bulk operation
- [ ] **No worker, no queue, no credits, no new job type** — it is a subset of the timeline document, so it belongs beside the editing operations rather than in the tools panel

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

Face mapping · lip sync · GPU cluster · noise removal · upscaling · stabilisation · clip finder · speaker tracking · ~~templates~~ · music · mobile · multiple video tracks · caption translation · subtitle files · custom LUTs · teams · publishing to TikTok/YouTube/Instagram · invoicing documents · tax configuration

> **Templates moved in on 25 August**, but only the narrow half of it. *Reuse
> your own project's settings* is phase 1. **A supplied library** — designed
> templates, licensed music, mood detection — is still out, and it is what the
> two commercial problems in [`vision.md`](vision.md) §Features 04 & 05 attach
> to. Reasoning in [`docs/13-mvp-direction.md`](docs/13-mvp-direction.md) §4.

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

# M2 — Accounts, upload, ingest

**What this document is.** The M1 spike left a write-up because it answered a
risky question and found two bugs nobody could see. M2 leaves this one for the
same reason: it changed three agreed documents, it made one contract-breaking
decision, and a real browser found three defects that a green test suite, a
strict type-check and a clean lint had all passed.

Read it before M3. Everything here is a decision or a trap that M3 inherits.

| | |
|---|---|
| **Milestone** | M2 — *"you can register, upload a real video, and see it as a clip with a waveform, and scrub it smoothly"* |
| **Finished** | 17 August 2026 |
| **Proof** | 92 backend tests · 96 frontend tests · 29 end-to-end checks in a real Chromium, three consecutive clean runs |
| **Checklist** | [`../PHASE1-TASKS.md`](../PHASE1-TASKS.md) · M2 |

---

## 1. What was decided, and why

### The refresh token is a cookie now — contract 1.2

Version 1.1 returned `refreshToken` in the response body and took it back in
the body on refresh. The frontend API client written during M0 called
`/auth/refresh` with `credentials: 'include'` and **no body** — it had always
assumed a cookie. The two could not both be right, and the conflict surfaced
the moment the endpoint was implemented.

**Resolved in favour of the cookie**, and the contract was changed to match.
The reasoning is not about which document is older: a refresh token in a body
must be stored somewhere by the client, and both options are bad. In memory it
is lost on every reload; in `localStorage` it is readable by any XSS. An
httpOnly cookie is readable by neither, so an XSS that can call the API *as*
the user still cannot walk away with a 30-day credential.

The price is that a non-browser client cannot hold a session. Phase 1 is web
only. When the mobile app arrives in phase 3 it needs a **second grant type**,
not a change to this one.

> This is the only breaking change M2 made to an agreed document. It needs the
> lead's acknowledgement, not their permission — but they should know.

### The timeline was built with no visual identity

No palette, typeface or visual states have been delivered. Rather than stop, or
invent a look that would be thrown away, M2 built the timeline **structurally**:
ruler, playhead, zoom, track, clip, waveform, scrubbing — all working — with
every colour routed through a token in
[`../frontend/src/styles/globals.css`](../frontend/src/styles/globals.css), and
every token a neutral grey or a system font.

Nothing in `editor/timeline/` holds a literal colour. Applying the real charter
is an edit to that one block, and it cannot break behaviour. The three states a
charter has to answer — **clip selected**, **clip dragging**, **track muted** —
are already named there and distinguishable today by lightness alone.

**M3 is still blocked on the charter.** What M2 removed is the rework, not the
dependency.

### M2 deliberately does not persist the timeline

`POST/GET/PATCH /projects` stay in M3, whose title is *"Editing that survives a
reload"*. M2's timeline lives in the browser and is gone on reload.

The end-to-end run **asserts** that — it reloads the page and checks the clip is
gone. A boundary that is checked is a boundary; one that is merely intended is a
thing someone quietly crosses.

---

## 2. Three contradictions found in the agreed documents

Each was verified against the running system before being called a
contradiction, and each is now fixed.

### `docker-compose.yml` made every proxy world-readable

The bucket bootstrap ran `mc anonymous set download` on `proxies/`, `thumbs/`
and `peaks/`. [`03-backend-architecture.md`](03-backend-architecture.md) §6.3
says the opposite in as many words: *"Everything is private. Delivery is through
CloudFront with signed URLs, one hour for playback, so a leaked URL expires on
its own."*

Confirmed live: an anonymous `GET` on a proxy object returned **200**.

Two consequences, and the second is worse than the first. Anyone who could
guess a UUID could read anyone's video — and development never exercised the
signed-URL path the browser actually has to use in production, so nobody would
have found out until deployment.

Fixed and verified: anonymous **403**, signed **200**. The corrected bootstrap
also repairs an existing volume, which was tested by deliberately re-breaking
the policy and running it again.

### The peaks format contradicted itself

§6.2 described *"min/max amplitude pairs at ~100 buckets/second"* — two numbers
per bucket. [`05-api-contract.md`](05-api-contract.md) §3 says one value per
bucket, and its own arithmetic ("a 10-minute file is ~60 000 numbers") only
works that way: 600 seconds × 100 buckets is exactly 60 000 **if** there is one
number each.

The contract is what both sides build against, so one value per bucket ships:
the **peak** in that hundredth of a second, not an RMS. RMS would flatten
exactly the transients a waveform is read for. §6.2's wording was corrected.

### The prefixed-id format was never pinned down

The conventions say identifiers are *"UUIDs, prefixed in responses"* and every
example in the contract is truncated — `usr_9b1d…`. Nothing said whether the
suffix is a canonical UUID, hex without dashes, or something shorter.

Fixed in [`../backend/app/api/ids.py`](../backend/app/api/ids.py) as prefix +
underscore + canonical lowercase UUID, and written into the conventions. Also
added `usr_`, which the list had omitted while the contract used it throughout.

---

## 2b. What the test harness was hiding — found 18 August, during M3

Two M2 defects, one of them a security defect, both invisible because of the
same gap: **the test suite never exercised the request transaction boundary.**

`app/db.py` promises *"commits on success, rolls back on any exception"*. The
`client` fixture overrode `get_session` with a bare `yield db` — no commit, no
rollback — so every request in a test ran inside one transaction that was never
ended. Any handler that wrote and then raised looked correct to the suite and
was wrong in production. The override now mirrors the real semantics with a
nested savepoint, and adding that made an existing, previously-green test fail.

### 2b.1 The refresh-token chain was never actually revoked 🔴

Contract §2: *"Presenting an already-rotated token revokes the whole chain and
forces a fresh sign-in; that pattern means a token leaked."* The handler does
call `revoke_all_for_user()` — and then raises `TokenRevokedError` to return the
401. `get_session` caught that exception and rolled the revocation back.

So on a replayed refresh token the API logged the reuse, cleared the cookie, told
the user to sign in again, and **left every token in the chain valid**. The
branch that exists to kill a leaked session was the one event guaranteed to undo
itself. `revoke_all_for_user` is now committed before the raise.

### 2b.2 A rejected upload kept its reservation

`POST /media/{id}/complete` compares the object's real size against the size that
was announced, and soft-deletes the reservation when they disagree — the check
that stops someone reserving one byte and uploading a gigabyte. The soft delete
was rolled back by the 422 that followed it, so the row survived at its
*announced* size while storage held whatever was really uploaded, which is the
quota evasion the check exists to prevent. Also committed before the raise.

The test for it asserted the 422 and the error details but never that the
reservation was gone — its own docstring described a property it did not check.
It does now.

---

## 3. What the browser found that nothing else could

The M1 spike established the method: drive a real Chromium over the DevTools
protocol and check the **result**, not the page's account of itself. M2 kept it,
and it paid for itself three times over. Every one of these passed 92 backend
tests, 96 frontend tests, `mypy --strict`, `tsc --noEmit` and ESLint.

### 3.1 Infinite render loop

`selectClips` returned `videoTrack(...)?.clips ?? []`. Zustand compares what a
selector returns **by reference**; `?? []` builds a new array on every call, so
the value was never equal to the last one, the component re-rendered, the
selector ran again.

React failed with *"Maximum update depth exceeded"* the moment the timeline had
no video track — which is its state on first paint, so the editor was unusable
from the first frame.

The selector is correct in isolation. It only misbehaves once React subscribes
to it, and no unit test subscribes. Fixed with one frozen module-level empty
array.

### 3.2 The compositor could not draw a real proxy ⚠️

The one that matters. `texImage2D` threw:

```
SecurityError: Failed to execute 'texImage2D' on 'WebGL2RenderingContext':
The video element contains cross-origin data, and may not be loaded.
```

Proxies come from object storage on another origin — MinIO on `:9000` in
development, a CDN in production — while the app is on `:3000`. Without
`crossOrigin` on the `<video>`, the response is opaque, the frame taints the
canvas, and WebGL refuses the upload.

**The element loads, plays, and reports `readyState 4` the whole time.** Only
the texture upload fails. Every other signal said the video was fine.

Wiring the compositor to real ingest output is the entire point of M2's
frontend, and it was completely broken. The M1 spike could never have caught it:
its media was served from the app's own origin.

Fixed by setting `crossOrigin = 'anonymous'` **before** `src` — after `src` it
does nothing, because the fetch has already started without CORS and the
browser will not restart it. MinIO answers with the right headers by default;
**CloudFront will need a response-headers policy that does the same**, which is
now recorded in §6.2.

### 3.3 The ingest worker died on its second job

The Celery task calls `asyncio.run()` per job while sharing the module-level
pooled engine. A pooled asyncpg connection remembers the loop it was created
on. Job one succeeded and left a connection in the pool; job two picked it up on
a different loop:

```
RuntimeError: Task ... got Future ... attached to a different loop
```

which reads like an application bug and is a lifetime mismatch. A test that
ingests one file never reaches the second, so the whole suite was green.

Fixed with a per-task engine using `NullPool`, disposed with the loop that
created it. One connection setup per job, against a job that runs ffmpeg for
several seconds.

---

## 4. Things M3 should not rediscover

**`alembic check` is in CI now.** It is the guard against a model and a
migration drifting apart, and it found two real drifts on its first run: check
constraints picking up a double `ck_` prefix from the naming convention, and
`postgresql_ops` being used to express sort order when it carries operator
classes — so four indexes claimed `DESC` and were ascending.

**The M2 migration is hand-written, not autogenerated.** Autogenerate gets three
things wrong here and each is silent: it re-creates native enum types a column
already installed, it drops `postgresql_where` from partial indexes, and it
cannot order the circular foreign key between `media_assets` and `jobs`. All
twelve partial indexes were verified present in the live schema.

**Scoping is structural.** A `ScopedRepository` cannot be built without a user,
and every query starts from `_select()`, which already carries the filter.
There is no method that returns an unfiltered statement. `test_media.py` proves
it from outside with two accounts against every endpoint — the stranger gets
`404`, never `403`, because telling them apart confirms the id exists.

**Tests run against real infrastructure.** Real Postgres, real MinIO, real
ffmpeg, no mocks — because what M2 gets wrong is exactly what a mock cannot
reproduce. The peaks extractor is cross-checked against ffmpeg's own
`volumedetect` rather than against our own arithmetic: an extractor that is
internally consistent and wrong by a constant factor produces a waveform that
looks entirely plausible and is not the audio.

**Display dimensions, not stored ones.** A phone recording portrait stores a
landscape frame plus a rotation flag. Reporting the stored dimensions puts every
such upload on the timeline with its aspect ratio on its side.

**The proxy never upscales.** `scale=-2:'min(480,ih)'`. A flat 480 would spend
encode time turning a 240p upload into a blurrier, larger file.

---

## 5. Still open

| | What | Who |
|---|---|---|
| 🔴 | **A palette, a typeface and the visual states.** M3 is the editor and is blocked on this. The timeline is built to receive it as a one-file change. | Project lead |
| 🟠 | **Storage quota per tier.** The contract rejects an upload for "insufficient storage quota" and §9 defines the error with `usedBytes`/`limitBytes`, but no document says how many GB a tier gets. The enforcement path is built and tested; the values in [`../backend/app/services/plans.py`](../backend/app/services/plans.py) are marked `PLACEHOLDER` and **must not ship**. | Project lead |
| ✅ | ~~**Multi-part upload in the browser.**~~ **Done 28 August** — the client transfers the parts instead of refusing the file, and the server's completion step, broken since this milestone shipped, was fixed with it. [`19-multipart-and-ci.md`](19-multipart-and-ci.md) | Done |
| | **The end-to-end run is not in CI.** It needs six services up at once. Worth adding before M5, when export makes the browser-versus-FFmpeg comparison a release gate. | Before M5 |
| | **Safari on macOS.** Still never opened. Low risk since iOS — the harder case — passed in M1. | Before launch |

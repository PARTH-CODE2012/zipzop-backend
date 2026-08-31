# M5 readiness — what export inherits, and the one thing that blocks it

**Written 25 August 2026 from a full read of the schema, the job pipeline, the
contract and the container build.** Same purpose as
[`10-m4-readiness.md`](10-m4-readiness.md): establish what is already built so
M5 does not rebuild it, what the contract already decides so it is not
re-decided, and what is genuinely open — with a recommendation rather than a
decision taken without you.

| | |
|---|---|
| **Milestone** | M5 — *"you export a 1080p 9:16 MP4 and it looks exactly like the preview"* |
| **Status** | 🟢 **Ready to start**, with one problem to solve in the first hour — §3 |
| **Blocking** | Nothing external. No provider account, no decision from the project lead |
| **Checklist** | [`../PHASE1-TASKS.md`](../PHASE1-TASKS.md) · M5 |

---

## 1. What M5 inherits, already built and running

This is the shortest list of the four readiness documents, because M4 built the
machine and export is another worker on it.

| | Where | State |
|---|---|---|
| `JobTool.EXPORT` | `models/enums.py` | ✅ In the enum **and in the Postgres type** — no migration needed |
| `export` → `render` family | `services/jobs.py` `FAMILY_FOR_TOOL` | ✅ Already mapped |
| Cost and estimate | `services/pricing.py` | ✅ **2 credits per minute**, `45 s` per minute estimated. One function shared by `POST /jobs` and `/jobs/estimate` |
| The `render` queue | `workers/celery_app.py` | ✅ Routed, with the four priority bands |
| Reserve · claim · progress · refund · cancel · idempotency | The whole M4 pipeline | ✅ Tool-agnostic. Export gets all of it free |
| `plans.max_export_height`, `plans.watermark` | `models/billing.py`, seeded | ✅ Columns exist, values seeded for four plans, already returned by `GET /me` |
| Timeline validation, all 8 invariants | `services/timeline.py` | ✅ The renderer can trust the document it is handed |
| `RENDER_FAILED`, `VERSION_CONFLICT` | `api/errors.py` | ✅ Defined, unused |
| Five `.cube` files | ~~`frontend/public/luts/`~~ `backend/app/assets/luts/` | ✅ **Moved 28 August** — inside the build context, resolved by `app/services/luts.py`. §3 is what this was |

**The contract needs no changes.** [§6.2](05-api-contract.md) specifies the
`export` payload, the plan gating, the `409` on a stale `timelineVersion`, the
result shape and the fact that the watermark is server-side and not a parameter.
[§4.4](05-api-contract.md) states the property the whole milestone turns on:
*"Both sides must produce the same picture — the LUT files are shared assets,
not two implementations."*

---

## 2. What has to be written

Nothing here is surprising; it is listed so the size is visible.

| | |
|---|---|
| `ExportInput` schema | `PHASE_1_TOOLS` in `api/schemas/job.py` deliberately excludes `export`, so `POST /jobs {"tool":"export"}` is rejected today. Adding it is the schema plus one tuple entry |
| `workers/tasks/render.py` | Does not exist. A thin wrapper like `analysis.py` — loop, session, retry policy — with the work in a service |
| The FFmpeg filter graph | **The milestone.** Trims, concat, transform, crop, LUT at strength, per-word text, transitions, audio mix with fades — one graph, one pass |
| `storage.export_key()` | `storage.py` has `original_key`, `proxy_key`, `thumbnail_key`, `peaks_key` and no `export_key`. The `exports/` prefix is in the architecture doc and nowhere in code |
| Plan gating | The columns are there and nothing reads them. `403 PLAN_LIMIT_EXCEEDED` rather than a silent downgrade |
| Watermark | Applied server-side from the plan |
| Progress from `-progress` | FFmpeg's own output, parsed into the same Redis channel the analysis tools publish to |
| `GET /catalog/luts` | Promised by contract §4.4 and **not implemented**. The client currently hardcodes the five names |
| Frontend export dialog | Presets gated by plan, progress, download |
| ⚠️ Frame comparison test | Browser output vs FFmpeg output on the same grade. **This is the test that makes the milestone's closing condition true** rather than assumed |

---

## 3. 🔴 The one real problem: the renderer cannot read the LUTs

**The architecture's claim is that the browser and the export renderer share the
same `.cube` files byte for byte. Right now the backend cannot open one at all.**

| | |
|---|---|
| Where the files are | `frontend/public/luts/*.cube` — committed, all five |
| What generates them | `make luts` → `scripts/make_luts.py frontend/public/luts` |
| What the backend has | **Nothing.** No path, no loader, no reference. `color_analysis.py` names five looks it has no way to read |
| What the container has | **Not the files.** `docker-compose.yml` builds `api` and `worker` with `context: ./backend`, so nothing outside `backend/` is in the image |

So `lut3d=file=…` has no file to point at, in development *or* in production,
and the failure arrives at the moment the first graded clip is exported rather
than at build time.

**It is also unguarded.** The test that supposedly keeps the two catalogues in
step — `lut-catalogue.test.ts`, *"has a file for every look the server can
recommend"* — compares the client's list against `SERVER_LOOKS`, a copy of the
server's names **kept in step by hand** and carrying a comment saying so. It
never reads `color_analysis.py`. A look added on the server passes it.

### Three ways out, and a recommendation

| | | |
|---|---|---|
| **A** | Move the files to a repository-root `luts/`, and have both sides read from there — `make luts` writes once, the frontend copies or symlinks into `public/` at build, the Dockerfile copies the directory in | Honest about them being **shared assets**, which is what the contract calls them. Needs the build context widened to the repository root, or the files copied into `backend/` by the Makefile |
| **B** | Generate them into `backend/app/assets/luts/` and have the frontend build copy them into `public/` | Smallest container change; the backend owns them, which matches who needs them at export |
| **C** | Upload them to S3 at deploy and have the worker fetch them | Most moving parts, an extra failure mode at render time, and a cache to invalidate. **Not worth it for 5 × 133 kB of static text** |

**Recommendation: B.** The renderer is the side that cannot work without them,
the container build stays as it is, and `make luts` grows one copy step. Whatever
is chosen, the check that matters is the same and should land with it: **a test
that asserts every name in `color_analysis.LOOKS` has a readable `.cube`, run
from the backend** — the direction the current test does not cover.

> **Do this before writing any of the filter graph.** It is an hour, and every
> hour after it spent on rendering assumes it.

> ### ✅ Done 28 August, and it was B
>
> The grades live in `backend/app/assets/luts/`, inside the build context, and
> `app/services/luts.py` resolves a look to a path. The browser's copy under
> `frontend/public/luts/` is **generated and gitignored** — `pnpm dev|build|test`
> and `make luts` both run `frontend/scripts/sync-luts.mjs`, so there is one
> source of truth and the two cannot be different files. Committing both was the
> obvious alternative and was rejected: two copies meant to be identical is a day
> when they are not.
>
> Two things this section did not anticipate:
>
> * **`lut3d=file=…` is a filter option, so its path needs the same two-level
>   escaping `movie=` does.** That escaping was one level short until 27 August
>   ([`17-first-real-test-run.md`](17-first-real-test-run.md) §2.3) and was
>   private to `color_analysis`. It moved to `app/services/ffmpeg_filters.py`,
>   because a second private copy is how the first one's bug comes back somewhere
>   nobody has looked.
> * **The test had to run FFmpeg, not a parser.** A `.cube` our own reader likes
>   proves our reader agrees with our writer, which is a closed loop. Each grade
>   is handed to `lut3d` and then measured: a mid-blue reads YAVG 103 ungraded
>   and 88–108 through the five, so the tables demonstrably do something. An
>   identity LUT would have passed every structural check and produced exports
>   indistinguishable from ungraded ones.

---

## 4. Two decisions worth taking deliberately

Neither blocks starting, and neither needs the project lead.

### 4.1 What "looks exactly like the preview" is measured as

The closing condition is a visual claim, and the checklist already asks for a
frame comparison — *"if these drift, users edit one picture and download
another"*. What it does not say is **the tolerance**, and a comparison with no
stated threshold either passes on anything or fails on everything.

Two things genuinely differ and always will: the preview composites a **480p
proxy** and the export uses the **original**, and H.264 is lossy on both sides.
So an exact match is the wrong target.

**Recommendation:** compare at matched resolution, on **mean absolute error per
channel over a downscaled frame**, at a handful of playhead positions, with a
stated ceiling. Start it loose enough to pass on a correct implementation, and
tighten it once there is a measurement — the same treatment
`SECONDS_PER_MINUTE_OF_MEDIA` should have had. What the test must catch is a
*systematic* drift: a LUT applied at the wrong strength, in the wrong colour
space, or not at all.

### 4.2 Transitions exist in the document and not in the preview

`09-m3-notes.md` §5 left preview transitions open, and the adapter still says so:
every join is drawn as a cut, including the ones with a dissolve on them, because
the engine derives a crossfade from two clips *overlapping in time* and the
document forbids that (invariant 1).

**M5 makes this a contradiction rather than a gap**: the renderer will implement
the dissolve, so the exported file will have a transition the preview never
showed. That is precisely the failure §4.1 exists to catch, and it will be
"caught" by design rather than by defect.

**Recommendation:** decide it now, and it is a scope call rather than a technical
one — either the preview learns transitions in M5 (engine work, plus deciding
which side gives up the frames), or the export renders them and **the editor says
so** on a clip that has one. Silently differing is the one option that is wrong.

---

## 5. What M4.5 changed that M5 touches

Small, but worth knowing before opening the editor's files.

* **The tools are rail modes now.** The export dialog is a new surface, not a
  fourth tool panel — most likely a header action rather than a rail mode, since
  it applies to the project rather than to a selected clip.
* **`ToolsPanel.tsx` is gone.** Anything M5 wants to copy is in
  `editor/rail/panels.tsx`; the estimate-then-run pattern with the price on the
  button is `ToolRow` there.
* **`NumberField` exists** for anything with a range, and its `scale` prop is
  what a percentage-shaped control needs — see `14-m4-5-notes.md` §2 for why.
* **Playback length now comes from the document**, not from the clips that
  happen to be playable ([`timeline-adapter.ts`](../frontend/src/editor/playback/timeline-adapter.ts)).
  The export's own duration should come from the same `timelineDurationMs`, so
  the file is as long as the project rather than as long as its video track.

---

## 6. The honest state of verification

> **Superseded in part, 27 August 2026.** Docker Desktop and ffmpeg are now
> installed on this machine and the backend suite has actually run. What
> follows is what this note said on 25 August, corrected item by item below;
> [`17-first-real-test-run.md`](17-first-real-test-run.md) is the full account.

**M4.5 has not been through `make e2e`, and neither has anything since M4.**
That needs Docker, which the machine this was built on does not have. The unit
suites are green — 304 frontend tests — and the backend suite has not been run
since M4 because it needs Postgres, Redis and MinIO.

**Nothing in M5 can be verified without that infrastructure.** Export is ffmpeg,
object storage and a worker; there is no fixture-server version of it, and the
frame comparison test in §4.1 is the milestone's whole point. So the first
practical prerequisite is not code:

- [x] A machine with Docker, or Postgres + Redis + MinIO natively — Docker
      Desktop 29.7 on WSL2, ffmpeg 9.0.1, all three services healthy
- [x] `make migrate`, then `make test-backend` green from a known state — 231
      passed, 2 skipped. The two skips are the transcription tests, which want
      a warmed `faster-whisper` model cache and skip rather than fail without
      one
- [ ] `make e2e` green — it covers M2 end to end and will confirm the stack is
      really working before M5 starts adding to it. **Still open**: it drives a
      real browser against `make e2e-up`, which needs `pnpm` on PATH and a
      `tmux`-free way to hold three servers up on Windows. This is now the only
      unverified thing standing between here and M5

---

*Readiness note · 25 August 2026 · written before M5, from the code rather than from the plan*

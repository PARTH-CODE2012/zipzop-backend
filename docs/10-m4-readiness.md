# M4 readiness — what exists, what is missing, what is still undecided

**Written before M4 starts, from a full read of the schema, the workers, the
contract and the vision doc — so the session that picks up M4 does not spend
its first hour rediscovering this.**

| | |
|---|---|
| **Milestone** | M4 — *"you run captions on a real clip, watch progress, see the words appear in time, and fix a misspelled name"* |
| **Written** | 19 August 2026, before any M4 code |
| **Checklist** | [`../PHASE1-TASKS.md`](../PHASE1-TASKS.md) · M4 |
| **Depends on** | [`03-backend-architecture.md`](03-backend-architecture.md) §5 (jobs, credits) · [`05-api-contract.md`](05-api-contract.md) §6 (the job endpoints, spelled out per tool) |

---

## 1. Two decisions that block a *real* Captions tool, and nobody has made them

Neither is in any document. Both surfaced by reading the vision doc's own
"still to confirm" lists, not by guessing.

### Which transcription engine

[`01-product-vision.md`](01-product-vision.md) §5.2 and §7 describe what
Captions *does* — word-level timing, emphasis, confidence — and never once
name what produces it. `backend/pyproject.toml` has no speech library at all:
no `faster-whisper`, no `openai`, no `deepgram-sdk`. This is not an oversight to
fix in passing; it is a real commercial decision (self-hosted model vs. a
paid API) with cost and latency attached, and [`03-backend-architecture.md`](03-backend-architecture.md)
§5.5 prices captions at "cents" per job — which constrains the answer without
naming it.

**Recommendation, not a decision made on anyone's behalf:** self-hosted
`faster-whisper` on CPU (the `base` or `small` model) for phase 1. It runs on
the same worker fleet as everything else, has no per-call cost that scales with
usage, and the accuracy ceiling — mentioned in the vision doc's own caveat
about the PRD's "98%" figure being a marketing claim, not a target — is
honestly the same order of accuracy a cheap third-party API gives at this
model size. A paid API is the one to reach for if latency turns out to matter
more than cost once this is measured. Either way, the job pipeline should not
be built assuming one over the other — put the actual transcription call
behind one function boundary in the analysis worker, so swapping providers
later is a function body, not an architecture change.

### The language list

[`02-scope-v1.md`](02-scope-v1.md) §3.4 says outright: *"Language list — blocked
on the '30+ languages' question in the vision doc."* Still blocked. Phase 1
transcribes in the language spoken and does not translate, so this decides
which `language` values `POST /jobs` accepts for `captions` — not whether the
feature ships. **Whisper-family models support the same ~99 languages
regardless of which one is chosen above**, so this does not block starting the
job pipeline; it blocks writing the accepted-language list into the schema.
`language: "auto"` (already in the contract's own example payload) is enough
to start with.

**Neither of these blocks starting M4.** They block finishing the Captions
tool with real transcripts. Everything else in this document — the job
pipeline, credits, the WebSocket, smart trim, colour analysis, the frontend —
does not wait on them.

---

## 2. What M4 inherits, already built and already tested

More than the checklist's blank boxes suggest. Confirmed by reading the models
and running the migration, not by trusting a comment.

**Schema — migrated, in `zipzop_test` and in the dev database.**
`jobs` (status, family, tool, priority, progress, credits reserved/settled,
idempotency key with a unique partial index, result/result_key, output asset
link) and `credit_ledger` (bucket, delta, reason, running balance, one
reserve-or-refund per job *per bucket* enforced by a unique partial index —
the constraint that makes a double refund structurally impossible even if a
worker retries). `plans` is seeded with real numbers for all four tiers from
[`03-backend-architecture.md`](03-backend-architecture.md) §5.5 — credits,
`facemap_seconds`, `fair_use_credits`, `max_export_height`, `watermark`,
`queue_priority`.

**Every enum the pipeline needs.** `JobFamily`, `JobStatus`, `JobTool`,
`CreditBucket`, `LedgerReason` — all in `app/models/enums.py`, matching the
architecture doc exactly. Nothing to invent here.

**Queues, routed and bound.** `app/workers/celery_app.py` already routes
`app.workers.tasks.analysis.*` → `analysis`, `.render.*` → `render`,
`.inference.*` → `inference` (phase 2), with `task_acks_late` and
`task_reject_on_worker_lost` set so a killed worker does not lose a job. The
files themselves — `app/workers/tasks/analysis.py`, `render.py` — do not exist
yet; only `ingest.py` and a stub `billing.py`.

**Concurrency limits, coded.** `app/services/plans.py` has
`CONCURRENCY_LIMITS` per family per plan, matching §5.3's table exactly.
`concurrency_for(plan)` is the one call M4 needs at job creation.

**Idempotency, generalised.** `app/services/idempotency.py` already backs
`POST /media/uploads`; its own docstring says `POST /jobs` is the other
intended caller. No new mechanism to design, only a second call site.

**The one credit ledger row that exists today** is `signup_grant`, written in
`UserRepository.create_with_free_plan`. `reserve`, `refund`, `plan_grant` and
`plan_expiry` are real `LedgerReason` values with **no code that writes them
yet** — `allocate()` from §5.4 does not exist. That function, and the
transaction around it, is the centre of M4's backend half.

**Assets are already ownership-checked.** `MediaAssetRepository.get_visible`
and `.by_ids` (added in M3, for timeline validation) are exactly what job
creation needs to resolve `input.assetId` and reject someone else's asset —
reuse them, do not re-derive the check.

**One LUT file exists.** `frontend/public/spike/luts/cinematic_warm.cube`, from
the M1 spike. The contract and the scope doc both call for **five**, shared
between the browser (WebGL preview) and the renderer (export) — see charter
§4.4 in the frontend architecture doc. Four more `.cube` files, or a documented
plan to generate them, is M4 work, not something to discover mid-milestone.

**Nothing job-related is in `openapi.json` yet.** Confirmed by inspection —
zero `Job*` schemas, no `/jobs` path. Clean slate, no drift to reconcile.

---

## 3. What does not exist and the contract already specifies in full

Worth knowing before writing a line, because the contract answers questions
that would otherwise become mid-implementation decisions:

- **Every per-tool payload is specified**, request and result, for `captions`,
  `smart_trim`, `color_analysis` and `export` — [`05-api-contract.md`](05-api-contract.md)
  §6.2. The captions result's short keys (`w`, `s`, `e`, `c`, `em`) are not
  arbitrary; they are chosen because a 60-minute transcript is thousands of
  objects.
- **`GET /jobs/{id}` must return both shapes** for a large result: `result`
  inline under 256 KB, `resultUrl` above it. A caption result on a long
  recording *will* cross that line — this is not an edge case to skip.
  §6.3.
- **`/catalog/luts` and `/catalog/caption-styles`** are named in the endpoint
  table (§10) but have no request/response body documented anywhere in the
  contract. Two small routes and two response shapes need designing before
  they can be built — five minutes of work, but undone work today.
- **The WebSocket's shape is architectural, not improvised**: one Redis
  channel per user (`user:{id}`), fire-and-forget, **the socket is confirmed
  as an optimisation and not the source of truth** — `GET /jobs/{id}` must
  work standalone and is what a client falls back to. Build the polling
  fallback and the reconnect re-sync as first-class paths, not as an
  afterthought once the socket works.

---

## 4. Suggested order

Not a rewrite of the checklist — a reading of it with the dependencies made
explicit, because "backend — job pipeline" and "backend — the three tools" read
as parallel sections and are not.

1. **`allocate()` and the ledger.** Nothing else can be tested end to end
   without it, and it is the one piece with a correctness property worth
   writing a property-style test for: two concurrent jobs against a tight
   balance must not both succeed.
2. **`POST /jobs` and `POST /jobs/estimate`** sharing one cost function, against
   `color_analysis` first — it is the tool with no external dependency at all
   (a histogram over sampled frames via ffmpeg, no transcription engine to
   pick). This proves the whole pipeline — creation, concurrency limit,
   priority, worker claim, progress, credits settling — before the harder
   tools touch it.
3. **`smart_trim`** next: ffmpeg's `silencedetect` gets silence for free;
   filler/stutter/repeat detection needs *some* transcript, so it is coupled to
   the engine decision in §1 more tightly than it first looks.
4. **`captions`** last of the three, once §1 is answered.
5. **The WebSocket and the frontend tool integration** can start as soon as
   `POST /jobs` exists — they need a job to watch, not a finished tool.

---

## 5. Everything else in the M4 checklist

Read [`../PHASE1-TASKS.md`](../PHASE1-TASKS.md) · M4 for the rest — retry and
backoff, cancellation, the nightly reconciliation job (the stub in
`billing.py` is exactly where it lands), the mock server and fixtures moved
here from M0, and the frontend's caption-editing and low-confidence flagging.
Nothing there was found to be wrong or missing; this document only covers what
a read of the code and the docs found that the checklist's one-line items do
not show.

# M4 build notes — the job pipeline, and the first tool through it

**What M4's backend half decided, what it found, and what it is still waiting on.**

| | |
|---|---|
| **Milestone** | M4 — *"you run captions on a real clip, watch progress, see the words appear in time, and fix a misspelled name"* |
| **Dates** | 20 August 2026 |
| **Read it after** | [`10-m4-readiness.md`](10-m4-readiness.md), which this follows the order of |
| **Checklist** | [`../PHASE1-TASKS.md`](../PHASE1-TASKS.md) · M4 |

Proven on a running stack: register, upload a 12-second clip, wait for ingest,
estimate, create the job, watch a Celery worker claim it, and read back

```json
{ "lut": "cinematic_warm", "strength": 0.66,
  "scene": { "exposure": "normal", "whiteBalance": "neutral", "contrast": "flat" },
  "alternatives": [ { "lut": "vlog_clean", "strength": 0.53 }, … ] }
```

with one credit reserved, settled, and the replayed idempotency key returning
the same job rather than a second one.

---

## 1. What was decided

### The enqueue happens after the commit, and the architecture doc's step 6 is wrong

[`03-backend-architecture.md`](03-backend-architecture.md) §5.2 lists job
creation as seven steps *in one transaction*, the sixth being "enqueue the
Celery task". Doing that literally hands a worker a job id that no other
connection can see yet: the task starts, reads nothing, and fails a job the user
has not yet been told exists. It is a race that only shows up under load, which
is the worst kind.

`POST /jobs` therefore commits **first** and sends the task afterwards. The
credits and the job row are still one transaction — that property is the whole
point and is untouched — but the message goes out only once the row is durable.
The worker also treats "job not visible" as a transient failure and retries, so
the ordering is defended in two places rather than trusted in one.

### Concurrency is enforced at the claim, not at creation

§5.3 says the caps are "enforced at creation" and then, two sentences later,
that beyond the limit *"jobs stay `queued` and start as slots free up — the
request still succeeds"*. Both cannot be true of the same check. The second is
the one the client depends on, so the cap lives in the claiming `UPDATE`: a
worker will not take a job whose owner is already at their limit, and the task
retries in fifteen seconds. `POST /jobs` never fails for concurrency.

### Cost and estimate are one function, because the alternative is a bug report

Contract §6.1 asks for the estimate to be *"exact, not indicative"*. Two
implementations that agree today are two implementations that disagree the first
time one is edited, so `services/pricing.py` is called by both endpoints and a
test asserts the two agree field for field. A price on a button that differs
from the price on click is the kind of bug users report as theft.

### Colour analysis first, and it earned its place

The readiness doc's suggested order put it first because it has no external
dependency — a handful of sampled frames through ffmpeg's `signalstats`, no
transcription engine to choose. That turned out to matter more than expected:
every part of the pipeline that could be wrong — the reservation, the claim, the
progress checkpoints, the settle, the refund — was exercised against a real file
and a real worker before anything harder touched it.

---

## 2. Two defects found while building it

### 🔴 A validator that raises `ValueError` produced a 500, not a 422

`register_exception_handlers` serialised Pydantic's error list straight into a
`JSONResponse`. A `field_validator` or `model_validator` that raises
`ValueError` puts the **exception object itself** in the error's `ctx`, and
`JSONResponse` cannot encode it — so the handler whose entire job is to turn a
bad request into a readable 422 raised inside itself.

Nothing caught this before because every previous validator failure came from a
built-in type check, whose `ctx` is a string. The first hand-written one — "this
tool does not ship yet" — was the request that broke, and the thing it broke was
the error path.

Every validator in the codebase was one line away from the same fault. The fix
scrubs the error list to JSON-safe values in `errors.py`, once, for all of them.

### Refunds were landing in the wrong bucket, and it was two clocks

`jobs.created_at` is the database's `now()`. A subscription's
`current_period_start` is written by whatever granted it — Python's clock, at
registration. Comparing them directly to decide whether the billing period had
rolled over meant a job created moments before a renewal looked like a job that
had outlived one, and its refund went to `topup` rather than `plan`.

The user is still made whole either way, so nothing was lost — but the ledger
recorded an event that did not happen, and the nightly reconciliation would have
had nothing to say about it. There is now a five-second tolerance, and the
reason is written where the comparison is.

---

## 3. What is deliberately not done

- **Captions and smart trim.** Both need a transcript, and *which transcription
  engine* is an open commercial decision — [`10-m4-readiness.md`](10-m4-readiness.md)
  §1, with a recommendation and no decision made on anyone's behalf. The
  pipeline runs them the moment there is something to call: `_work()` in
  `analysis_pipeline.py` is one `if` per tool, which is the boundary the
  readiness doc asked for so swapping providers is a function body.
- **The WebSocket endpoint.** The publish half is done and every checkpoint
  already goes onto the user's Redis channel. What is missing is `/ws` and its
  client. This is safe to leave last precisely because the socket is *not* the
  source of truth: `GET /jobs?status=running` is, and it works today.
- **Four of the five LUTs.** The looks are named, scored and recommended;
  only `cinematic_warm` has a `.cube` file. ⚠️ **A recommendation the browser
  cannot render is worse than no recommendation**, so the other four files are a
  blocker for showing this to anyone, not a polish item.
- **The nightly ledger reconciliation.** The stub in `billing.py` is where it
  lands. The ledger it would check is now written by exactly one module, which
  is what makes the alarm meaningful.

---

## 4. Numbers worth knowing

| | |
|---|---|
| Cost | `captions` 2 · `smart_trim` 1 · `color_analysis` 1 · `export` 2 credits per minute, rounded up, minimum one |
| The contract's own example | 623 480 ms of captions = **22 credits**, and the implementation returns 22 |
| Inline result limit | 256 KB, measured on the serialised JSON — above it the result goes to S3 |
| Retries | 3, backing off 10 s · 30 s · 90 s, transient failures only |
| Priority bands | 0 / 10 / 20 / 30, from `plans.queue_priority`, as Celery `priority_steps` |

🟠 `SECONDS_PER_MINUTE_OF_MEDIA` in `pricing.py` is a **heuristic, not a
measurement**, and carries the same caveat the architecture doc puts on its own
credit numbers: recalibrate from real jobs. Nothing in the product may present
it as an SLA.

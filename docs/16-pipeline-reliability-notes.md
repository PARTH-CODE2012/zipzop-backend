# Pipeline reliability — build notes

**What an outside audit found, what turned out to be true against the running
code, and what was fixed.** Written 26 August 2026.

| | |
|---|---|
| **Trigger** | An audit, delivered as one paragraph: *"Make the upload → processing → job pipeline reliable and self-recovering... database state, file storage, and background workers can get out of sync."* |
| **Method** | Every claim verified against the code before anything was changed — file and line, not taken on trust |
| **Status** | 🟢 Four fixes shipped. One gap named and deliberately left open — §5 |
| **Checklist** | [`../PHASE1-TASKS.md`](../PHASE1-TASKS.md) · *Pipeline reliability* |

---

## 1. What was checked before anything was changed

The audit proposed a state machine — `PENDING_UPLOAD → UPLOADED → QUEUED →
PROCESSING → READY`, with retry and reconciliation. Before building any of
that, the actual code was read to find out how much of it already existed.

**Most of it did.** `AssetStatus` (`pending_upload · probing · ready · failed`)
and `JobStatus` (`queued · running · succeeded · failed · cancelled`) already
form the shape the audit was asking for, under different names. The worker
claim is already atomic — `UPDATE jobs SET status='running' WHERE
status='queued'` in `JobRepository.claim` — so two workers cannot both pick up
the same job, and `analysis_pipeline.py` already has real retry logic:
`TransientFailureError` triggers `self.retry()` with backoff, and only
exhausting three attempts fails the job for good.

**What the audit was actually right about is narrower and more specific than
"build a state machine"**: the newer pipeline (jobs, M4) got this right, and
the older one (upload and ingest, M2) never received the same treatment. Four
concrete gaps, each confirmed by reading the code that has them:

| | Where | What was actually there |
|---|---|---|
| 1 | `app/workers/tasks/ingest.py` | `max_retries=2` declared, never exercised |
| 2 | `app/services/ingest_pipeline.py` | Every exception, including infrastructure ones, written straight to `failed` |
| 3 | `app/api/routes/media.py::complete_upload` | Celery message sent *before* the transaction committed |
| 4 | Nowhere | No sweep for a job whose Celery send failed, no sweep for an abandoned upload |

None of these needed a migration, a new enum value, or a rewrite. Each is a
handful of lines, once the actual problem was pinned down.

---

## 2. Ingest's retry was decorative

`process_asset`'s Celery task declared `max_retries=2, default_retry_delay=30`.
`run_ingest` caught every exception — `UnreadableMediaError` for bad media,
correctly, but also `except Exception` for everything else, including an S3
client error or a dropped database connection — and wrote `failed`
immediately. **No code path inside `run_ingest` ever called `self.retry()`.**
The retry policy in the decorator was never once reachable.

Contrast with `app/workers/tasks/analysis.py`, which already has the right
shape: `analysis_pipeline.py` raises `TransientFailureError` for
infrastructure-only failures, and the task wrapper catches exactly that and
retries with backoff (10 s · 30 s · 90 s), giving up only after three attempts.
M4 built this correctly. M2's ingest pipeline predates it and never got the
same fix.

**The fix mirrors it exactly.** `ingest_pipeline.py` now defines its own
`TransientFailureError`, and the catch-all clause wraps and re-raises instead
of swallowing:

```python
except Exception as exc:
    log.warning("ingest_transient_failure", asset_id=key_id, error=type(exc).__name__)
    raise TransientFailureError(f"ingest failed for {key_id}: {exc}") from exc
```

`workers/tasks/ingest.py` gained the same `MAX_RETRIES` / `RETRY_BACKOFF_SECONDS`
/ `_give_up` shape `analysis.py` already had. `UnreadableMediaError` is
untouched — a corrupt file still fails immediately, on the first attempt,
because retrying it three times produces the same answer three times and only
delays the message.

---

## 3. The upload-complete endpoint enqueued before its own commit

`POST /jobs`'s own module docstring already explains this exact race and why
the fix matters:

> *"Enqueueing inside the transaction hands a worker an id that is not yet
> visible to anyone else's connection: the task starts, reads nothing, and
> fails a job the user was never told about."*

`media.py::complete_upload` had the identical bug. `mark_probing()` wrote the
status change to the session, and `process_asset.delay(...)` was called
immediately after — but the actual commit only happened later, when
`get_session`'s dependency resumed after the handler returned. A worker could
claim the message and start reading the asset on its own connection before
that commit landed, and find the row still `pending_upload`.

**Fixed by moving the commit earlier**, with the same reasoning `jobs.py`
already states:

```python
await assets.mark_probing(asset, size_bytes=stored.size_bytes, checksum=None)
await session.commit()          # before the enqueue, not after
process_asset.delay(str(asset.id))
```

A second `commit()` still runs when the handler returns, via `get_session`.
That is not new — `jobs.py`'s `create_job` already commits explicitly and lets
the dependency commit again afterward, and it works today, so the same shape
here carries no new risk.

---

## 4. A sweep, not a rewrite

The audit's third ask — *"don't leave a job stuck forever"* — needed something
that did not exist at all: nothing periodically checked for a job whose Celery
message never arrived, or an upload nobody ever finished.

`app/services/pipeline_reconciliation.py` adds four checks, run every five
minutes by `app/workers/tasks/reconciliation.py::sweep_pipeline`, on its own
`reconciliation` queue so a billing backlog can never delay finding a stuck
upload:

| Check | Threshold | Action | Why the threshold |
|---|---|---|---|
| Stuck `queued`, no `started_at` | 10 min | Re-send `apply_async` | A legitimate concurrency-cap wait is already being retried every 15 s by the task itself (`JobUnavailableError`) — this only fires for a job with no Celery message in flight at all |
| Stuck `running` | 30 min | `requeue()`, then re-send | Generous next to every phase-1 tool's own timeout. Revisit once export (M5) ships — a render can legitimately run this long |
| `pending_upload` | 2 hours | Marked `failed` | Comfortably outlives the 15-minute presigned URL, so a slow upload is never caught mid-flight |
| `probing` | 20 min | **Reported only** | See §5 — this one is not safe to act on yet |

**Why re-sending is safe.** `claim()`'s `WHERE status='queued'` is what makes
this correct rather than merely convenient: a job that was never actually
stuck — already claimed, already running, already finished — matches nothing
on the sweep's re-send, and the task exits cleanly with `JobUnavailableError`.
Re-sending a message for a job that did not need it costs one wasted Celery
message and nothing else. This is not new machinery; it is the same guarantee
`POST /jobs`'s idempotency key already relies on, applied to a different
trigger.

**Why this reports and does not repair for money.** `reconciliation.py` — the
existing nightly credit-ledger check — is explicit that drift is reported, not
corrected, because a balance is evidence and silently fixing it destroys the
record of what went wrong. This module acts, on purpose, because job and asset
status are not a financial record; they are a claim about what a worker is
doing *right now*, and a stale claim is not evidence worth preserving. The two
modules make opposite choices deliberately, for different kinds of state.

---

## 5. What was deliberately not automated

**A stuck `probing` asset is reported, never touched.** This is the one place
the audit's ask and what is safe to ship diverge, and it is worth being exact
about why.

`Job` has an atomic claim: `WHERE status='queued'` in one `UPDATE` is what
makes it impossible for two workers to both pick up the same job, and what
makes re-sending a Celery message harmless. `MediaAsset` has **no equivalent**
— `run_ingest` reads the row and processes it with no `WHERE status='probing'`
guard, no `worker_id`, nothing preventing a second worker from doing the same
work concurrently. It does not even have an `updated_at` column, only
`created_at`, so the sweep cannot tell "just started processing a large file"
from "actually stuck" with any precision — only how long ago the *upload*
finished.

Re-sending `process_asset.delay()` for a `probing` asset under these
conditions could start a second worker transcoding and uploading the same file
while the first is still running — wasted compute at best, a race on the final
`finish_ingest` write at worst. That is a worse failure than the one being
fixed, so the sweep only logs `pipeline_sweep_asset_looks_stuck` and leaves the
row alone.

**The real fix is to give `media_assets` the same claim mechanism `jobs`
already has** — a `worker_id` column and a `WHERE status='probing'` guard on
the update that starts processing, mirroring `JobRepository.claim` exactly.
That is a migration and a small change to `ingest_pipeline.py`, not a config
value, which is why it is named here as a follow-up rather than done in the
same pass as everything else.

---

## 5b. Two defects in this pass's own code, caught by re-reading it

Worth recording, because both are the same *kind* of mistake the audit was
about, made while fixing it.

**`sweep_stuck_running_jobs` sent its Celery messages before committing.** The
first draft called `requeue()` and `apply_async()` inside one loop, with the
`commit()` after it. A worker picking up that message before the commit was
visible would read the row still `running`, match nothing on `claim()`'s
`WHERE status='queued'`, and give up — a wasted send and a job waiting another
five minutes. **This is character-for-character the bug being fixed in
`complete_upload` two files away**, written into brand-new code by the same
person on the same day. Fixed by requeueing every row and committing once,
before any message goes out.

**`sweep()` claimed each check was isolated and was not.** Its docstring said
*"one transaction per kind of fix, so one failing does not roll back the
others"*, and it then called all four unguarded in sequence — so a lock timeout
in the first would silently skip the other three. That is the failure mode this
whole module exists to prevent, reproduced one level up. Fixed with a
`_guarded` wrapper that logs, rolls back the dirty session and lets the
remaining checks run; the claim in the docstring is now true rather than
aspirational.

Both are covered by tests now. The lesson is not "be more careful" — it is that
commit-before-enqueue is subtle enough to be worth the explicit comment it now
carries in all three places it appears.

---

## 6. Verification, honestly

**Every file compiles** — `python -m py_compile` on all six touched and new
modules. The config guard's logic in a previous pass on this machine was
provable by executing the branch directly against stand-in values, because it
was pure Python; **this is not that** — every one of the four sweep checks is a
SQL query against Postgres, and there is no meaningful way to exercise
`sa.select(...).where(...)` without a real database behind it.

**This machine has no Docker**, so nothing that needs Postgres has run since
M4. Thirteen tests were written to the project's existing conventions —
`tests/test_pipeline_reconciliation.py` (twelve cases, each "acted on" paired
with a "left alone" at a fresher timestamp) and one addition to
`tests/test_ingest.py` proving a storage exception raises
`TransientFailureError` rather than writing `failed`. They are reviewed
carefully by hand and believed correct. They have not executed. Run them
before trusting this write-up over the code:

```bash
make migrate && make test-backend
```

---

## 7. What this changes for M5

Nothing blocks it. The claim gap in §5 is worth closing before export adds a
render queue with its own long-running, expensive jobs — a stuck render is a
more expensive thing to duplicate than a stuck 480p proxy. Worth revisiting
alongside [`15-m5-readiness.md`](15-m5-readiness.md) rather than deferred
indefinitely.

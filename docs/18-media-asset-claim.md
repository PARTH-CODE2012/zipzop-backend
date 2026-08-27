# The media_assets claim — closing the gap M4 left open

*27 August 2026 · the follow-up named in [`16-pipeline-reliability-notes.md`](16-pipeline-reliability-notes.md) §5*

| | |
|---|---|
| **Why now** | Named as "before M5" on 26 August. A stuck render is a far more expensive thing to duplicate than a stuck 480p proxy, and M5 adds a render queue |
| **Migration** | `0003_media_asset_claim` — three columns and one partial index |
| **Status** | 🟢 Shipped. 240 backend tests green, 9 of them new |
| **Checklist** | [`../PHASE1-TASKS.md`](../PHASE1-TASKS.md) · *Pipeline reliability* |

The 26 August pass added a sweep that recovers stuck jobs and abandoned
uploads, and deliberately stopped short of one case: a `probing` asset whose
worker died. `jobs` has an atomic claim, so re-sending a Celery message for one
is a harmless no-op; `media_assets` had nothing of the kind, so the same
recovery could have put a second worker on a file the first was still
transcoding. The sweep logged those assets and left them alone.

This closes it.

---

## 1. What the table gained

Three columns, deliberately named after their `jobs` counterparts so the two
read the same way:

| `media_assets` | `jobs` | What it is |
|---|---|---|
| `worker_id` | `worker_id` | Who holds it. **The discriminator the claim tests** — see §2 |
| `ingest_started_at` | `started_at` | When a worker took it, as distinct from `created_at`, which is when the *upload* was reserved |
| `ingest_attempts` | `attempts` | How many times anything has claimed it, ever |

Plus `ix_media_assets_status_probing`, a partial index mirroring
`ix_jobs_status_live` — `probing` is a handful of rows at any moment against a
table that only grows.

All three are nullable or defaulted, so the migration applies without a table
rewrite and without a backfill. **Existing `probing` rows come out with
`worker_id IS NULL` and `ingest_started_at IS NULL`**, which the sweep reads as
"never claimed" — the correct interpretation for a row that predates the claim,
and one that gets it re-sent rather than stranded.

---

## 2. The one place the mirror does not hold

The follow-up note said the guard would be `WHERE status='probing'`, mirroring
`JobRepository.claim`'s `WHERE status='queued'`. **That would not have worked,
and the reason is worth writing down, because it is the kind of thing that
looks right until you try it.**

`jobs` moves `queued → running`. The status *is* the claim: exactly one
worker's `UPDATE ... WHERE status='queued'` can match, and the row's own state
then says somebody has it.

An asset has no such pair. `complete_upload` writes `probing` and *then* sends
the message, so the asset is already `probing` before any worker sees it. There
is no state before `probing` and none between it and `ready`. `WHERE
status='probing'` would therefore match for **every** worker that tried, which
is not a claim at all — it is the same unguarded read the sweep was refusing to
trigger, with an UPDATE in front of it.

So `worker_id IS NULL` carries it instead:

```sql
UPDATE media_assets
   SET worker_id = :worker, ingest_started_at = now(),
       ingest_attempts = ingest_attempts + 1
 WHERE id = :id AND status = 'probing'
   AND worker_id IS NULL AND deleted_at IS NULL
```

Adding a fourth `AssetStatus` — `uploaded`, sitting between `pending_upload`
and `probing` — was the alternative, and would have made the mirror exact. It
was not taken: a new enum label is a schema change the API contract, the
frontend's generated types and every existing row's meaning all have to absorb,
in exchange for a nullable column's worth of information. The asymmetry is
cheaper than the symmetry, and this section is the price of it.

The consequence is that **releasing is not optional**. A job goes back in the
pool by returning to `queued`, which its own status expresses. An asset goes
back by having `worker_id` cleared, and nothing else does that, so every path
that abandons an attempt has to call `release_ingest_claim` explicitly — §4.

---

## 3. Commit before the work, not after

`run_ingest` claims and then commits, *before* downloading a byte.

This is not the commit-before-enqueue rule from the last pass; it is a
different one that happens to point the same way. The claiming UPDATE takes a
row lock held until the transaction ends, and the transaction here ends after
ffmpeg — minutes on a large source. Without the commit, a second worker's claim
would not return `None`, it would **block**, for the entire duration of the
first worker's transcode, and then match nothing anyway. Correct, and useless.

Committing turns a minutes-long block into an immediate `None` and an
`IngestUnavailableError` the task returns on. `analysis_pipeline.run_analysis`
already commits right after its claim for the same reason; this is the same
shape, arrived at from the same constraint.

---

## 4. Releasing, and why the attempt count survives it

`release_ingest_claim` clears `worker_id` and `ingest_started_at` and
**deliberately leaves `ingest_attempts` alone.** It is the mirror of
`job.requeue`, called in the same two places:

* **`ingest_pipeline`, on a transient failure** — the retry is a new attempt
  that has to claim again, and one still holding a `worker_id` from the attempt
  that just failed would match nothing and never run. The asset would sit
  `probing` for ever, claimed by an attempt that is over. This is a failure
  mode the claim *introduces*, and the release is what pays for it.
* **The sweep, for a worker that died** — released and committed before any
  message goes out, for the reason §3 of the previous note spells out.

Resetting `ingest_attempts` on release would have been the natural-looking
thing to do and would have made `MAX_INGEST_ATTEMPTS` unreachable: every
release zeroes the counter, so a file that kills its worker on attempt one is
re-sent for ever at five-minute intervals, each costing a full download and
transcode. The counter has to be the count of attempts *ever*, not attempts
since the last recovery.

---

## 5. What the sweep does now

The report is replaced by two checks that mirror the two job checks exactly:

| Check | Signature on the row | Threshold | Action |
|---|---|---|---|
| `sweep_unclaimed_probing_assets` | `probing`, no `worker_id`, no `ingest_started_at` | 10 min from `created_at` | Re-send. Nothing to release |
| `sweep_stuck_probing_assets` | `probing`, `ingest_started_at` stale | 20 min from `ingest_started_at` | Release, commit, re-send — or fail, past the ceiling |

The first is `sweep_stuck_queued_jobs`: the message never arrived. The second is
`sweep_stuck_running_jobs`: the worker took it and died.

**`ingest_started_at` is what makes the second one possible at all.** The
report-only version measured from `created_at`, which conflates "uploaded three
hours ago, a worker took it thirty seconds ago and is transcoding right now"
with "uploaded three hours ago and nothing ever touched it". Acting on that
distinction is exactly what would have cut a live transcode short. There is a
test for precisely this row — old on `created_at`, freshly claimed — asserting
both checks leave it alone.

`MAX_INGEST_ATTEMPTS = 5` bounds the whole thing, and is checked in **both**
queries: an asset that is released, re-sent and dies again alternates between
the two shapes, so a ceiling enforced on only one of them would never be
reached. There is a test for that too.

Five rather than the task's own `MAX_RETRIES = 3`, on purpose: those three are
one worker retrying itself over ninety seconds, and this is the outer bound
across every worker that ever touched the row.

---

## 6. Verification

Run, not reasoned about — the correction
[`17-first-real-test-run.md`](17-first-real-test-run.md) exists to make.

| | |
|---|---|
| Backend | **240 passed, 2 skipped** (231 before this pass) |
| New tests | 9 — 4 on the claim itself, 5 on the sweep's two new checks |
| `alembic check` | *No new upgrade operations detected* — the model and the migration agree |
| Migration round trip | `downgrade` then `upgrade` clean; columns, types and the partial index verified against `information_schema` |
| `openapi.json` | Unchanged. None of this reaches the contract |
| `ruff` · `ruff format` · `mypy app` | Clean |
| Frontend | 304 passed, untouched |

The tests worth knowing about, because they are the properties rather than the
plumbing:

* `test_only_one_worker_can_claim_an_asset` — two claims, one wins. The whole
  mechanism in three lines.
* `test_run_ingest_refuses_an_asset_another_worker_holds` — the same property
  in the terms that matter: two workers handed the same message do not both
  transcode the file. **This is the test that makes the sweep's re-send safe**,
  and therefore the reason this pass exists.
* `test_an_asset_a_worker_is_holding_is_not_treated_as_unclaimed` — the
  `created_at`/`ingest_started_at` distinction from §5.
* `test_an_infrastructure_blip_is_retried_not_failed` gained three assertions:
  the claim is released, and the attempt still counted.

---

## 7. What this changes for M5

The last thing named as "do it before export" is done. The render queue M5 adds
inherits `jobs`, which already had the claim, so nothing there needs this — but
export produces a **new asset** from a job, and every asset that enters
`probing` from now on is covered by a recovery that can actually act.

Still open before M5, unchanged: `make e2e`
([`17-first-real-test-run.md`](17-first-real-test-run.md) §5) and the LUT
problem ([`15-m5-readiness.md`](15-m5-readiness.md) §3).

---

*Build note · 27 August 2026*

"""Does the pipeline still agree with itself?

`reconciliation.py` asks whether the credit ledger still agrees with its own
cached balances, and answers by **reporting drift, never repairing it** — a
balance is evidence, and silently correcting it would destroy the evidence of
what went wrong.

This module asks a different question — *is anything stuck* — about a
different kind of state: `jobs.status` and `media_assets.status` are not a
financial record, they are **a claim about what a worker is doing right now**,
and a claim that has gone stale is not evidence to preserve, it is a job that
never started. So where the ledger reconciliation only reports, this module
**acts**, and only where acting is provably safe:

* **Stuck `queued` jobs** — re-sent to Celery. Safe because `JobRepository.claim`
  is `WHERE status='queued'`: a job already claimed matches nothing on a second
  claim attempt, so re-sending the message for a job that is not actually stuck
  is a harmless no-op, not a duplicate run.
* **Stuck `running` jobs** — put back to `queued` with `JobRepository.requeue`,
  then re-sent. The same idempotent claim covers the second half; `requeue`
  already exists and is exactly what `analysis_pipeline` calls on an ordinary
  `TransientFailureError` — this is the same recovery, for the case where the
  *worker process itself* disappeared and never got to raise anything.
* **Abandoned uploads** — a `pending_upload` asset old enough that its presigned
  URL has long expired has no worker watching it at all; nothing else will ever
  touch that row, so marking it `failed` is unambiguous.
* **Stuck `probing` assets** — released and re-sent, in the two shapes the job
  checks already have: one whose ingest message never arrived (never claimed),
  and one whose worker died holding the claim. Bounded by `MAX_INGEST_ATTEMPTS`,
  past which the asset is failed rather than resurrected again.

> **This last one was reported and not acted on until 27 August**, and the
> reason is worth keeping. `MediaAsset` had no atomic claim the way `Job` does
> — no `worker_id`, no guard on the update that starts processing, not even a
> column separating "when the upload was reserved" from "when a worker picked
> it up". Re-sending `process_asset.delay()` under those conditions could start
> a second worker transcoding the same file while the first was still running,
> racing the same row's `finish_ingest` write: a worse failure than the one
> being fixed. Migration `0003_media_asset_claim` gives the table the three
> columns `jobs` has, `run_ingest` now claims through
> `media.claim_for_ingest`, and the recovery below is safe for exactly the same
> reason the job re-sends are — an atomic claim makes a redundant message a
> no-op.

Found by an outside audit, 26 August 2026, of the class of bug that survives a
green test suite because nothing exercises the unhappy path: a broker
unreachable for one second, a worker process that dies between heartbeats. See
`docs/16-pipeline-reliability-notes.md`.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import AssetStatus, Job, JobStatus, MediaAsset
from app.repositories.job import requeue
from app.repositories.media import release_ingest_claim

log = get_logger(__name__)

#: A job with no `started_at` sitting this long in `queued` almost certainly
#: never reached Redis — a legitimate concurrency-cap wait is retried by the
#: task itself every 15 s (`analysis_pipeline.JobUnavailableError`), which
#: means a Celery message for it already exists and is alive. Ten minutes is
#: long enough that a real backlog is worth re-nudging anyway.
STUCK_QUEUED_AFTER = timedelta(minutes=10)

#: Generous next to every phase-1 tool's own timeout (`FFMPEG_TIMEOUT_SECONDS`
#: and friends are minutes, not tens of minutes). Revisit once export (M5)
#: ships — a render can legitimately run this long.
STUCK_RUNNING_AFTER = timedelta(minutes=30)

#: Outlives `settings.upload_url_ttl_seconds` (15 minutes) by a wide margin, so
#: a slow upload on a bad connection is never caught mid-flight.
ABANDONED_UPLOAD_AFTER = timedelta(hours=2)

#: A claimed asset whose worker has not been heard from since. Measured from
#: `ingest_started_at` — the column migration `0003` added precisely so this
#: could stop measuring from `created_at`, which conflated a slow upload with
#: a dead worker and was one of the two reasons this check could not act.
#: Generous: a 2 GB source on a busy worker is minutes of ffmpeg, and cutting
#: a live transcode short to start it again is the failure to avoid here.
STUCK_PROBING_AFTER = timedelta(minutes=20)

#: A `probing` asset nobody ever claimed — `complete_upload` committed and the
#: `process_asset` message never arrived. The mirror of `STUCK_QUEUED_AFTER`
#: and the same duration for the same reason: there is no worker retrying this
#: one in the background, so there is nothing to wait for.
UNCLAIMED_PROBING_AFTER = timedelta(minutes=10)

#: How many times the sweep will resurrect one asset before failing it. A file
#: that reliably kills its worker would otherwise be re-sent every five minutes
#: for ever, and each attempt costs a full download and transcode. Counted on
#: `ingest_attempts`, which `claim_for_ingest` increments and
#: `release_ingest_claim` deliberately does not reset.
#:
#: Above the task's own `MAX_RETRIES = 3`, on purpose: those three are one
#: worker retrying itself over ninety seconds, and this is the outer bound
#: across every worker that ever touched the row.
MAX_INGEST_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class PipelineSweepResult:
    requeued_jobs: list[uuid.UUID] = field(default_factory=list)
    failed_uploads: list[uuid.UUID] = field(default_factory=list)
    #: Stuck `probing` assets sent back for ingest. Acted on since 27 August —
    #: this was `stuck_probing`, a report, until `media_assets` grew a claim.
    requeued_assets: list[uuid.UUID] = field(default_factory=list)
    #: Assets past `MAX_INGEST_ATTEMPTS`, failed rather than resurrected again.
    failed_assets: list[uuid.UUID] = field(default_factory=list)

    @property
    def touched(self) -> int:
        return (
            len(self.requeued_jobs)
            + len(self.failed_uploads)
            + len(self.requeued_assets)
            + len(self.failed_assets)
        )


async def sweep_stuck_queued_jobs(session: AsyncSession, *, now: datetime) -> list[uuid.UUID]:
    """Jobs that look like the Celery send never happened at all."""
    rows = await session.execute(
        sa.select(Job.id, Job.priority).where(
            Job.status == JobStatus.QUEUED,
            Job.started_at.is_(None),
            Job.created_at < now - STUCK_QUEUED_AFTER,
        )
    )
    found = rows.all()
    if not found:
        return []

    from app.workers.tasks.analysis import run_analysis

    ids: list[uuid.UUID] = []
    for job_id, priority in found:
        run_analysis.apply_async(args=[str(job_id)], priority=priority)
        ids.append(job_id)
        log.warning("pipeline_sweep_requeued_stuck_queued", job_id=str(job_id))
    return ids


async def sweep_stuck_running_jobs(session: AsyncSession, *, now: datetime) -> list[uuid.UUID]:
    """Jobs whose worker went away without ever raising anything to catch.

    `task_acks_late` and `task_reject_on_worker_lost` cover the ordinary case —
    a worker process that dies mid-task redelivers the message. This is the
    residue: a broker disconnect, a killed container, anything that leaves a
    `running` row with nobody actually running it and no exception for
    `analysis_pipeline`'s own `except TransientFailureError: requeue` to catch.
    """
    rows = await session.execute(
        sa.select(Job.id, Job.priority).where(
            Job.status == JobStatus.RUNNING,
            Job.started_at < now - STUCK_RUNNING_AFTER,
        )
    )
    found = rows.all()
    if not found:
        return []

    from app.workers.tasks.analysis import run_analysis

    # Every row back to `queued` **and committed** before a single message goes
    # out. `claim()` is `WHERE status='queued'`, so a worker that picks up the
    # message before this commit is visible reads the row still `running`,
    # matches nothing, and gives up — the send is wasted and the job waits for
    # the next sweep.
    #
    # This is the same commit-before-enqueue rule `POST /jobs` documents and
    # `complete_upload` was fixed for on 26 August. It was worth writing down
    # twice: the first draft of *this function* had the two in the wrong order,
    # which is how easy the mistake is to make even while fixing it elsewhere.
    ids: list[uuid.UUID] = []
    for job_id, _priority in found:
        await requeue(session, job_id)
        ids.append(job_id)
    await session.commit()

    for job_id, priority in found:
        run_analysis.apply_async(args=[str(job_id)], priority=priority)
        log.warning("pipeline_sweep_requeued_stuck_running", job_id=str(job_id))
    return ids


async def sweep_abandoned_uploads(session: AsyncSession, *, now: datetime) -> list[uuid.UUID]:
    """Reservations nobody ever finished uploading against.

    Nothing watches a `pending_upload` row — no worker owns it, no task is
    dispatched for it until `POST /media/{id}/complete` is called. One that
    outlives its presigned URL by this much is never coming back.
    """
    result = await session.execute(
        sa.update(MediaAsset)
        .where(
            MediaAsset.status == AssetStatus.PENDING_UPLOAD,
            MediaAsset.created_at < now - ABANDONED_UPLOAD_AFTER,
        )
        .values(
            status=AssetStatus.FAILED,
            failure_reason="This upload was never completed and has expired.",
        )
        .returning(MediaAsset.id)
    )
    ids = [row[0] for row in result.all()]
    if ids:
        await session.commit()
        for asset_id in ids:
            log.warning("pipeline_sweep_failed_abandoned_upload", asset_id=str(asset_id))
    return ids


async def sweep_unclaimed_probing_assets(
    session: AsyncSession, *, now: datetime
) -> list[uuid.UUID]:
    """Assets whose ingest message never arrived at all.

    The asset analogue of `sweep_stuck_queued_jobs`, and the same signature on
    the row: `probing` — so `complete_upload`'s transaction committed — with no
    `worker_id` and no `ingest_started_at`, so no worker ever claimed it. That
    is a Celery send that failed, or a message dropped between the two.

    Nothing is written here. There is no claim to release on a row that was
    never claimed, so the whole recovery is the message itself, and
    `claim_for_ingest` is what makes sending it safe: an asset that was not
    actually stuck matches nothing and the second worker exits with
    `IngestUnavailableError`.
    """
    rows = await session.execute(
        sa.select(MediaAsset.id).where(
            MediaAsset.status == AssetStatus.PROBING,
            MediaAsset.worker_id.is_(None),
            MediaAsset.ingest_started_at.is_(None),
            MediaAsset.ingest_attempts < MAX_INGEST_ATTEMPTS,
            MediaAsset.deleted_at.is_(None),
            MediaAsset.created_at < now - UNCLAIMED_PROBING_AFTER,
        )
    )
    ids = [row[0] for row in rows.all()]
    if not ids:
        return []

    from app.workers.tasks.ingest import process_asset

    for asset_id in ids:
        process_asset.apply_async(args=[str(asset_id)])
        log.warning("pipeline_sweep_resent_unclaimed_ingest", asset_id=str(asset_id))
    return ids


async def sweep_stuck_probing_assets(
    session: AsyncSession, *, now: datetime
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """Assets whose worker took the claim and then disappeared.

    Returns `(requeued, failed)`. The asset analogue of
    `sweep_stuck_running_jobs`, down to the ordering: **every row released and
    committed before a single message goes out.** `claim_for_ingest` is
    `WHERE worker_id IS NULL`, so a worker that picks up the message before
    this commit is visible reads the row still claimed by the dead worker,
    matches nothing, and gives up — the send wasted and the asset waiting
    another five minutes. This is the third place that rule is written down;
    the first draft of `sweep_stuck_running_jobs` got it wrong.

    An asset past `MAX_INGEST_ATTEMPTS` is failed instead. Something about that
    file kills whatever picks it up, and a download-and-transcode every five
    minutes for ever is an expensive way to keep not finding that out.
    """
    rows = await session.execute(
        sa.select(MediaAsset.id, MediaAsset.ingest_attempts).where(
            MediaAsset.status == AssetStatus.PROBING,
            MediaAsset.ingest_started_at < now - STUCK_PROBING_AFTER,
            MediaAsset.deleted_at.is_(None),
        )
    )
    found = rows.all()
    if not found:
        return [], []

    from app.workers.tasks.ingest import process_asset

    requeue_ids = [row[0] for row in found if row[1] < MAX_INGEST_ATTEMPTS]
    exhausted = [row[0] for row in found if row[1] >= MAX_INGEST_ATTEMPTS]

    for asset_id in requeue_ids:
        await release_ingest_claim(session, asset_id)

    if exhausted:
        await session.execute(
            sa.update(MediaAsset)
            .where(MediaAsset.id.in_(exhausted), MediaAsset.status == AssetStatus.PROBING)
            .values(
                status=AssetStatus.FAILED,
                failure_reason="We could not prepare this file. Please try uploading it again.",
            )
        )

    await session.commit()

    for asset_id in requeue_ids:
        process_asset.apply_async(args=[str(asset_id)])
        log.warning("pipeline_sweep_requeued_stuck_ingest", asset_id=str(asset_id))
    for asset_id in exhausted:
        log.error("pipeline_sweep_ingest_attempts_exhausted", asset_id=str(asset_id))
    return requeue_ids, exhausted


async def _guarded[T](
    session: AsyncSession, name: str, check: Callable[[], Awaitable[T]]
) -> T | None:
    """Run one check; if it fails, log it and return None so the others run.

    This is a recovery job. A sweep that abandons four working checks because
    the fifth hit a lock timeout is a recovery job that stops recovering the
    first time anything goes wrong — which is the failure mode it exists to
    prevent, reproduced one level up.

    The rollback matters: a check that raised part-way through may have left
    the session dirty, and the next check shares it.

    **`None` rather than an empty default.** The checks no longer all answer in
    the same shape — one returns two lists — and taking the empty value as a
    parameter would mean either one shared mutable list handed to several
    fields of the frozen result, or a `cast` at every call site. `None` is the
    one value that fits every shape, and the caller says what nothing means for
    its own.
    """
    try:
        return await check()
    except Exception:
        log.exception("pipeline_sweep_check_failed", check=name)
        await session.rollback()
        return None


async def sweep(session: AsyncSession) -> PipelineSweepResult:
    """Run every check. Each is isolated: one failing does not stop the rest."""
    now = datetime.now(UTC)
    requeued = [
        *(
            await _guarded(
                session, "stuck_queued", lambda: sweep_stuck_queued_jobs(session, now=now)
            )
            or []
        ),
        *(
            await _guarded(
                session, "stuck_running", lambda: sweep_stuck_running_jobs(session, now=now)
            )
            or []
        ),
    ]
    failed_uploads = (
        await _guarded(
            session, "abandoned_uploads", lambda: sweep_abandoned_uploads(session, now=now)
        )
        or []
    )
    unclaimed = (
        await _guarded(
            session, "unclaimed_probing", lambda: sweep_unclaimed_probing_assets(session, now=now)
        )
        or []
    )
    probing = await _guarded(
        session, "stuck_probing", lambda: sweep_stuck_probing_assets(session, now=now)
    )
    # Spelled out rather than `or ([], [])`: this one answers with two lists,
    # and unpacking a fallback silently would hide which of them was empty.
    stuck_assets, exhausted = probing if probing is not None else ([], [])
    return PipelineSweepResult(
        requeued_jobs=requeued,
        failed_uploads=failed_uploads,
        requeued_assets=[*unclaimed, *stuck_assets],
        failed_assets=exhausted,
    )

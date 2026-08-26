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

**What this deliberately does not do: touch a stuck `probing` asset.**
`MediaAsset` has no atomic claim the way `Job` does — no `WHERE status='probing'`
guard, no `worker_id`, not even an `updated_at` column to measure staleness
precisely. Re-sending `process_asset.delay()` for one could start a second
worker transcoding the same file while the first is still running, racing the
same row's `finish_ingest` write. That needs `media_assets` to grow the same
claim mechanism `jobs` already has before it can be automated — until then this
only **reports** which assets look stuck, the same caution
`reconciliation.py` already applies to money. See the follow-up in
`docs/PHASE1-TASKS.md`.

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

#: `media_assets` has no `updated_at`, only `created_at` — this is measured
#: from when the row was *created*, which conflates upload time with probing
#: time. Report-only, and this imprecision is one more reason it stays that
#: way rather than acting on it.
STUCK_PROBING_AFTER = timedelta(minutes=20)


@dataclass(frozen=True, slots=True)
class PipelineSweepResult:
    requeued_jobs: list[uuid.UUID] = field(default_factory=list)
    failed_uploads: list[uuid.UUID] = field(default_factory=list)
    #: Reported, not acted on — see the module docstring.
    stuck_probing: list[uuid.UUID] = field(default_factory=list)

    @property
    def touched(self) -> int:
        return len(self.requeued_jobs) + len(self.failed_uploads)


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


async def report_stuck_probing_assets(session: AsyncSession, *, now: datetime) -> list[uuid.UUID]:
    """Log which assets look stuck. **Does not touch them** — see the module
    docstring for why re-triggering ingest is not safe yet."""
    rows = await session.execute(
        sa.select(MediaAsset.id).where(
            MediaAsset.status == AssetStatus.PROBING,
            MediaAsset.created_at < now - STUCK_PROBING_AFTER,
        )
    )
    ids = [row[0] for row in rows.all()]
    for asset_id in ids:
        log.warning("pipeline_sweep_asset_looks_stuck", asset_id=str(asset_id))
    return ids


async def _guarded(
    session: AsyncSession, name: str, check: Callable[[], Awaitable[list[uuid.UUID]]]
) -> list[uuid.UUID]:
    """Run one check; if it fails, log it and let the others still run.

    This is a recovery job. A sweep that abandons three working checks because
    the fourth hit a lock timeout is a recovery job that stops recovering the
    first time anything goes wrong — which is the failure mode it exists to
    prevent, reproduced one level up.

    The rollback matters: a check that raised part-way through may have left
    the session dirty, and the next check shares it.
    """
    try:
        return await check()
    except Exception:
        log.exception("pipeline_sweep_check_failed", check=name)
        await session.rollback()
        return []


async def sweep(session: AsyncSession) -> PipelineSweepResult:
    """Run every check. Each is isolated: one failing does not stop the rest."""
    now = datetime.now(UTC)
    requeued = [
        *await _guarded(session, "stuck_queued", lambda: sweep_stuck_queued_jobs(session, now=now)),
        *await _guarded(
            session, "stuck_running", lambda: sweep_stuck_running_jobs(session, now=now)
        ),
    ]
    failed_uploads = await _guarded(
        session, "abandoned_uploads", lambda: sweep_abandoned_uploads(session, now=now)
    )
    stuck_probing = await _guarded(
        session, "stuck_probing", lambda: report_stuck_probing_assets(session, now=now)
    )
    return PipelineSweepResult(
        requeued_jobs=requeued, failed_uploads=failed_uploads, stuck_probing=stuck_probing
    )

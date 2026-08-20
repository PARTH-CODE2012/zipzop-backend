"""Running an analysis job, from claim to settled.

Kept out of the Celery task for the same reason `ingest_pipeline` is: the task
owns retries and an event loop, and everything that can actually be wrong lives
here, where a test calls it directly with no broker in the way.

The shape every analysis tool shares:

    claim → download → work, publishing progress → store the result → settle

and the two failure paths that must never be confused. **A permanent failure is
a normal outcome**: unreadable media, no speech, no video track. It goes
straight to `failed`, refunds, and tells the user something they can act on.
A transient failure — S3 unreachable, the database down — is the queue's
problem, not the user's: the job goes back to `queued` with its reservation
untouched and Celery tries again.
"""

import json
import tempfile
import uuid
from pathlib import Path
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import Job, JobStatus, JobTool, User
from app.repositories import job as job_repo
from app.services import color_analysis, job_events, storage
from app.services.credits import CreditLedger
from app.services.plans import concurrency_for

log = get_logger(__name__)

#: Contract §6.3. Over this, the result goes to S3 and `result` is null.
#: Measured on the serialised JSON, which is what the client would receive —
#: not on the object, whose Python size says nothing about the payload.
INLINE_RESULT_LIMIT_BYTES: Final = 256 * 1024


class TransientFailureError(Exception):
    """Infrastructure. Worth trying again, so it must not reach the user."""


class JobUnavailableError(Exception):
    """Someone else has it, it is cancelled, or the user is at their cap.

    Not a failure — there is nothing to refund and nothing to tell anyone. The
    task returns quietly, and in the concurrency case retries later.
    """

    def __init__(self, reason: str, *, retry_in_seconds: int | None = None) -> None:
        self.reason = reason
        self.retry_in_seconds = retry_in_seconds
        super().__init__(reason)


async def run_analysis(session: AsyncSession, job_id: uuid.UUID, *, worker_id: str) -> str:
    """Do the work. Returns the job's final status.

    Never raises for bad media — that is a `failed` row with a sentence the user
    can read. Only infrastructure raises, as `TransientFailureError`.
    """
    job = await session.get(Job, job_id)
    if job is None:
        # The API commits before it enqueues, so this means the row is genuinely
        # gone — or, if the ordering is ever changed back, that we are ahead of
        # the commit. Either way the task's own retry is the right answer, and
        # `TransientFailureError` is how it asks for one.
        raise TransientFailureError(f"job {job_id} is not visible yet")

    user = await session.get(User, job.user_id)
    if user is None:
        raise JobUnavailableError("the account is gone")

    limits = concurrency_for(await _plan_code(session, job.user_id))
    limit = limits.get(job.family.value, 1)

    claimed = await job_repo.claim(session, job_id, concurrency_limit=limit, worker_id=worker_id)
    if claimed is None:
        await session.rollback()
        current = await session.get(Job, job_id)
        if current is not None and current.status is JobStatus.QUEUED:
            # Still queued means the cap turned it away, not another worker.
            # Contract §5.3: it waits for a slot, it does not error.
            raise JobUnavailableError("at the plan's concurrency cap", retry_in_seconds=15)
        raise JobUnavailableError("already claimed or no longer runnable")

    await session.commit()
    await _publish(job, JobStatus.RUNNING, 5)

    try:
        result = await _work(session, job)
    except color_analysis.AnalysisFailedError as exc:
        return await _fail(session, job, code="UNSUPPORTED_MEDIA", message=str(exc))
    except TransientFailureError:
        await job_repo.requeue(session, job.id)
        await session.commit()
        raise
    except Exception as exc:
        # An unexpected exception is a bug, and a bug must not silently charge
        # the user for work that produced nothing.
        log.exception("job_crashed", job_id=str(job.id))
        return await _fail(session, job, code="INTERNAL", message=str(exc)[:500])

    if await job_repo.cancellation_requested(session, job.id):
        # Cancelled while we worked. The credits went back when it was
        # cancelled; finishing now would hand over the result for free.
        log.info("job_cancelled_mid_flight", job_id=str(job.id))
        return JobStatus.CANCELLED.value

    await _store_result(session, job, result)
    await session.commit()
    await _publish(job, JobStatus.SUCCEEDED, 100)
    log.info("job_succeeded", job_id=str(job.id), tool=job.tool.value)
    return JobStatus.SUCCEEDED.value


# --------------------------------------------------------------------------
# The work itself
# --------------------------------------------------------------------------


async def _work(session: AsyncSession, job: Job) -> dict[str, Any]:
    if job.tool is JobTool.COLOR_ANALYSIS:
        return await _color_analysis(session, job)
    # captions and smart_trim land next — see docs/10-m4-readiness.md §4 for
    # why colour analysis is the one that proves the pipeline first.
    raise color_analysis.AnalysisFailedError(f"{job.tool.value} is not implemented yet")


async def _color_analysis(session: AsyncSession, job: Job) -> dict[str, Any]:
    from app.models import MediaAsset

    asset_uuid = uuid.UUID(str(job.input["assetId"]).removeprefix("ast_"))
    asset = await session.get(MediaAsset, asset_uuid)
    if asset is None:
        raise color_analysis.AnalysisFailedError("That file is no longer available.")

    # The proxy, not the original: it is 480p, already normalised, and the
    # answer to "what colour is this" does not change with resolution. On a 4K
    # source it is the difference between seconds and minutes.
    key = asset.proxy_key or asset.storage_key
    duration_ms = int(job.input.get("analyzedDurationMs") or asset.duration_ms or 0)

    with tempfile.TemporaryDirectory(prefix="zipzop-analysis-") as workspace:
        source = Path(workspace) / "source"
        try:
            storage.download(key, str(source))
        except Exception as exc:
            raise TransientFailureError(f"could not fetch {key}") from exc

        await _progress(session, job, 35)
        stats = color_analysis.sample_frames(source, duration_ms)
        await _progress(session, job, 80)

    return color_analysis.recommend(stats, preferred_look=job.input.get("preferredLook"))


# --------------------------------------------------------------------------
# Finishing
# --------------------------------------------------------------------------


async def _store_result(session: AsyncSession, job: Job, result: dict[str, Any]) -> None:
    """Inline under 256 KB, in S3 above it — contract §6.3.

    The split is not an optimisation. A caption run on an hour of speech is
    megabytes of JSON, and putting that in a JSONB column makes every `GET
    /jobs` that touches the row pay for it.
    """
    payload = json.dumps(result, separators=(",", ":")).encode()
    if len(payload) <= INLINE_RESULT_LIMIT_BYTES:
        await job_repo.succeed(session, job.id, result=result)
        return

    key = f"results/{job.user_id}/{job.id}/result.json"
    try:
        storage.put_bytes(key, payload, "application/json")
    except Exception as exc:
        raise TransientFailureError("could not store the result") from exc
    await job_repo.succeed(session, job.id, result_key=key)


async def _fail(session: AsyncSession, job: Job, *, code: str, message: str) -> str:
    """Mark it failed and give the credits back, in one transaction.

    *"A failure on our side never costs the user anything, automatically,
    without anyone contacting support."* — §5.4. The refund reads the job's own
    reserve rows, so it returns to the buckets it took from.
    """
    await job_repo.fail(session, job.id, error_code=code, error_message=message)

    ledger = CreditLedger(session)
    locked = await ledger.lock_user(job.user_id)
    if locked is not None:
        await ledger.refund(
            user=locked,
            job_id=job.id,
            period_started_at=await job_repo.period_start_for(session, job.user_id),
            job_created_at=job.created_at,
        )
    await session.commit()
    await _publish(job, JobStatus.FAILED, job.progress, error={"code": code, "message": message})
    log.info("job_failed", job_id=str(job.id), code=code)
    return JobStatus.FAILED.value


async def _progress(session: AsyncSession, job: Job, percent: int) -> None:
    """A checkpoint, not a timer. Published at points that mean something —
    "the file is here", "the frames are read" — because a bar that advances on
    a clock is a bar that lies about what is happening."""
    await job_repo.set_progress(session, job.id, percent)
    await session.commit()
    await _publish(job, JobStatus.RUNNING, percent)


async def _publish(
    job: Job, status: JobStatus, progress: int, error: dict[str, str] | None = None
) -> None:
    await job_events.publish(
        user_id=job.user_id,
        job_id=job.id,
        tool=job.tool,
        status=status,
        progress=progress,
        clip_id=job.input.get("clipId"),
        error=error,
    )


async def _plan_code(session: AsyncSession, user_id: uuid.UUID) -> Any:
    import sqlalchemy as sa

    from app.models import PlanCode, Subscription, SubStatus

    plan = await session.scalar(
        sa.select(Subscription.plan).where(
            Subscription.user_id == user_id,
            Subscription.status.in_([SubStatus.ACTIVE, SubStatus.PAST_DUE]),
        )
    )
    return plan or PlanCode.FREE

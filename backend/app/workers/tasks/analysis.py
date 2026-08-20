"""Analysis queue: captions, smart trim, colour analysis.

A thin wrapper, like `ingest`. The task owns three things and nothing else — an
event loop, its own database session, and the retry policy — and every decision
about the work is in `app.services.analysis_pipeline`, where a test can drive it
without a broker.

**The retry policy is the part worth reading.** Bad media is not retried: the
pipeline turns it into a `failed` row with a sentence the user can read, refunds
the credits, and returns normally. Only `TransientFailureError` gets here as an
exception, and only that is tried again.
"""

import asyncio
import uuid
from typing import Any

from app.logging import get_logger
from app.workers.celery_app import celery_app

log = get_logger(__name__)

#: Three attempts, backing off. An S3 blip clears in seconds; a database
#: failover takes a minute. Beyond that, something is wrong that retrying will
#: not fix, and the job should fail visibly rather than sit in the queue.
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = (10, 30, 90)


@celery_app.task(
    name="app.workers.tasks.analysis.run_analysis",
    bind=True,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def run_analysis(self: Any, job_id: str) -> dict[str, Any]:
    from app.services.analysis_pipeline import JobUnavailableError, TransientFailureError

    try:
        status = asyncio.run(_run(uuid.UUID(job_id), worker_id=self.request.hostname or "worker"))
    except JobUnavailableError as unavailable:
        if unavailable.retry_in_seconds is not None:
            # At the plan's concurrency cap. The job stays `queued` and starts
            # when a slot frees — the contract promises the client never sees
            # "try again later", so the waiting happens here.
            log.info("job_waiting_for_slot", job_id=job_id, reason=unavailable.reason)
            raise self.retry(countdown=unavailable.retry_in_seconds, max_retries=None) from None
        log.info("job_not_claimed", job_id=job_id, reason=unavailable.reason)
        return {"jobId": job_id, "status": "skipped", "reason": unavailable.reason}
    except TransientFailureError as transient:
        attempt = self.request.retries
        if attempt >= MAX_RETRIES:
            # Out of attempts. Fail it properly so the credits go back rather
            # than leaving a job queued for ever with the user's balance held.
            log.error("job_transient_exhausted", job_id=job_id, error=str(transient))
            asyncio.run(_give_up(uuid.UUID(job_id), str(transient)))
            return {"jobId": job_id, "status": "failed"}
        delay = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
        log.warning("job_retrying", job_id=job_id, attempt=attempt + 1, in_seconds=delay)
        raise self.retry(countdown=delay, exc=transient) from None

    return {"jobId": job_id, "status": status}


async def _run(job_id: uuid.UUID, *, worker_id: str) -> str:
    # Its own session on its own engine — see `worker_session`'s docstring for
    # why a pooled connection cannot cross the loops `asyncio.run` creates.
    from app.db import worker_session
    from app.services.analysis_pipeline import run_analysis as run

    async with worker_session() as session:
        try:
            return await run(session, job_id, worker_id=worker_id)
        except Exception:
            await session.rollback()
            raise


async def _give_up(job_id: uuid.UUID, reason: str) -> None:
    """Last attempt spent. Fail and refund, so nothing is left holding credits."""
    from app.db import worker_session
    from app.models import Job
    from app.services.analysis_pipeline import _fail

    async with worker_session() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return
        await _fail(
            session,
            job,
            code="INTERNAL",
            message=f"We could not complete this job: {reason}",
        )

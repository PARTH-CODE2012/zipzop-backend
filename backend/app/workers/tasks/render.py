"""Render queue: export.

A thin wrapper, like `analysis` and `ingest` — an event loop, its own database
session, and the retry policy. Every decision about the render is in
`app.services.render_pipeline`, where a test can drive it without a broker.

**Its own task and its own queue, not a branch inside `run_analysis`.** A render
holds a worker for minutes and saturates a CPU; an analysis job is seconds of
ffprobe. Sharing a queue means one export blocking every caption behind it, and
the queue routing in `celery_app.py` has separated `render` from `analysis`
since M4 precisely so this could exist without that trade.
"""

import asyncio
import uuid
from typing import Any

from app.logging import get_logger
from app.workers.celery_app import celery_app

log = get_logger(__name__)

#: Fewer attempts than analysis, and deliberately. A retry costs a full
#: transcode of the whole timeline, so three of them on a genuinely broken
#: source is an hour of CPU spent learning nothing. Anything that is not a
#: storage blip fails permanently on the first attempt.
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = (15, 60)


@celery_app.task(
    name="app.workers.tasks.render.run_export",
    bind=True,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def run_export(self: Any, job_id: str) -> dict[str, Any]:
    from app.services.analysis_pipeline import JobUnavailableError, TransientFailureError

    try:
        status = asyncio.run(_run(uuid.UUID(job_id), worker_id=self.request.hostname or "worker"))
    except JobUnavailableError as unavailable:
        if unavailable.retry_in_seconds is not None:
            log.info("export_waiting_for_slot", job_id=job_id, reason=unavailable.reason)
            raise self.retry(countdown=unavailable.retry_in_seconds, max_retries=None) from None
        log.info("export_not_claimed", job_id=job_id, reason=unavailable.reason)
        return {"jobId": job_id, "status": "skipped", "reason": unavailable.reason}
    except TransientFailureError as transient:
        attempt = self.request.retries
        if attempt >= MAX_RETRIES:
            log.error("export_transient_exhausted", job_id=job_id, error=str(transient))
            asyncio.run(_give_up(uuid.UUID(job_id), str(transient)))
            return {"jobId": job_id, "status": "failed"}
        delay = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
        log.warning("export_retrying", job_id=job_id, attempt=attempt + 1, in_seconds=delay)
        raise self.retry(countdown=delay, exc=transient) from None

    return {"jobId": job_id, "status": status}


async def _run(job_id: uuid.UUID, *, worker_id: str) -> str:
    from app.db import worker_session
    from app.services.render_pipeline import run_export as run

    async with worker_session() as session:
        try:
            return await run(session, job_id, worker_id=worker_id)
        except Exception:
            await session.rollback()
            raise


async def _give_up(job_id: uuid.UUID, reason: str) -> None:
    """Out of attempts. Fail it properly so the credits go back rather than the
    balance staying held against a job nobody is working."""
    from app.db import worker_session
    from app.models import Job
    from app.services.render_pipeline import _fail

    async with worker_session() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return
        await _fail(session, job, code="RENDER_FAILED", message=reason[:500])

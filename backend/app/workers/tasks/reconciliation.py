"""The pipeline sweep — Celery's side of `app.services.pipeline_reconciliation`.

A thin wrapper, like every other task module: an event loop and a session, the
decisions are in the service. Scheduled far more often than the nightly ledger
reconciliation, because this is about a job or an upload sitting stuck for a
person waiting on it right now, not an overnight audit of the books.
"""

import asyncio
from typing import Any

from app.logging import get_logger
from app.workers.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="app.workers.tasks.reconciliation.sweep_pipeline")
def sweep_pipeline() -> dict[str, Any]:
    result = asyncio.run(_run())
    if result.touched or result.stuck_probing:
        log.warning(
            "pipeline_sweep_ran",
            requeued_jobs=len(result.requeued_jobs),
            failed_uploads=len(result.failed_uploads),
            stuck_probing_reported=len(result.stuck_probing),
        )
    return {
        "requeuedJobs": [str(i) for i in result.requeued_jobs],
        "failedUploads": [str(i) for i in result.failed_uploads],
        "stuckProbingReported": [str(i) for i in result.stuck_probing],
    }


async def _run() -> Any:
    from app.db import worker_session
    from app.services.pipeline_reconciliation import sweep

    async with worker_session() as session:
        return await sweep(session)

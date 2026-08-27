"""Ingest queue.

Probe with ffprobe, generate a 480p proxy, a thumbnail and waveform peaks. The
work itself is in `app.services.ingest_pipeline` — this module owns only the
queue's concerns: an event loop, its own database session, and the retry
policy.

**The retry policy is the part worth reading**, and it mirrors
`app.workers.tasks.analysis` exactly, for the same reason: bad media is not
retried — `run_ingest` turns it into a `failed` row with a sentence the user
can read and returns normally. Only `TransientFailureError` gets here as an
exception, and only that is tried again.

**This used to be decorative.** `process_asset` declared `max_retries=2`, but
`run_ingest` caught every exception — including S3 blips and dropped database
connections — and wrote `failed` immediately, so no code path ever called
`self.retry()`. Found by audit, 26 August 2026; fixed by giving
`ingest_pipeline` the same `TransientFailureError` shape `analysis_pipeline`
already had.
"""

import asyncio
import uuid
from typing import Any

from app.logging import get_logger
from app.workers.celery_app import celery_app

log = get_logger(__name__)

#: Three attempts, backing off. Matches `app.workers.tasks.analysis` — an S3
#: blip clears in seconds, a database failover takes a minute, and beyond that
#: something is wrong that retrying will not fix.
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = (10, 30, 90)


@celery_app.task(
    name="app.workers.tasks.ingest.process_asset",
    bind=True,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def process_asset(self: Any, asset_id: str) -> dict[str, Any]:
    """Turn an uploaded object into a usable asset."""
    from app.services.ingest_pipeline import IngestUnavailableError, TransientFailureError

    try:
        worker_id = self.request.hostname or "worker"
        status = asyncio.run(_run(uuid.UUID(asset_id), worker_id=worker_id))
    except IngestUnavailableError as unavailable:
        # Somebody else has it, or it already finished. The ordinary answer to
        # a redelivered message and to a sweep re-send that turned out not to
        # be needed — `claim_for_ingest` matching nothing is the mechanism
        # working, not failing. Retrying would only lose the same race again.
        log.info("ingest_unavailable", asset_id=asset_id, reason=str(unavailable))
        return {"assetId": asset_id, "status": "unavailable"}
    except TransientFailureError as transient:
        attempt = self.request.retries
        if attempt >= MAX_RETRIES:
            # Out of attempts. Fail it properly so the media bin shows a
            # message instead of spinning forever on a job nobody is working.
            log.error("ingest_transient_exhausted", asset_id=asset_id, error=str(transient))
            asyncio.run(_give_up(uuid.UUID(asset_id), str(transient)))
            return {"assetId": asset_id, "status": "failed"}
        delay = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
        log.warning("ingest_retrying", asset_id=asset_id, attempt=attempt + 1, in_seconds=delay)
        raise self.retry(countdown=delay, exc=transient) from None

    return {"assetId": asset_id, "status": status}


async def _run(asset_id: uuid.UUID, *, worker_id: str) -> str:
    # A worker process is not a request: it opens and commits its own session
    # rather than borrowing the API's dependency — and on its own engine, which
    # `worker_session` disposes with the loop this `asyncio.run` created. See
    # the note there for what happens otherwise.
    from app.db import worker_session
    from app.services.ingest_pipeline import run_ingest

    async with worker_session() as session:
        try:
            status = await run_ingest(session, asset_id, worker_id=worker_id)
            await session.commit()
            return status
        except Exception:
            await session.rollback()
            raise


async def _give_up(asset_id: uuid.UUID, reason: str) -> None:
    """Last attempt spent. Fail the asset so nothing is left `probing` forever."""
    from app.db import worker_session
    from app.models import MediaAsset
    from app.repositories.media import fail_ingest

    async with worker_session() as session:
        asset = await session.get(MediaAsset, asset_id)
        if asset is None:
            return
        await fail_ingest(
            session, asset_id, "We could not prepare this file. Please try uploading it again."
        )
        await session.commit()


@celery_app.task(name="app.workers.tasks.ingest.ping")
def ping(message: str = "pong") -> dict[str, Any]:
    """No-op task. Proves API → Redis → worker → result works.

        make up
        cd backend && ./.venv/bin/python -c \
          "from app.workers.tasks.ingest import ping; print(ping.delay('hi').get(timeout=10))"
    """
    log.info("ping", message=message)
    return {"ok": True, "message": message}


@celery_app.task(name="app.workers.tasks.ingest.sweep_expired_media")
def sweep_expired_media() -> dict[str, Any]:
    """Storage lifecycle: drop exports past their expiry, scratch past a day.

    Storage is the largest recurring cost in the system and the only one that
    grows on its own. Retention policy is still open — see the decision
    register in docs/01-product-vision.md §12.
    """
    log.info("storage sweep — not implemented yet")
    return {"deleted": 0}

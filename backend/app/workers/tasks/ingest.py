"""Ingest queue.

Probe with ffprobe, generate a 480p proxy, a thumbnail and waveform peaks. The
work itself is in `app.services.ingest_pipeline` — this module owns only the
queue's concerns: an event loop, its own database session, and retries.
"""

import asyncio
import uuid
from typing import Any

from app.logging import get_logger
from app.workers.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(
    name="app.workers.tasks.ingest.process_asset",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def process_asset(self: Any, asset_id: str) -> dict[str, Any]:
    """Turn an uploaded object into a usable asset.

    Bad media is **not** retried: `run_ingest` catches it, writes a reason the
    user can read, and returns `failed`. Only an infrastructure failure — S3
    unreachable, database down — gets here as an exception, and those are worth
    trying again.
    """
    status = asyncio.run(_run(uuid.UUID(asset_id)))
    return {"assetId": asset_id, "status": status}


async def _run(asset_id: uuid.UUID) -> str:
    # A worker process is not a request: it opens and commits its own session
    # rather than borrowing the API's dependency — and on its own engine, which
    # `worker_session` disposes with the loop this `asyncio.run` created. See
    # the note there for what happens otherwise.
    from app.db import worker_session
    from app.services.ingest_pipeline import run_ingest

    async with worker_session() as session:
        try:
            status = await run_ingest(session, asset_id)
            await session.commit()
            return status
        except Exception:
            await session.rollback()
            raise


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

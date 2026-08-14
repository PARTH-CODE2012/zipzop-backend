"""Ingest queue.

Real work arrives in M2: probe with ffprobe, generate a 480p proxy, a
thumbnail and waveform peaks. Until then, `ping` proves the chain end to end.
"""

from typing import Any

from app.logging import get_logger
from app.workers.celery_app import celery_app

log = get_logger(__name__)


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

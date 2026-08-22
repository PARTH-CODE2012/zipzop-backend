"""Billing queue.

Real work arrives in M6. The scheduled entries exist now so beat has something
to bind to and the schedule itself can be verified early.
"""

import asyncio
from typing import Any

from app.logging import get_logger
from app.workers.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="app.workers.tasks.billing.sweep_renewals")
def sweep_renewals() -> dict[str, Any]:
    """Hourly safety net for subscription renewal.

    The provider's webhook is the primary path and fires within seconds of
    payment. This exists because webhooks get lost, and a user whose allowance
    silently failed to renew is a support ticket we should never receive. It is
    also the only path free users have — they have no provider and no webhook.

    Idempotent by period boundary: firing after the webhook is a no-op.
    See docs/03-backend-architecture.md §8.4.
    """
    log.info("renewal sweep — not implemented yet")
    return {"renewed": 0}


@celery_app.task(name="app.workers.tasks.billing.reconcile_ledger")
def reconcile_ledger() -> dict[str, Any]:
    """Re-sum `credit_ledger` per user per bucket, against the cached balances.

    **If this reports drift, a transaction boundary is wrong** — a balance was
    written without its ledger row, or a row without its balance. It reports and
    does not repair: correcting silently would hide the bug that caused the
    drift and destroy the evidence of how much was lost and to whom.

    Logged at `error` with the accounts named, because this is a page for a
    person rather than a line in a dashboard nobody reads.
    """
    result = asyncio.run(_reconcile())
    if result["drift"]:
        log.error(
            "ledger_drift",
            drift=result["drift"],
            checked=result["checked"],
            accounts=result["accounts"][:20],
        )
    else:
        log.info("ledger_reconciled", checked=result["checked"])
    return result


async def _reconcile() -> dict[str, Any]:
    from app.db import worker_session
    from app.services.reconciliation import reconcile

    async with worker_session() as session:
        outcome = await reconcile(session)
        return {
            "checked": outcome.users_checked,
            "drift": len(outcome.drifts),
            "accounts": [
                {
                    "userId": str(d.user_id),
                    "bucket": d.bucket.value,
                    "cached": d.cached,
                    "ledger": d.from_ledger,
                    "difference": d.difference,
                }
                for d in outcome.drifts
            ],
        }

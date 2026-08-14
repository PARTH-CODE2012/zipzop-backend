"""Billing queue.

Real work arrives in M6. The scheduled entries exist now so beat has something
to bind to and the schedule itself can be verified early.
"""

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
    """Re-sum credit_ledger per user per bucket and compare to the cached
    balances on users.

    If this ever reports drift, a transaction boundary is wrong somewhere —
    a balance was written without its ledger row, or vice versa. It should
    alert, not just log.
    """
    log.info("ledger reconciliation — not implemented yet")
    return {"checked": 0, "drift": 0}

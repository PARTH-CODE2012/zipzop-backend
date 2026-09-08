"""Billing queue: the renewal safety net, and webhook processing.

Two paths, and the split between them is the shape of
docs/03-backend-architecture.md §8.4. The provider's webhook is the **primary**
path and fires within seconds of payment; `sweep_renewals` is the safety net for
the ones that never arrive — they do get lost — and it is also **the only path
free users have**, since they have no provider and no webhook at all.

Both are idempotent through the period-boundary check in
`billing/service.py`, so the sweep firing after a webhook has already granted
the period is a no-op rather than a second month.
"""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from celery import Task

from app.logging import get_logger
from app.models import PaymentProvider, ProviderEvent, Subscription
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
    """
    return asyncio.run(_sweep_renewals())


async def _sweep_renewals() -> dict[str, Any]:
    """**One subscription per transaction**, deliberately.

    A single transaction over five hundred accounts means one unexpected row
    rolls back four hundred and ninety-nine correct renewals, and the failure is
    then indistinguishable from the sweep never having run at all. Per-row
    commits cost more round trips and are worth it: money that has moved stays
    moved, and the one account that failed is named in the log rather than
    hidden behind the others.
    """
    from app.db import worker_session
    from app.services.billing.service import due_for_renewal, renew_one

    tally: dict[str, int] = {}
    failures = 0
    now = datetime.now(UTC)

    async with worker_session() as session:
        due = await due_for_renewal(session, now=now)

    for subscription in due:
        async with worker_session() as session:
            fresh = await session.get(Subscription, subscription.id)
            if fresh is None:  # pragma: no cover - removed between the two reads
                continue
            try:
                outcome = await renew_one(session, subscription=fresh, now=now)
                await session.commit()
                tally[outcome] = tally.get(outcome, 0) + 1
            except Exception as exc:
                await session.rollback()
                failures += 1
                log.error(
                    "renewal_failed",
                    subscription_id=str(subscription.id),
                    user_id=str(subscription.user_id),
                    error=str(exc),
                )

    result: dict[str, Any] = {
        "due": len(due),
        "renewed": tally.get("renewed", 0),
        "cancelled": tally.get("cancelled", 0),
        "downgraded": tally.get("downgraded", 0),
        "already_granted": tally.get("already_granted", 0),
        "failed": failures,
    }
    log.info("renewal_sweep", **result)
    return result


@celery_app.task(
    name="app.workers.tasks.billing.process_provider_event",
    bind=True,
    max_retries=5,
    default_retry_delay=60,
)
def process_provider_event(self: Task, provider: str, event_id: str) -> dict[str, Any]:
    """Apply one stored webhook delivery — step 3 of §8.5.

    The route has already verified the signature, stored the row and answered
    `200`. This runs here precisely so a slow database query cannot make the
    provider believe the delivery failed and retry it.

    Retried with a delay rather than dropped: the row is stored, so a redelivery
    from the provider would be discarded as a duplicate and would *not* be a
    second chance. If this task gives up, nothing else will pick the event up —
    which is why it retries five times and logs loudly when it stops.
    """
    try:
        return asyncio.run(_process_provider_event(provider, event_id))
    except Exception as exc:
        log.error("provider_event_failed", provider=provider, event_id=event_id, error=str(exc))
        raise self.retry(exc=exc) from exc


async def _process_provider_event(provider: str, event_id: str) -> dict[str, Any]:
    """**Reads the row rather than trusting its arguments.**

    The message carries only a key; the payload comes from `provider_events`,
    which is the record. A message that arrives before its own transaction is
    visible, or twice, or after a redeploy, all resolve to the same row or to
    nothing — and nothing is safe, because the hourly sweep is behind it.
    """
    from app.db import worker_session
    from app.services.billing.providers.base import WebhookEvent
    from app.services.billing.providers.razorpay import RazorpayProvider
    from app.services.billing.service import apply_event

    async with worker_session() as session:
        key = (PaymentProvider(provider), event_id)
        stored = await session.get(ProviderEvent, key)
        if stored is None:
            log.warning("provider_event_missing", provider=provider, event_id=event_id)
            return {"applied": False, "reason": "not_stored"}

        if stored.processed_at is not None:
            # Belt and braces. The ledger's own unique indexes are what actually
            # stop a double grant; this stops the work being done at all.
            return {"applied": False, "reason": "already_processed"}

        adapter = RazorpayProvider()
        event: WebhookEvent = adapter.parse_webhook(
            raw_body=json.dumps(stored.payload).encode(), headers={}
        )
        # The stored row is the authority on identity. `event_id` came from a
        # header the payload does not carry, so re-parsing loses it and it has
        # to be put back — otherwise a retry would compute a body digest and
        # look like a different event.
        event = replace(event, event_id=stored.event_id)

        try:
            outcome = await apply_event(session, event)
        except Exception as exc:
            await session.rollback()
            await _record_failure(provider, event_id, exc)
            raise

        stored.processed_at = datetime.now(UTC)
        stored.error = None
        await session.commit()

        log.info(
            "provider_event_applied",
            provider=provider,
            event_type=stored.event_type,
            action=outcome.action,
            user_id=outcome.user_id,
        )
        return {
            "applied": outcome.action not in ("ignored", "unattributed"),
            **{"action": outcome.action},
        }


async def _record_failure(provider: str, event_id: str, exc: Exception) -> None:
    """Write why it failed onto the event, in its own transaction.

    The processing transaction has just been rolled back, so the note cannot go
    in it. Without this, a repeatedly failing event looks identical to one that
    was never delivered — and the difference is the whole of the diagnosis.
    """
    from app.db import worker_session

    try:
        async with worker_session() as session:
            stored = await session.get(ProviderEvent, (PaymentProvider(provider), event_id))
            if stored is not None:
                stored.error = f"{type(exc).__name__}: {exc}"[:2000]
                await session.commit()
    except Exception:  # pragma: no cover - the database is what just failed
        log.error("provider_event_failure_note_not_written", event_id=event_id)


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

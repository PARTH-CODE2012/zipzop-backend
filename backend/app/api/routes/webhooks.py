"""Server-to-server callbacks from the payment providers.

**Two unauthenticated routes**, listed in contract §7 only so nobody is
surprised by them. The signature is the entire defence: an unverified billing
callback is a way to grant yourself a plan for free
(docs/07-security.md §6.7).

The order in `03-backend-architecture.md` §8.5 is not a suggestion, and every
step is here for a failure that has happened to somebody:

1. **Verify the signature before parsing anything.** A mis-signed request is
   dropped with `400` and logged.
2. **Insert into `provider_events`.** A duplicate collides on
   `(provider, event_id)`; acknowledge and stop. Never process twice.
3. **Acknowledge immediately, process asynchronously.** Both providers treat a
   slow response as a failure and retry — so a handler that did the work inline
   would turn one slow database query into a second charge attempt.
4. Out-of-order delivery is normal; a grant for a period already granted is
   dropped rather than doubled. That check lives in the service.
"""

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import Session, client_ip
from app.api.errors import APIError
from app.logging import get_logger
from app.models import PaymentProvider, ProviderEvent
from app.services import rate_limit
from app.services.billing import service
from app.services.billing.providers.base import ProviderError, SignatureError

log = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

#: Excluded from the normal rate limiter — a provider is not a client and its
#: retries are legitimate — but limited by source IP, because the route is
#: public and unauthenticated. Generous enough that a real burst of deliveries
#: after an outage gets through.
WEBHOOK_RATE_LIMIT = 600
WEBHOOK_RATE_WINDOW_SECONDS = 60


class WebhookRejectedError(APIError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "WEBHOOK_REJECTED"
    message = "This callback could not be verified."


async def _webhook_rate_limit(request: Request) -> None:
    ip = client_ip(request)
    verdict = await rate_limit.hit(
        f"webhook:{ip}", limit=WEBHOOK_RATE_LIMIT, window_seconds=WEBHOOK_RATE_WINDOW_SECONDS
    )
    if not verdict.allowed:
        log.warning("webhook_rate_limited", ip=ip)
        raise WebhookRejectedError("Too many callbacks from this address.")


async def _handle(provider: PaymentProvider, request: Request, session: Session) -> Response:
    try:
        adapter = service.adapter_for(provider)
    except ProviderError as exc:
        # A provider we route nothing to and have no adapter for. `400` and not
        # `500`: there is nothing wrong here that a retry would fix, and a 500
        # would have the provider redelivering for days.
        log.warning("webhook_for_unimplemented_provider", provider=provider.value)
        raise WebhookRejectedError("That provider is not configured here.") from exc

    headers = dict(request.headers)

    # **The raw bytes, not the parsed body.** Both providers sign the exact
    # octets they sent; a dict that has been through a JSON round trip is a
    # different byte string with the same meaning, and it will not verify.
    raw = await request.body()

    try:
        adapter.verify_signature(raw_body=raw, headers=headers)
    except SignatureError as exc:
        # An attack, or a misconfiguration that looks exactly like one. Logged
        # with the address and nothing from the body — the body is unverified,
        # so putting it in the log is letting an attacker write to it.
        log.warning(
            "webhook_signature_rejected",
            provider=provider.value,
            ip=client_ip(request),
            detail=exc.detail,
        )
        raise WebhookRejectedError() from exc

    try:
        event = adapter.parse_webhook(raw_body=raw, headers=headers)
    except ProviderError as exc:
        # Verified but unreadable. `400` rather than `500`: a 500 reads as our
        # outage and is retried for days, and a body we could not parse the
        # first time will not parse on the ninth.
        log.error("webhook_unparseable", provider=provider.value, detail=exc.detail)
        raise WebhookRejectedError("This callback could not be read.") from exc

    try:
        # **`add` inside the savepoint, not before it.** An object added outside
        # is not discarded when the savepoint rolls back — it stays pending, and
        # the commit `get_session` performs at the end of the request retries the
        # same doomed INSERT and raises `PendingRollbackError`.
        #
        # On this route that turns the correct answer into the worst one: a
        # redelivery would return `500` instead of `200`, the provider would read
        # that as a failure and retry, and every retry would `500` again — a loop
        # that ends only when somebody notices. Found with a probe against a real
        # database rather than by a test, because the suite overrides
        # `get_session` and so never runs the commit that fails.
        async with session.begin_nested():
            session.add(
                ProviderEvent(
                    provider=event.provider,
                    event_id=event.event_id,
                    event_type=event.event_type,
                    payload=event.payload,
                )
            )
            await session.flush()
    except IntegrityError:
        # A redelivery. This is the idempotency, and it is the database's rather
        # than ours — which is why the insert comes before the processing.
        log.info("webhook_duplicate_dropped", provider=provider.value, event_id=event.event_id)
        return Response(status_code=status.HTTP_200_OK)

    # Committed here and not by the dependency's own commit at the end of the
    # request: the Celery message below must not be sent for an event that a
    # later failure rolls back, and a worker must not pick up a row the
    # transaction has not yet made visible. The same ordering M4's job enqueue
    # settled on, for the same reason (docs/16-pipeline-reliability-notes.md).
    await session.commit()

    _enqueue(event.provider, event.event_id)
    return Response(status_code=status.HTTP_200_OK)


def _enqueue(provider: PaymentProvider, event_id: str) -> None:
    """Hand the stored event to a worker, and never fail the response over it.

    A send that fails leaves a row in `provider_events` with `processed_at`
    still NULL, which the hourly renewal sweep and the reconciliation both see.
    Returning `500` instead would make the provider retry — and the retry would
    be dropped as a duplicate, because the event *is* stored. The row is the
    record; the message is only a nudge.
    """
    try:
        from app.workers.tasks.billing import process_provider_event

        process_provider_event.apply_async(args=[provider.value, event_id])
    except Exception as exc:  # pragma: no cover - broker down
        log.error(
            "webhook_enqueue_failed",
            provider=provider.value,
            event_id=event_id,
            error=str(exc),
        )


@router.post(
    "/razorpay",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(_webhook_rate_limit)],
    summary="Razorpay server-to-server callback",
)
async def razorpay_webhook(request: Request, session: Session) -> Response:
    return await _handle(PaymentProvider.RAZORPAY, request, session)


@router.post(
    "/stripe",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(_webhook_rate_limit)],
    summary="Stripe server-to-server callback",
)
async def stripe_webhook(request: Request, session: Session) -> Response:
    """Listed because the contract lists it. Stripe is deferred, not dropped —
    there is no adapter yet, so this refuses rather than pretending."""
    return await _handle(PaymentProvider.STRIPE, request, session)

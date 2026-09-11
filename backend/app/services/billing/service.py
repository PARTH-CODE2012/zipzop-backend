"""Billing, with no idea which provider anyone came through.

Everything that is *not* provider-specific lives here: what a plan grants, when
a period ends, which bucket credits land in, what a cancellation costs the user.
`billing/providers/` translates the outside world into the vocabulary in
`base.py`; this module is everything that happens next
(docs/03-backend-architecture.md §8.1).

**Balances are never written here.** Every credit movement goes through
`CreditLedger`, which is the one place the cached balances on `users` are
touched — that is what makes the nightly reconciliation's alarm mean something.
This module decides *that* a grant happens; `credits.py` performs it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import (
    CommissionLedgerEntry,
    CommissionReason,
    Payment,
    PaymentKind,
    PaymentProvider,
    PaymentStatus,
    Plan,
    PlanCode,
    PromoCode,
    ProviderPlan,
    Subscription,
    SubStatus,
    User,
)
from app.services.billing import catalogue, routing
from app.services.billing.providers.base import (
    BillingEventKind,
    BillingProvider,
    CheckoutSession,
    PortalSession,
    ProviderError,
    WebhookEvent,
)
from app.services.billing.providers.razorpay import RazorpayProvider
from app.services.credits import CreditLedger
from app.services.periods import add_a_month

log = get_logger(__name__)

#: Two periods granted within this of each other are the same period arriving
#: twice — a webhook and the sweep racing, or a redelivery. §8.5 step 4.
SAME_PERIOD_TOLERANCE = timedelta(hours=1)


# --------------------------------------------------------------------------
# Adapters
# --------------------------------------------------------------------------

#: Only implemented providers appear here. Stripe is deferred, not dropped — the
#: interface was designed for two and adding it is a file plus a line.
_ADAPTERS: Final[dict[PaymentProvider, type[BillingProvider]]] = {
    PaymentProvider.RAZORPAY: RazorpayProvider,
}


def adapter_for(provider: PaymentProvider) -> BillingProvider:
    """The adapter, or a refusal the route can turn into a readable error.

    A provider that is routed to but not implemented is a configuration
    mistake, and it fails here rather than at a redirect the customer has
    already followed.
    """
    factory = _ADAPTERS.get(provider)
    if factory is None:
        raise ProviderError(
            "That payment method is not available yet.",
            detail=f"no adapter implemented for {provider.value}",
        )
    return factory()


def adapter_for_currency(currency: str) -> BillingProvider:
    return adapter_for(routing.provider_for_currency(currency))


# --------------------------------------------------------------------------
# The plan catalogue — GET /plans
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanOffer:
    code: str
    display_name: str
    price_minor: int
    currency: str
    monthly_credits: int
    approx_videos_per_month: int
    facemap_seconds: int
    max_export_height: int
    watermark: str
    queue_label: str


async def public_plans(session: AsyncSession, *, currency: str) -> list[PlanOffer]:
    """Every plan a stranger may buy, cheapest first.

    🔴 **Filtered on `is_public`, and that filter is the point.** The column has
    existed since migration `0002` and nothing read it until this function.
    Retiring the `beta` plan when the Discord campaign ends is one boolean —
    the row stays, everyone on it keeps it, and the price list stops offering it
    (docs/13-mvp-direction.md §3.2). An endpoint that ignored the column would
    make that retirement a no-op, and it would be discovered on the day it was
    needed.

    A plan with no price in the requested currency is dropped rather than shown
    at zero: "Business — free" is worse than one fewer row.
    """
    rows = (
        (
            await session.execute(
                sa.select(Plan)
                .where(Plan.is_public.is_(True))
                .order_by(sa.func.coalesce(Plan.price_usd_cents, 0), Plan.monthly_credits)
            )
        )
        .scalars()
        .all()
    )

    offers: list[PlanOffer] = []
    for plan in rows:
        price = catalogue.price_minor(plan, currency)
        if price is None:
            continue
        offers.append(
            PlanOffer(
                code=plan.code.value,
                display_name=plan.display_name,
                price_minor=price,
                currency=currency.upper(),
                monthly_credits=plan.monthly_credits,
                approx_videos_per_month=catalogue.approx_videos_per_month(plan.monthly_credits),
                facemap_seconds=plan.facemap_seconds,
                max_export_height=plan.max_export_height,
                watermark=plan.watermark.value,
                queue_label=catalogue.queue_label(plan.queue_priority),
            )
        )
    return offers


# --------------------------------------------------------------------------
# Checkout
# --------------------------------------------------------------------------


async def _provider_plan_id(
    session: AsyncSession,
    adapter: BillingProvider,
    *,
    plan: Plan,
    currency: str,
    amount_minor: int,
) -> str:
    """The provider's own id for this plan, created once and remembered.

    Cached in `provider_plans` rather than fetched every time: creating the
    same plan twice on the provider's side leaves two ids for one price, and
    subscriptions split across them are a reconciliation problem nobody enjoys.
    """
    existing = await session.get(ProviderPlan, (adapter.provider, plan.code, currency.upper()))
    if existing is not None:
        return existing.provider_plan_id

    provider_plan_id = await adapter.ensure_plan(
        plan=plan.code,
        display_name=plan.display_name,
        currency=currency.upper(),
        amount_minor=amount_minor,
    )
    session.add(
        ProviderPlan(
            provider=adapter.provider,
            plan_code=plan.code,
            currency=currency.upper(),
            provider_plan_id=provider_plan_id,
        )
    )
    await session.flush()
    return provider_plan_id


async def start_subscription_checkout(
    session: AsyncSession,
    *,
    user: User,
    plan: Plan,
    currency: str,
    return_url: str,
    idempotency_key: str,
) -> CheckoutSession:
    """Create the hosted page, and record the intent **before** the redirect.

    The pending `payments` row is not bookkeeping for its own sake. When the
    webhook arrives, the `notes` we attached are the primary way to tell whose
    subscription it is — and notes have been known to come back empty. The row
    keyed on `(provider, provider_payment_id)` is the fallback, and it is the
    difference between a paid customer and a support ticket nobody can resolve.

    It also makes checkout idempotent in the database rather than in Redis: a
    second attempt that produces the same provider reference collides on the
    unique index instead of creating a second subscription.
    """
    amount = catalogue.price_minor(plan, currency)
    if amount is None or amount <= 0:
        raise ProviderError(
            "That plan cannot be bought in this currency.",
            detail=f"{plan.code.value} has no price in {currency}",
        )

    adapter = adapter_for_currency(currency)
    provider_plan_id = await _provider_plan_id(
        session, adapter, plan=plan, currency=currency, amount_minor=amount
    )

    session_out = await adapter.create_checkout(
        user_id=str(user.id),
        email=user.email,
        plan=plan.code,
        currency=currency.upper(),
        amount_minor=amount,
        return_url=return_url,
        idempotency_key=idempotency_key,
        provider_plan_id=provider_plan_id,
    )

    session.add(
        Payment(
            user_id=user.id,
            provider=adapter.provider,
            provider_payment_id=session_out.provider_reference,
            kind=PaymentKind.SUBSCRIPTION,
            status=PaymentStatus.PENDING,
            amount_minor=amount,
            currency=currency.upper(),
        )
    )
    await session.flush()
    return session_out


async def start_topup_checkout(
    session: AsyncSession,
    *,
    user: User,
    pack: catalogue.TopupPack,
    currency: str,
    return_url: str,
    idempotency_key: str,
) -> CheckoutSession:
    adapter = adapter_for_currency(currency)
    amount = pack.price_minor(currency)

    session_out = await adapter.create_topup(
        user_id=str(user.id),
        email=user.email,
        pack_code=pack.code,
        credits=pack.credits,
        currency=currency.upper(),
        amount_minor=amount,
        return_url=return_url,
        idempotency_key=idempotency_key,
    )

    session.add(
        Payment(
            user_id=user.id,
            provider=adapter.provider,
            provider_payment_id=session_out.provider_reference,
            kind=PaymentKind.TOPUP,
            status=PaymentStatus.PENDING,
            amount_minor=amount,
            currency=currency.upper(),
            credits_granted=None,  # written when the payment actually lands
        )
    )
    await session.flush()
    return session_out


async def open_portal(session: AsyncSession, *, user: User, return_url: str) -> PortalSession:
    subscription = await live_subscription(session, user.id)
    if subscription is None or subscription.provider is None:
        # A free user has no provider and nothing to manage. Not an error: the
        # client shows the plans instead, which is what they wanted.
        return PortalSession(url=None, reason="There is no paid subscription to manage.")
    adapter = adapter_for(subscription.provider)
    return await adapter.create_portal_session(
        provider_customer_id=subscription.provider_customer_id, return_url=return_url
    )


# --------------------------------------------------------------------------
# Cancellation
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Cancellation:
    """What the user is about to give up, in the words they will read.

    Contract §7: *"The response is written to be shown to the user before they
    confirm. Losing 1,840 credits is worth knowing about in advance, and saying
    so plainly at the moment of cancelling is both fairer and better retention
    than discovering it a week later."*
    """

    plan: str
    status: str
    cancel_at_period_end: bool
    access_until: datetime
    credits_lost_at_period_end: dict[str, int]
    credits_kept: dict[str, int]


async def cancel(session: AsyncSession, *, user: User, at_period_end: bool) -> Cancellation:
    """Stop the subscription renewing. Access runs to the end of the period.

    **Our row is updated whether or not the provider answers.** A provider that
    is briefly unreachable must not stop somebody cancelling: the alternative is
    a customer who tried to cancel, was told it failed, and is charged again.
    The renewal sweep reads our row, so a failed provider call means one refund
    at worst, and a cancel button that does not work means a chargeback and a
    review.
    """
    subscription = await live_subscription(session, user.id)
    if subscription is None:
        raise ProviderError("There is no subscription to cancel.")

    if subscription.provider is not None and subscription.provider_subscription_id:
        try:
            adapter = adapter_for(subscription.provider)
            await adapter.cancel_subscription(
                provider_subscription_id=subscription.provider_subscription_id,
                at_period_end=at_period_end,
            )
        except ProviderError as exc:
            # Logged loudly: the provider still believes it should charge, and
            # somebody has to reconcile that. The user's intent is honoured
            # regardless.
            log.error(
                "cancel_not_acknowledged_by_provider",
                user_id=str(user.id),
                provider=subscription.provider.value,
                subscription=subscription.provider_subscription_id,
                detail=exc.detail,
            )

    # **Access always runs to `current_period_end`, whichever was asked for.**
    # The month is paid for; taking it away early would be keeping the money and
    # withdrawing the service. `atPeriodEnd=false` changes what the *provider*
    # is told — stop now rather than at the cycle end — not what the customer
    # keeps. Contract §7's `accessUntil` is the same date either way, which is
    # why it is in the response.
    subscription.cancel_at_period_end = True
    await session.flush()

    return Cancellation(
        plan=subscription.plan.value,
        status=subscription.status.value,
        cancel_at_period_end=True,
        access_until=subscription.current_period_end,
        # `plan` and `facemap` are swept at the boundary; `topup` was bought
        # separately and survives cancellation, plan changes and rollovers.
        credits_lost_at_period_end={
            "plan": user.plan_credits,
            "facemapSeconds": user.facemap_seconds,
        },
        credits_kept={"topup": user.topup_credits},
    )


# --------------------------------------------------------------------------
# The lifecycle — grants, renewals, dunning
# --------------------------------------------------------------------------


async def live_subscription(session: AsyncSession, user_id: uuid.UUID) -> Subscription | None:
    result = await session.execute(
        sa.select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.status.in_([SubStatus.ACTIVE, SubStatus.PAST_DUE]),
        )
    )
    return result.scalar_one_or_none()


def _already_granted(subscription: Subscription, plan: PlanCode, period_start: datetime) -> bool:
    """Has **this plan** already been paid out for **this period**?

    Out-of-order delivery is normal and both providers retry for days, so a
    grant for a period we have already granted is dropped rather than doubled
    (§8.5 step 4). The tolerance absorbs the difference between the provider's
    clock and ours; without it the hourly sweep and the webhook would each grant
    the same month a few seconds apart.

    🔴 **The plan is half of the question, and leaving it out broke the launch
    path.** Every account is given a subscription at registration, with a period
    starting *now* — so for a free account this compared the incoming period
    against one that began seconds ago, decided the month was already granted,
    and dropped it. Somebody arriving from a Discord announcement, creating an
    account and subscribing straight away paid and stayed on free. The hourly
    sweep could not rescue them either: it only looks at subscriptions whose
    period has *ended*, which was a month away.

    Found by signing a webhook with the real key and watching a brand-new
    account not get what it paid for. Every test in the suite built an account
    whose period had started thirty-one days earlier, so none of them could see
    it.

    A change of plan is never a repeat: `free → beta` is a purchase, and so is
    `beta → pro` mid-period. Only the same plan over the same period is the
    duplicate this guards against.
    """
    if subscription.plan is not plan:
        return False
    current = subscription.current_period_start
    if current.tzinfo is None:  # pragma: no cover - the column is timezone-aware
        current = current.replace(tzinfo=UTC)
    return period_start <= current + SAME_PERIOD_TOLERANCE


async def grant_period(
    session: AsyncSession,
    *,
    user: User,
    subscription: Subscription,
    plan: Plan,
    period_start: datetime,
    period_end: datetime,
    note: str,
) -> bool:
    """Sweep, grant, move the period. One transaction — §8.4.

    Returns `False` when this period was already granted, which is the normal
    outcome of the safety-net sweep firing after the webhook has done the work.

    🔴 `topup` is not named anywhere in this path. Those credits were bought and
    never expire, and a renewal that swept them would be taking money already
    paid.
    """
    if _already_granted(subscription, plan.code, period_start):
        return False

    ledger = CreditLedger(session)
    moved = await ledger.roll_period(
        user=user,
        plan_credits=plan.monthly_credits,
        facemap_seconds=plan.facemap_seconds,
        note=note,
    )

    subscription.plan = plan.code
    subscription.pending_plan = None
    subscription.current_period_start = period_start
    subscription.current_period_end = period_end
    subscription.status = SubStatus.ACTIVE
    await session.flush()

    log.info(
        "period_granted",
        user_id=str(user.id),
        plan=plan.code.value,
        period_start=period_start.isoformat(),
        **moved,
    )
    return True


async def apply_upgrade(
    session: AsyncSession, *, user: User, subscription: Subscription, plan: Plan
) -> None:
    """§8.3: upgrades apply immediately and grant the difference.

    The difference, not the allowance: someone who upgrades having spent half of
    Pro's credits should not be handed Business's full month on top of what is
    left, which would make upgrading twice in a month a way to print credits.
    """
    await CreditLedger(session).grant_upgrade_difference(
        user=user,
        plan_credits=plan.monthly_credits,
        facemap_seconds=plan.facemap_seconds,
        note=f"upgrade to {plan.code.value}",
    )
    subscription.plan = plan.code
    subscription.pending_plan = None
    await session.flush()


async def schedule_downgrade(
    session: AsyncSession, *, subscription: Subscription, plan: PlanCode
) -> None:
    """§8.3: downgrades apply at the next boundary, so nobody loses credits
    they are half way through using."""
    subscription.pending_plan = plan
    await session.flush()


async def mark_past_due(session: AsyncSession, *, subscription: Subscription) -> None:
    """A failed renewal. **Not a cancellation.**

    The provider retries on its own schedule and we keep the plan live during
    that window (§8.3): a first decline is usually an expired card rather than
    an unwilling customer, and cutting service off is how a recoverable payment
    becomes a lost one.
    """
    subscription.status = SubStatus.PAST_DUE
    await session.flush()


async def _plan_or_none(session: AsyncSession, code: PlanCode | None) -> Plan | None:
    return await session.get(Plan, code) if code is not None else None


async def _ensure_subscription(
    session: AsyncSession,
    *,
    user: User,
    plan: PlanCode,
    provider: PaymentProvider,
    provider_subscription_id: str | None,
    currency: str | None,
) -> Subscription:
    """The user's live subscription, moved onto this provider and plan.

    Everyone has one from registration, including free users — uniform rows are
    why the renewal path has no special case for the free tier.
    """
    subscription = await live_subscription(session, user.id)
    if subscription is None:  # pragma: no cover - registration always creates one
        now = datetime.now(UTC)
        subscription = Subscription(
            user_id=user.id,
            plan=plan,
            status=SubStatus.ACTIVE,
            current_period_start=now,
            current_period_end=add_a_month(now),
        )
        session.add(subscription)
        await session.flush()

    subscription.provider = provider
    if provider_subscription_id:
        subscription.provider_subscription_id = provider_subscription_id
    if currency:
        subscription.currency = currency.upper()
    return subscription


# --------------------------------------------------------------------------
# Webhook events
# --------------------------------------------------------------------------


@dataclass
class EventOutcome:
    """What a delivery did, for the log and for the tests."""

    action: str
    user_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


async def _user_for_event(session: AsyncSession, event: WebhookEvent) -> User | None:
    """Whose event is this?

    Two routes, in order of reliability:

    1. the `user_id` we attached at checkout and got back in `notes`;
    2. the pending `payments` row we wrote **before** the redirect, matched on
       the provider's own reference.

    The second exists because the first has a known failure mode — notes coming
    back empty — and a paid customer we cannot identify is the worst outcome on
    this path.
    """
    if event.user_id:
        try:
            found = await session.get(User, uuid.UUID(event.user_id))
        except ValueError:
            found = None
        if found is not None:
            return found

    for reference in (event.subscription_reference, event.payment_reference):
        if not reference:
            continue
        payment = await session.scalar(
            sa.select(Payment).where(
                Payment.provider == event.provider,
                Payment.provider_payment_id == reference,
            )
        )
        if payment is not None:
            return await session.get(User, payment.user_id)
    return None


async def apply_event(session: AsyncSession, event: WebhookEvent) -> EventOutcome:
    """Turn a verified delivery into ledger rows. Called from the worker.

    Never from the request handler: both providers treat a slow response as a
    failure and retry, so the route stores the event and returns `200`
    immediately (§8.5 step 3).
    """
    if event.kind is BillingEventKind.IGNORED:
        return EventOutcome(action="ignored", details={"event": event.event_type})

    user = await _user_for_event(session, event)
    if user is None:
        # Stored, acknowledged, and loud. This is money we have taken and cannot
        # attribute; it needs a person, not a retry.
        log.error(
            "billing_event_names_no_user",
            provider=event.provider.value,
            event_type=event.event_type,
            subscription=event.subscription_reference,
            payment=event.payment_reference,
        )
        return EventOutcome(action="unattributed", details={"event": event.event_type})

    locked = await CreditLedger(session).lock_user(user.id)
    user = locked if locked is not None else user

    if event.kind is BillingEventKind.TOPUP_PAID:
        return await _apply_topup(session, user=user, event=event)

    subscription = await _ensure_subscription(
        session,
        user=user,
        plan=event.plan or PlanCode.FREE,
        provider=event.provider,
        provider_subscription_id=event.subscription_reference,
        currency=event.currency,
    )

    if event.kind is BillingEventKind.SUBSCRIPTION_PAYMENT_FAILED:
        await mark_past_due(session, subscription=subscription)
        return EventOutcome(action="past_due", user_id=str(user.id))

    if event.kind is BillingEventKind.SUBSCRIPTION_CANCELLED:
        subscription.cancel_at_period_end = True
        await session.flush()
        return EventOutcome(action="cancel_scheduled", user_id=str(user.id))

    if event.kind is BillingEventKind.SUBSCRIPTION_ACTIVATED:
        # A mandate authorised. No money has necessarily moved, so this is not a
        # grant — `subscription.charged` is.
        subscription.status = SubStatus.ACTIVE
        await session.flush()
        return EventOutcome(action="activated", user_id=str(user.id))

    # SUBSCRIPTION_CHARGED — the grant path.
    plan = await _plan_or_none(session, event.plan) or await session.get(Plan, subscription.plan)
    if plan is None:  # pragma: no cover - the FK guarantees a plan
        return EventOutcome(action="unknown_plan", user_id=str(user.id))

    period_start = event.period_start or datetime.now(UTC)
    period_end = event.period_end or add_a_month(period_start)

    payment = await _record_payment(session, user=user, event=event, subscription=subscription)
    granted = await grant_period(
        session,
        user=user,
        subscription=subscription,
        plan=plan,
        period_start=period_start,
        period_end=period_end,
        note=f"{event.provider.value} {event.event_type}",
    )
    if not granted:
        return EventOutcome(action="already_granted", user_id=str(user.id))

    await accrue_commission(session, user=user, payment=payment)
    return EventOutcome(action="granted", user_id=str(user.id), details={"plan": plan.code.value})


async def _record_payment(
    session: AsyncSession,
    *,
    user: User,
    event: WebhookEvent,
    subscription: Subscription | None,
) -> Payment | None:
    """Settle the pending row, or write one the checkout never got to.

    A payment that arrives without a pending row is not an error — the sweep can
    reach a renewal the checkout knew nothing about — so this upserts rather
    than assuming.
    """
    reference = event.payment_reference or event.subscription_reference
    if not reference:
        return None

    payment = await session.scalar(
        sa.select(Payment).where(
            Payment.provider == event.provider, Payment.provider_payment_id == reference
        )
    )
    if payment is None:
        payment = Payment(
            user_id=user.id,
            provider=event.provider,
            provider_payment_id=reference,
            kind=PaymentKind.SUBSCRIPTION,
            status=PaymentStatus.SUCCEEDED,
            amount_minor=event.amount_minor or 0,
            currency=(event.currency or "USD").upper(),
        )
        session.add(payment)
    else:
        payment.status = PaymentStatus.SUCCEEDED
        if event.amount_minor:
            payment.amount_minor = event.amount_minor

    payment.settled_at = datetime.now(UTC)
    if subscription is not None:
        payment.subscription_id = subscription.id
    await session.flush()
    return payment


async def _apply_topup(session: AsyncSession, *, user: User, event: WebhookEvent) -> EventOutcome:
    """Credits that were bought outright.

    The pack is read from the notes we attached at checkout, not from the amount
    paid: an amount can be matched to the wrong pack by a currency conversion or
    a partial payment, and granting the wrong number of credits is worse than
    granting none.
    """
    pack_code = event.notes.get("pack")
    pack = catalogue.pack(pack_code) if pack_code else None
    if pack is None:
        log.error(
            "topup_without_a_known_pack",
            user_id=str(user.id),
            pack=pack_code,
            payment=event.payment_reference,
        )
        return EventOutcome(action="unknown_pack", user_id=str(user.id))

    payment = await _record_payment(session, user=user, event=event, subscription=None)
    if payment is not None:
        if payment.credits_granted:
            # A redelivery. The credits are already in the account, and the
            # unique index on `(provider, provider_payment_id)` is what made
            # this the same row rather than a second one.
            return EventOutcome(action="already_granted", user_id=str(user.id))
        payment.kind = PaymentKind.TOPUP
        payment.credits_granted = pack.credits

    await CreditLedger(session).grant_topup(
        user=user,
        credits=pack.credits,
        payment_id=payment.id if payment is not None else None,
        note=f"top-up {pack.code}",
    )
    return EventOutcome(
        action="topup_granted", user_id=str(user.id), details={"credits": pack.credits}
    )


# --------------------------------------------------------------------------
# Commission
# --------------------------------------------------------------------------


async def accrue_commission(session: AsyncSession, *, user: User, payment: Payment | None) -> int:
    """What the Discord server owner earned from this payment.

    **Recomputed on every renewal**, not once at sign-up: a commission paid once
    on a recurring product misaligns the owner's incentive from month two
    (docs/13-mvp-direction.md §6).

    The rate is stored on the row it was computed at, so changing the rate later
    cannot silently restate what was already earned. A redelivery collides on
    `(payment_id, reason)` and is swallowed — the same defence the credit ledger
    uses against a double refund, and for the same reason: code that relies on
    remembering to check is code that will one day forget.
    """
    if payment is None or not user.promo_code:
        return 0

    code = await session.get(PromoCode, user.promo_code)
    if code is None or not code.is_active:
        return 0

    amount = payment.amount_minor * code.commission_bps // 10_000
    if amount <= 0:
        return 0

    try:
        # **`add` inside the savepoint, not before it.** An object added outside
        # is not discarded when the savepoint rolls back: it stays pending, the
        # next flush retries the same doomed INSERT, and the *enclosing*
        # transaction dies with `PendingRollbackError` — far away from here, in
        # whatever ran next.
        #
        # The visible symptom was worse than it sounds. A redelivered webhook
        # would log "already accrued", correctly, and then fail the whole event
        # transaction; the task would retry five times, fail five times, and the
        # event would be marked failed forever — for a redelivery that had
        # nothing left to do.
        async with session.begin_nested():
            session.add(
                CommissionLedgerEntry(
                    code=code.code,
                    user_id=user.id,
                    payment_id=payment.id,
                    reason=CommissionReason.ACCRUAL,
                    amount_minor=amount,
                    currency=payment.currency,
                    rate_bps=code.commission_bps,
                )
            )
            await session.flush()
    except IntegrityError:
        # Already accrued for this payment. The webhook was redelivered, which
        # is normal and is exactly what the unique index is for.
        log.info("commission_already_accrued", payment_id=str(payment.id))
        return 0

    log.info(
        "commission_accrued",
        code=code.code,
        amount_minor=amount,
        currency=payment.currency,
        user_id=str(user.id),
    )
    return amount


# --------------------------------------------------------------------------
# The safety net — the hourly sweep
# --------------------------------------------------------------------------


async def due_for_renewal(session: AsyncSession, *, now: datetime) -> list[Subscription]:
    """Subscriptions whose period has ended and not been renewed.

    Ordered and bounded so one bad row cannot make the sweep unbounded work.
    """
    result = await session.execute(
        sa.select(Subscription)
        .where(
            Subscription.status.in_([SubStatus.ACTIVE, SubStatus.PAST_DUE]),
            Subscription.current_period_end <= now,
        )
        .order_by(Subscription.current_period_end)
        .limit(500)
    )
    return list(result.scalars().all())


async def renew_one(session: AsyncSession, *, subscription: Subscription, now: datetime) -> str:
    """One subscription's boundary. Returns what happened, for the sweep's tally.

    Three outcomes, and the middle one is the reason this function is not
    simply "grant the next month":

    * **cancelled** — `cancel_at_period_end` was set, so the account drops to
      `free` and the `plan` and `facemap` balances are swept. `topup` survives.
    * **downgraded** — a `pending_plan` was scheduled, and the boundary is where
      it applies (§8.3).
    * **renewed** — the ordinary case, including every free user, who has no
      provider and no webhook and is renewed entirely from here.
    """
    user = await CreditLedger(session).lock_user(subscription.user_id)
    if user is None:  # pragma: no cover - FK cascade removes the subscription too
        return "orphaned"

    period_start = now
    period_end = add_a_month(now)

    if subscription.cancel_at_period_end and subscription.plan is not PlanCode.FREE:
        free = await session.get(Plan, PlanCode.FREE)
        assert free is not None  # seeded by migration 0002
        subscription.status = SubStatus.ACTIVE
        subscription.provider = None
        subscription.provider_subscription_id = None
        subscription.cancel_at_period_end = False
        await grant_period(
            session,
            user=user,
            subscription=subscription,
            plan=free,
            period_start=period_start,
            period_end=period_end,
            note="subscription cancelled; back to free",
        )
        return "cancelled"

    target = subscription.pending_plan or subscription.plan
    plan = await session.get(Plan, target)
    if plan is None:  # pragma: no cover - FK guarantees it
        return "unknown_plan"

    outcome = "downgraded" if subscription.pending_plan else "renewed"
    granted = await grant_period(
        session,
        user=user,
        subscription=subscription,
        plan=plan,
        period_start=period_start,
        period_end=period_end,
        note="renewal sweep",
    )
    return outcome if granted else "already_granted"

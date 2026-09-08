"""Plans, checkout, the portal, cancellation and the ledger — contract §7.

**The client never touches card details.** Every payment happens on the
provider's own hosted page, reached through a redirect, which keeps card data
entirely out of this system — the only sane position to hold.

**And the redirect is never proof of payment.** The subscription activates when
the provider's webhook arrives, usually within seconds. On return the client
polls `GET /me` behind a "confirming your payment" state, because a user can
land on `returnUrl` by pressing back.
"""

import base64
import binascii
import json
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request, status

from app.api import ids
from app.api.deps import CurrentUser, Session, general_rate_limit
from app.api.errors import (
    APIError,
    CheckoutFailedError,
    NotFoundError,
    PlanLimitExceededError,
)
from app.api.schemas.billing import (
    CancelRequest,
    CancelResponse,
    CheckoutRequest,
    CheckoutResponse,
    LedgerEntryOut,
    LedgerResponse,
    PlanOfferOut,
    PlansResponse,
    PortalRequest,
    PortalResponse,
    PromoPreviewResponse,
    PromoStatsResponse,
    TopupPackOut,
    TopupPacksResponse,
    TopupRequest,
)
from app.logging import get_logger
from app.models import CreditLedgerEntry, Plan, PlanCode, PromoCode
from app.services import promo
from app.services.billing import catalogue, routing, service
from app.services.billing.providers.base import ProviderError

log = get_logger(__name__)

router = APIRouter(tags=["billing"])

#: Contract §7 shows `?limit=50`. Bounded so a client cannot ask for the whole
#: ledger of an account that has been running jobs for a year.
LEDGER_DEFAULT_LIMIT = 50
LEDGER_MAX_LIMIT = 200


class UnsupportedCurrencyError(APIError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "UNSUPPORTED_CURRENCY"
    message = "We cannot take payment in that currency."


class NoSubscriptionError(APIError):
    status_code = status.HTTP_409_CONFLICT
    code = "NO_SUBSCRIPTION"
    message = "There is no subscription to change."


def _currency(request: Request, requested: str | None) -> str:
    code = routing.normalise_currency(requested, dict(request.headers))
    if code is None:
        raise UnsupportedCurrencyError(
            f"We take payment in {' and '.join(routing.SUPPORTED_CURRENCIES)}.",
            details={"supported": list(routing.SUPPORTED_CURRENCIES)},
        )
    return code


# --------------------------------------------------------------------------
# The pricing table
# --------------------------------------------------------------------------


@router.get(
    "/plans",
    response_model=PlansResponse,
    dependencies=[Depends(general_rate_limit)],
    summary="The plans anyone can buy",
)
async def list_plans(
    request: Request,
    session: Session,
    currency: Annotated[str | None, Query(max_length=3)] = None,
) -> PlansResponse:
    """Public — no authentication.

    Deliberately so: a pricing page that needs an account to show a price asks
    people to sign up before they know what it costs.

    `suggestedCurrency` comes from whatever the edge network worked out about
    the caller's country. **It is a suggestion, not a decision** — the client
    must let the user change it with `?currency=`, because VPNs, travellers and
    expatriates make IP unreliable.
    """
    code = _currency(request, currency)
    offers = await service.public_plans(session, currency=code)
    return PlansResponse(
        suggested_currency=routing.suggest_currency(dict(request.headers)),
        suggested_provider=routing.provider_for_currency(code).value,
        plans=[PlanOfferOut.model_validate(offer, from_attributes=True) for offer in offers],
    )


@router.get(
    "/billing/topup-packs",
    response_model=TopupPacksResponse,
    dependencies=[Depends(general_rate_limit)],
    summary="Credit packs that never expire",
)
async def list_topup_packs(
    request: Request,
    currency: Annotated[str | None, Query(max_length=3)] = None,
) -> TopupPacksResponse:
    """What `POST /billing/topup` will accept.

    Contract §7 names `credits_5000` in its example without saying where the
    list comes from. A client that has to hardcode pack codes is a client that
    breaks the day a pack is renamed.
    """
    code = _currency(request, currency)
    return TopupPacksResponse(
        currency=code,
        packs=[
            TopupPackOut(
                code=pack.code,
                display_name=pack.display_name,
                credits=pack.credits,
                price_minor=pack.price_minor(code),
                currency=code,
            )
            for pack in catalogue.TOPUP_PACKS.values()
        ],
    )


# --------------------------------------------------------------------------
# Checkout
# --------------------------------------------------------------------------


@router.post(
    "/billing/checkout",
    response_model=CheckoutResponse,
    dependencies=[Depends(general_rate_limit)],
    summary="Start a subscription or change plan",
)
async def checkout(
    body: CheckoutRequest, request: Request, user: CurrentUser, session: Session
) -> CheckoutResponse:
    """A hosted page to pay on. **The provider is derived, never chosen.**

    A downgrade does not go through checkout at all: it is scheduled for the
    next period boundary so nobody loses credits they are half way through
    using (§8.3), and sending the user to a payment page for it would be asking
    them to pay to receive less.
    """
    currency = _currency(request, body.currency)

    try:
        plan_code = PlanCode(body.plan)
    except ValueError:
        raise NotFoundError("We have no plan with that code.", {"plan": body.plan}) from None

    plan = await session.get(Plan, plan_code)
    if plan is None or not plan.is_public:
        # A retired plan is indistinguishable from one that never existed, on
        # purpose: `is_public` is how `beta` is withdrawn, and an endpoint that
        # still sold it would make the withdrawal a no-op.
        raise NotFoundError("We have no plan with that code.", {"plan": body.plan})

    if plan_code is PlanCode.FREE:
        raise PlanLimitExceededError(
            "The free plan is what you already have. Cancel instead to return to it.",
            details={"plan": "free"},
        )

    current = await service.live_subscription(session, user.id)
    if current is not None and current.plan == plan_code:
        raise NoSubscriptionError(
            "You are already on that plan.", details={"plan": plan_code.value}
        )

    # A move to a cheaper plan is scheduled, not sold — and it **returns**
    # rather than raising. Raising would roll the scheduling back: `get_session`
    # rolls back on any exception, so the response would have announced a
    # downgrade that was never written. The same trap `auth.py` documents on the
    # refresh-token-reuse branch, found here by a test asserting the row rather
    # than the status code.
    if current is not None:
        current_plan = await session.get(Plan, current.plan)
        if current_plan is not None and _is_downgrade(current_plan, plan):
            await service.schedule_downgrade(session, subscription=current, plan=plan_code)
            return CheckoutResponse(
                scheduled_plan=plan_code.value,
                effective_at=current.current_period_end,
            )

    try:
        checkout_session = await service.start_subscription_checkout(
            session,
            user=user,
            plan=plan,
            currency=currency,
            return_url=body.return_url,
            idempotency_key=request.headers.get("idempotency-key", ""),
        )
    except ProviderError as exc:
        log.error("checkout_failed", user_id=str(user.id), plan=plan_code.value, detail=exc.detail)
        raise CheckoutFailedError(str(exc)) from exc

    return CheckoutResponse(
        provider=checkout_session.provider.value,
        checkout_url=checkout_session.url,
        expires_at=checkout_session.expires_at,
    )


def _is_downgrade(current: Plan, target: Plan) -> bool:
    """Cheaper is a downgrade. Price, not credits: a plan could grant fewer
    credits and cost more, and it is the price the customer is deciding on."""
    return (target.price_usd_cents or 0) < (current.price_usd_cents or 0)


@router.post(
    "/billing/topup",
    response_model=CheckoutResponse,
    dependencies=[Depends(general_rate_limit)],
    summary="Buy credits that never expire",
)
async def topup(
    body: TopupRequest, request: Request, user: CurrentUser, session: Session
) -> CheckoutResponse:
    currency = _currency(request, body.currency)
    pack = catalogue.pack(body.pack_code)
    if pack is None:
        raise NotFoundError("We have no pack with that code.", {"packCode": body.pack_code})

    try:
        checkout_session = await service.start_topup_checkout(
            session,
            user=user,
            pack=pack,
            currency=currency,
            return_url=body.return_url,
            idempotency_key=request.headers.get("idempotency-key", ""),
        )
    except ProviderError as exc:
        log.error("topup_failed", user_id=str(user.id), pack=pack.code, detail=exc.detail)
        raise CheckoutFailedError(str(exc)) from exc

    return CheckoutResponse(
        provider=checkout_session.provider.value,
        checkout_url=checkout_session.url,
        expires_at=checkout_session.expires_at,
    )


@router.post(
    "/billing/portal",
    response_model=PortalResponse,
    dependencies=[Depends(general_rate_limit)],
    summary="Manage the subscription on the provider's own page",
)
async def portal(body: PortalRequest, user: CurrentUser, session: Session) -> PortalResponse:
    """`portalUrl` may be null, and that is a real answer.

    Razorpay hosts no customer portal. The client shows what *is* available —
    cancel here, invoices by email — rather than opening a page that does not
    exist. Amendment recorded in contract §7.
    """
    result = await service.open_portal(session, user=user, return_url=body.return_url)
    return PortalResponse(portal_url=result.url, reason=result.reason)


@router.post(
    "/billing/cancel",
    response_model=CancelResponse,
    dependencies=[Depends(general_rate_limit)],
    summary="Stop the subscription renewing",
)
async def cancel(body: CancelRequest, user: CurrentUser, session: Session) -> CancelResponse:
    """One request, no funnel, and the cost stated in the response.

    The body is written to be shown to the user *before* they confirm: losing
    1,840 credits is worth knowing about in advance, and saying so plainly at
    the moment of cancelling is both fairer and better retention than
    discovering it a week later. There is deliberately no retention offer here
    and no extra step — the competitor research that prompted this milestone was
    about exactly that (docs/20-m6-readiness.md §5).
    """
    try:
        outcome = await service.cancel(session, user=user, at_period_end=body.at_period_end)
    except ProviderError as exc:
        raise NoSubscriptionError(str(exc)) from exc

    return CancelResponse(
        plan=outcome.plan,
        status=outcome.status,
        cancel_at_period_end=outcome.cancel_at_period_end,
        access_until=outcome.access_until,
        credits_lost_at_period_end=outcome.credits_lost_at_period_end,
        credits_kept=outcome.credits_kept,
    )


# --------------------------------------------------------------------------
# The ledger
# --------------------------------------------------------------------------


def _encode_cursor(last_id: int) -> str:
    """Opaque, like every other cursor in this API (contract §1).

    Base64 of a small JSON object rather than the bare id: the client must not
    build one, and something it can read is something it will eventually
    construct by hand.
    """
    raw = json.dumps({"id": last_id}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> int | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        parsed = json.loads(base64.urlsafe_b64decode(padded))
        return int(parsed["id"])
    except (ValueError, KeyError, TypeError, binascii.Error):
        # A cursor we did not issue. Starting from the top is the harmless
        # answer — the alternative is a 400 on a link somebody pasted.
        return None


@router.get(
    "/credits/ledger",
    response_model=LedgerResponse,
    dependencies=[Depends(general_rate_limit)],
    summary="Every credit movement, with its bucket",
)
async def credit_ledger(
    user: CurrentUser,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=LEDGER_MAX_LIMIT)] = LEDGER_DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> LedgerResponse:
    """A first-class endpoint, not an admin tool.

    This is what a support conversation about *"where did my credits go"* is
    answered from, and it is the reason the ledger records a bucket on every
    row rather than a single balance.
    """
    query = (
        sa.select(CreditLedgerEntry)
        .where(CreditLedgerEntry.user_id == user.id)
        .order_by(CreditLedgerEntry.id.desc())
        .limit(limit + 1)  # one extra: its presence is what says there is a next page
    )
    before = _decode_cursor(cursor)
    if before is not None:
        query = query.where(CreditLedgerEntry.id < before)

    rows = list((await session.execute(query)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    return LedgerResponse(
        items=[
            LedgerEntryOut(
                id=row.id,
                bucket=row.bucket.value,
                delta=row.delta,
                reason=row.reason.value,
                job_id=ids.encode(ids.JOB, row.job_id) if row.job_id else None,
                payment_id=ids.encode(ids.PAYMENT, row.payment_id) if row.payment_id else None,
                balance_after=row.balance_after,
                note=row.note,
                created_at=row.created_at,
            )
            for row in rows
        ],
        next_cursor=_encode_cursor(rows[-1].id) if has_more and rows else None,
    )


# --------------------------------------------------------------------------
# Promo codes
# --------------------------------------------------------------------------


@router.get(
    "/promo/{code}",
    response_model=PromoPreviewResponse,
    dependencies=[Depends(general_rate_limit)],
    summary="Is this code real, and what does it give?",
)
async def preview_promo(code: str, session: Session) -> PromoPreviewResponse:
    """Public, and checked at the sign-up form rather than after registering.

    The attribution is written once and never revisited, so a mistyped code
    discovered afterwards can never be applied. A message under the field costs
    one request; the alternative costs a customer and a commission.

    Always `200`. A 404 here would let anyone enumerate which codes exist, and
    the client's rendering is the same either way.
    """
    found = await promo.find(session, code)
    if found is None:
        return PromoPreviewResponse(
            code=code, valid=False, message="We do not recognise that code."
        )
    return PromoPreviewResponse(
        code=found.code,
        valid=True,
        bonus_credits=found.bonus_credits,
        message=f"{found.bonus_credits} bonus credits when you sign up.",
    )


@router.get(
    "/promo/{code}/stats",
    response_model=PromoStatsResponse,
    dependencies=[Depends(general_rate_limit)],
    summary="What this code has brought in, for whoever owns it",
)
async def promo_stats(code: str, user: CurrentUser, session: Session) -> PromoStatsResponse:
    """Only the owner sees it.

    A code's numbers say how many people from a particular Discord server signed
    up and how many pay us — which is commercial information about somebody
    else's community, not ours to publish. 404 rather than 403 for a code
    somebody else owns, so the response cannot be used to discover that a code
    exists.
    """
    found = await session.get(PromoCode, code)
    if found is None or found.owner_user_id != user.id:
        raise NotFoundError("We have no code with that name.", {"code": code})

    stats = await promo.owner_stats(session, code=found.code)
    assert stats is not None  # the row was just read
    return PromoStatsResponse(
        code=stats.code,
        is_active=stats.is_active,
        signups=stats.signups,
        subscribers=stats.subscribers,
        owed_minor=stats.owed_minor,
        accrued_minor=stats.accrued_minor,
        paid_minor=stats.paid_minor,
    )

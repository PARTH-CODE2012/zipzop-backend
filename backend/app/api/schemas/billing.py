"""Request and response shapes for /plans, /billing and /credits — contract §7."""

from datetime import datetime
from typing import Annotated

from pydantic import Field, field_validator

from app.api.schemas.common import ApiModel
from app.services.billing.routing import SUPPORTED_CURRENCIES

CurrencyCode = Annotated[str, Field(min_length=3, max_length=3)]


def _known_currency(value: str | None) -> str | None:
    """`None` means "suggest one from my IP", which the route then does."""
    if value is None:
        return None
    code = value.strip().upper()
    if code not in SUPPORTED_CURRENCIES:
        raise ValueError(f"currency must be one of {', '.join(SUPPORTED_CURRENCIES)}")
    return code


class PlanOfferOut(ApiModel):
    """One row of the pricing table.

    `approxVideosPerMonth` is the marketing figure and `queueLabel` is a word
    rather than a time — contract §7 is explicit about both. **Credits are the
    real unit**; these two exist so a pricing page can say something a person
    understands without publishing an SLA nobody has measured.
    """

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


class PlansResponse(ApiModel):
    suggested_currency: str
    suggested_provider: str
    plans: list[PlanOfferOut]


# --------------------------------------------------------------------------
# The return URL
# --------------------------------------------------------------------------


def _validated_return_url(value: str) -> str:
    """Refuse a `returnUrl` that is not one of ours.

    **This is an open-redirect check on the billing path, and that is the worst
    place to have one.** The value arrives in the request body, so it is
    attacker-controlled; a checkout that comes back to somewhere else is a
    phishing page a customer reaches from a genuine payment, having just typed
    card details on a page that really was the provider's.

    Origin-exact rather than a substring or a suffix match: `evil.com` and
    `zipzop.app.evil.com` both pass a naive `endswith`, and both are the attack.
    """
    from urllib.parse import urlparse

    from app.config import settings

    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("returnUrl must be an absolute http(s) URL")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in settings.billing_return_url_origins:
        raise ValueError("returnUrl must point back at this application")
    return value


ReturnUrl = Annotated[str, Field(max_length=2048)]


class CheckoutRequest(ApiModel):
    plan: str
    currency: CurrencyCode | None = None
    return_url: ReturnUrl

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str | None) -> str | None:
        return _known_currency(value)

    @field_validator("return_url")
    @classmethod
    def _own_return_url(cls, value: str) -> str:
        return _validated_return_url(value)


class TopupRequest(ApiModel):
    pack_code: str
    currency: CurrencyCode | None = None
    return_url: ReturnUrl

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str | None) -> str | None:
        return _known_currency(value)

    @field_validator("return_url")
    @classmethod
    def _own_return_url(cls, value: str) -> str:
        return _validated_return_url(value)


class PortalRequest(ApiModel):
    return_url: ReturnUrl

    @field_validator("return_url")
    @classmethod
    def _own_return_url(cls, value: str) -> str:
        return _validated_return_url(value)


class CheckoutResponse(ApiModel):
    """What a plan change produced.

    **`checkoutUrl` is nullable, which is an amendment to contract §7.** The
    endpoint is specified as *"start a subscription or change plan"*, and one
    kind of plan change costs nothing now: a downgrade applies at the next
    period boundary (§8.3) so nobody loses credits they are half way through
    using. There is no page to pay on, and sending the user to one would be
    asking them to pay in order to receive less.

    Reporting that as an error was the first shape this took, and it was wrong
    twice: a scheduled downgrade is a success, and — worse — the state change
    was written and then thrown away, because `get_session` rolls back on any
    exception. The client now has one path for "your plan changed": follow
    `checkoutUrl` if there is one, otherwise show `effectiveAt`.
    """

    provider: str | None = None
    checkout_url: str | None = None
    expires_at: datetime | None = None
    #: Set instead of `checkoutUrl` when the change was scheduled rather than sold.
    scheduled_plan: str | None = None
    effective_at: datetime | None = None


class PortalResponse(ApiModel):
    """`portalUrl` is nullable, which is an amendment to contract §7.

    Stripe hosts a customer portal; **Razorpay does not**. Rather than invent a
    URL that 404s or rebuild card management — which §7 explicitly declines to
    do — the absence is reported, with a sentence the client can show and the
    actions that *are* available beside it. Recorded in the contract.
    """

    portal_url: str | None = None
    reason: str | None = None


class TopupPackOut(ApiModel):
    code: str
    display_name: str
    credits: int
    price_minor: int
    currency: str


class TopupPacksResponse(ApiModel):
    currency: str
    packs: list[TopupPackOut]


class CancelRequest(ApiModel):
    at_period_end: bool = True


class CancelResponse(ApiModel):
    """Written to be shown *before* the user confirms — contract §7."""

    plan: str
    status: str
    cancel_at_period_end: bool
    access_until: datetime
    credits_lost_at_period_end: dict[str, int]
    credits_kept: dict[str, int]


# --------------------------------------------------------------------------
# The ledger
# --------------------------------------------------------------------------


class LedgerEntryOut(ApiModel):
    id: int
    bucket: str
    delta: int
    reason: str
    job_id: str | None = None
    payment_id: str | None = None
    balance_after: int
    note: str | None = None
    created_at: datetime


class LedgerResponse(ApiModel):
    items: list[LedgerEntryOut]
    next_cursor: str | None = None


# --------------------------------------------------------------------------
# Promo codes
# --------------------------------------------------------------------------


class PromoPreviewResponse(ApiModel):
    """What a code is worth, before an account exists.

    Checked at the sign-up form so a mistyped code is a message under the field
    rather than a silent nothing after registering — at which point it can never
    be applied, because the attribution is written once.
    """

    code: str
    valid: bool
    bonus_credits: int = 0
    message: str


class PromoStatsResponse(ApiModel):
    """What a Discord server owner is owed, for the owner.

    Amounts are minor units and per currency, because a code can bring in both
    rupee and dollar subscribers and summing them would be a made-up number.
    """

    code: str
    is_active: bool
    signups: int
    subscribers: int
    owed_minor: dict[str, int]
    accrued_minor: dict[str, int]
    paid_minor: dict[str, int]

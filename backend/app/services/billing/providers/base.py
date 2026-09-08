"""The provider interface, and the vocabulary everything above it speaks.

**Nothing outside this package knows which provider a user came through**
(docs/03-backend-architecture.md §8.1). `subscriptions.provider` records the
origin; allowances, gating and priority read `plans` and never branch on it. A
third provider later is a new file here, not a new path through the
application.

The interface was designed for two providers before either existed, and only
one is implemented. That is the point of writing it down: Stripe is deferred,
not dropped, and adding it must not reshape anything.

---

**What is testable here and what is not.** Everything on our side of the
boundary — signature verification, event normalisation, which of our users an
event names, what a webhook does to the ledger — is tested and must be. What
happens on the provider's server is not: the account's webhook secret does not
exist yet (docs/20-m6-readiness.md §3.1), and a test that asserts against a
recorded response body only proves the recording is unchanged. So the HTTP
calls live behind `_request`, the wire shapes are isolated in one adapter, and
every rule worth defending is expressed on this side of them.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.models import PaymentProvider, PlanCode


class BillingEventKind(enum.StrEnum):
    """Provider events, translated into our vocabulary.

    Deliberately small. Each member is something the application actually *does*
    something about; a provider event with no member here is stored and
    acknowledged, which is the right answer for the dozens of notifications
    neither provider lets you unsubscribe from.
    """

    #: A subscription started or was renewed and paid for. Grants the period.
    SUBSCRIPTION_CHARGED = "subscription_charged"
    #: Active, but no money moved with this event — a mandate authorised.
    SUBSCRIPTION_ACTIVATED = "subscription_activated"
    #: The customer or the provider ended it. Access runs to the period end.
    SUBSCRIPTION_CANCELLED = "subscription_cancelled"
    #: A renewal failed. Not a cancellation: the provider retries on its own
    #: schedule and we keep the plan live during that window (§8.3).
    SUBSCRIPTION_PAYMENT_FAILED = "subscription_payment_failed"
    #: A one-off payment for credits that never expire.
    TOPUP_PAID = "topup_paid"
    #: Understood well enough to store, not well enough to act on.
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class CheckoutSession:
    """Where to send the user, and what we will recognise when they come back.

    `provider_reference` is the provider's own id for the thing created — a
    subscription id, an order id, a payment-link id. It is stored before the
    redirect so a webhook can be matched to a user even if the `notes` we
    attached come back empty, which is the failure mode that turns a paid
    customer into a support ticket.
    """

    provider: PaymentProvider
    url: str
    provider_reference: str
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PortalSession:
    """A hosted place to manage the subscription — or the honest absence of one.

    `url is None` is a real answer, not a failure. Stripe has a customer portal;
    **Razorpay does not**, and pretending otherwise would mean either inventing
    a URL that 404s or rebuilding card management ourselves, which contract §7
    explicitly declines to do. The route reports the absence and the client
    offers what does exist — cancel, and the provider's own emailed invoices.
    """

    url: str | None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class WebhookEvent:
    """One delivery, verified and normalised.

    `event_id` is what makes a duplicate a duplicate: it is the primary key in
    `provider_events` together with the provider, so a redelivery collides and
    is dropped rather than granting a second month (§8.5).
    """

    provider: PaymentProvider
    event_id: str
    event_type: str
    kind: BillingEventKind
    payload: dict[str, Any]

    #: Which of our users this concerns, when the event says so.
    user_id: str | None = None
    #: The provider's subscription id, for events about a subscription.
    subscription_reference: str | None = None
    #: The provider's payment id, for events that moved money.
    payment_reference: str | None = None
    #: The plan the subscription is for, when the event carries it.
    plan: PlanCode | None = None
    #: Minor units — paise or cents — and the currency they are in.
    amount_minor: int | None = None
    currency: str | None = None
    #: The period this event pays for, when the provider states it. Used to
    #: drop a grant for a period already granted (§8.5 step 4).
    period_start: datetime | None = None
    period_end: datetime | None = None
    #: Anything we attached at checkout and got back unchanged.
    notes: dict[str, str] = field(default_factory=dict)


class ProviderError(RuntimeError):
    """The provider refused, or could not be reached.

    Raised by an adapter and turned into `CHECKOUT_FAILED` (502) at the route.
    Never carries a provider error body straight through to the client: those
    quote account ids and internal reasons, and the user can do nothing with
    either.
    """

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class SignatureError(ProviderError):
    """The webhook did not verify. The request is dropped with 400.

    Its own class because it is the one failure on this path that is an
    *attack* rather than an outage, and it is logged accordingly.
    """


class BillingProvider(ABC):
    """One adapter per payment provider. Four things, and no more.

    Anything that is not provider-specific — what a plan grants, when a period
    ends, which bucket credits land in — belongs in `billing/service.py`, where
    there is one copy of it rather than one per provider.
    """

    provider: PaymentProvider

    @abstractmethod
    def is_configured(self) -> bool:
        """Whether this adapter has the credentials it needs.

        Checked at call time and never at startup: billing keys are empty in
        development, and an application that refuses to boot without a payment
        provider cannot be developed.
        """

    @abstractmethod
    async def ensure_plan(
        self, *, plan: PlanCode, display_name: str, currency: str, amount_minor: int
    ) -> str:
        """Create this plan on the provider's side, and return its id there.

        Both providers want a plan object of their own before a subscription can
        reference one — a Razorpay plan, a Stripe price — and both give it an
        opaque id. The caller caches the result in `provider_plans`, so this
        runs once per (plan, currency) rather than once per checkout.
        """

    @abstractmethod
    async def create_checkout(
        self,
        *,
        user_id: str,
        email: str,
        plan: PlanCode,
        currency: str,
        amount_minor: int,
        return_url: str,
        idempotency_key: str,
        provider_plan_id: str,
    ) -> CheckoutSession:
        """A hosted page that starts or changes a subscription."""

    @abstractmethod
    async def create_topup(
        self,
        *,
        user_id: str,
        email: str,
        pack_code: str,
        credits: int,
        currency: str,
        amount_minor: int,
        return_url: str,
        idempotency_key: str,
    ) -> CheckoutSession:
        """A hosted page for a one-off purchase of credits."""

    @abstractmethod
    async def create_portal_session(
        self, *, provider_customer_id: str | None, return_url: str
    ) -> PortalSession:
        """Somewhere for the customer to manage the subscription themselves."""

    @abstractmethod
    async def cancel_subscription(
        self, *, provider_subscription_id: str, at_period_end: bool
    ) -> None:
        """Tell the provider to stop charging.

        Our own row is updated by the caller regardless of what this does — a
        provider that is briefly unreachable must not stop a user cancelling.
        """

    @abstractmethod
    def verify_signature(self, *, raw_body: bytes, headers: dict[str, str]) -> None:
        """Raise `SignatureError` unless the delivery is genuinely the provider's.

        Takes the **raw bytes**, never a parsed body: both providers sign the
        exact octets they sent, and a dict that has been through a JSON
        round-trip is a different byte string with the same meaning.
        """

    @abstractmethod
    def parse_webhook(self, *, raw_body: bytes, headers: dict[str, str]) -> WebhookEvent:
        """Turn a verified delivery into our vocabulary. Never called unverified."""

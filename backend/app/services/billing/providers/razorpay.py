"""Razorpay — the launch provider.

Chosen 25 August over Stripe (docs/13-mvp-direction.md §3.4): Indian cards and
UPI are the first market, and Stripe's India coverage plus the RBI rules on
recurring mandates make it the wrong one to start with. Stripe is deferred, not
dropped — this adapter implements the interface in `base.py`, which was
designed for two.

**Written against Razorpay's published REST API and not yet exercised against a
live account.** The webhook secret does not exist until an endpoint is created
in the dashboard (docs/20-m6-readiness.md §3.1), so nothing here has seen a real
delivery. Two consequences shape the file:

* every wire shape the provider owns is confined to this module, so correcting
  one is a local edit rather than a search;
* everything on *our* side of the boundary — the signature check, the
  normalisation, which of our users an event names — is pure and tested in
  `tests/test_billing_razorpay.py`, because those are the parts a live account
  would not have proved anyway.

`httpx` rather than the `razorpay` SDK: it is already a dependency, it is async,
and the SDK is a thin wrapper over six endpoints. A new dependency on the money
path needs a better reason than saving thirty lines.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any, Final

import httpx

from app.config import settings
from app.logging import get_logger
from app.models import PaymentProvider, PlanCode
from app.services.billing.providers.base import (
    BillingEventKind,
    BillingProvider,
    CheckoutSession,
    PortalSession,
    ProviderError,
    SignatureError,
    WebhookEvent,
)

log = get_logger(__name__)

API_BASE: Final = "https://api.razorpay.com/v1"

#: Razorpay has no "until cancelled" subscription — `total_count` is required
#: and finite. Ten years of monthly cycles is the standard way to say forever;
#: a subscription that outlives it is a problem we would be glad to have.
TOTAL_COUNT_MONTHS: Final = 120

#: How long a hosted page stays usable. Razorpay's own default is longer; a
#: short window means an abandoned checkout stops being a live payment link
#: sitting in someone's browser history.
CHECKOUT_TTL_SECONDS: Final = 30 * 60

_TIMEOUT: Final = httpx.Timeout(15.0, connect=5.0)

#: Razorpay event name → what we do about it.
#:
#: Everything absent from this table is stored and acknowledged as
#: `IGNORED`, which is the right answer for the many notifications neither
#: provider lets you turn off. Adding a case later is a line here, not a new
#: code path.
_EVENT_KINDS: Final[dict[str, BillingEventKind]] = {
    # Money moved and a period was paid for. **The grant path.**
    "subscription.charged": BillingEventKind.SUBSCRIPTION_CHARGED,
    # The mandate was authorised. No money yet on some flows, so it is not a
    # grant on its own.
    "subscription.activated": BillingEventKind.SUBSCRIPTION_ACTIVATED,
    "subscription.authenticated": BillingEventKind.SUBSCRIPTION_ACTIVATED,
    "subscription.cancelled": BillingEventKind.SUBSCRIPTION_CANCELLED,
    "subscription.completed": BillingEventKind.SUBSCRIPTION_CANCELLED,
    "subscription.expired": BillingEventKind.SUBSCRIPTION_CANCELLED,
    # Dunning. Not a cancellation: the provider retries on its own schedule and
    # we keep the plan live during that window (§8.3).
    "subscription.halted": BillingEventKind.SUBSCRIPTION_PAYMENT_FAILED,
    "subscription.pending": BillingEventKind.SUBSCRIPTION_PAYMENT_FAILED,
    # One-off credit purchases go out as payment links.
    "payment_link.paid": BillingEventKind.TOPUP_PAID,
    "order.paid": BillingEventKind.TOPUP_PAID,
}


def _minor_to_paise(amount_minor: int) -> int:
    """Razorpay counts in the currency's smallest unit, which is what we store."""
    return amount_minor


class RazorpayProvider(BillingProvider):
    provider = PaymentProvider.RAZORPAY

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        #: Injected in tests. Never constructed at import time: building a
        #: client for credentials that are empty in every developer environment
        #: would fail at the wrong moment.
        self._client = client

    # ---------------------------------------------------------------- setup
    def is_configured(self) -> bool:
        return bool(settings.razorpay_key_id and settings.razorpay_key_secret)

    def _require_configuration(self) -> None:
        if not self.is_configured():
            raise ProviderError(
                "Payments are not available right now.",
                detail="RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are not set",
            )

    async def _request(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """One place where this application talks to Razorpay.

        Errors are logged with the provider's own message and raised with one
        the user can act on. **The provider's body never reaches the client**:
        it quotes account ids and internal reasons, and a customer can do
        nothing with either.
        """
        self._require_configuration()
        auth = (settings.razorpay_key_id, settings.razorpay_key_secret)
        client = self._client
        try:
            if client is None:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as fresh:
                    response = await fresh.request(
                        method, f"{API_BASE}{path}", json=json_body, auth=auth
                    )
            else:
                response = await client.request(
                    method, f"{API_BASE}{path}", json=json_body, auth=auth
                )
        except httpx.HTTPError as exc:
            log.error("razorpay_unreachable", path=path, error=str(exc))
            raise ProviderError(
                "We could not reach the payment provider. Please try again.",
                detail=str(exc),
            ) from exc

        if response.status_code >= 400:
            log.error(
                "razorpay_refused",
                path=path,
                status=response.status_code,
                body=response.text[:500],
            )
            raise ProviderError(
                "The payment provider refused that request.",
                detail=f"{response.status_code}: {response.text[:200]}",
            )

        body: dict[str, Any] = response.json()
        return body

    # ------------------------------------------------------------- checkout
    async def ensure_plan(
        self, *, plan: PlanCode, display_name: str, currency: str, amount_minor: int
    ) -> str:
        """Create the plan on Razorpay's side and return its id.

        Called by the service, which caches the result in `provider_plans` — so
        this runs once per (plan, currency) for the life of the account rather
        than once per checkout.
        """
        body = await self._request(
            "POST",
            "/plans",
            json_body={
                "period": "monthly",
                "interval": 1,
                "item": {
                    "name": f"ZipZop {display_name}",
                    "amount": _minor_to_paise(amount_minor),
                    "currency": currency,
                },
                "notes": {"plan": plan.value},
            },
        )
        plan_id = str(body.get("id") or "")
        if not plan_id:
            raise ProviderError(
                "The payment provider did not return a plan.", detail=json.dumps(body)[:200]
            )
        return plan_id

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
        """A hosted subscription page.

        `notes` is what comes back on every webhook about this subscription, and
        it is how a delivery is matched to one of our users. The subscription id
        is stored beside it by the caller **before the redirect**, because notes
        have been known to arrive empty and a paid customer nobody can identify
        is the worst outcome on this path.
        """
        body = await self._request(
            "POST",
            "/subscriptions",
            json_body={
                "plan_id": provider_plan_id,
                "total_count": TOTAL_COUNT_MONTHS,
                "quantity": 1,
                "customer_notify": 1,
                "expire_by": int(datetime.now(UTC).timestamp()) + CHECKOUT_TTL_SECONDS,
                "notes": {
                    "user_id": user_id,
                    "plan": plan.value,
                    "kind": "subscription",
                    "idempotency_key": idempotency_key,
                    "return_url": return_url,
                },
            },
        )
        url = str(body.get("short_url") or "")
        reference = str(body.get("id") or "")
        if not url or not reference:
            raise ProviderError(
                "The payment provider did not return a checkout page.",
                detail=json.dumps(body)[:200],
            )
        return CheckoutSession(
            provider=self.provider,
            url=url,
            provider_reference=reference,
            expires_at=datetime.fromtimestamp(
                int(datetime.now(UTC).timestamp()) + CHECKOUT_TTL_SECONDS, tz=UTC
            ),
        )

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
        """A payment link — Razorpay's hosted page for a one-off charge.

        Not an order: an order needs the browser to run Razorpay's Checkout
        script, which would put the provider's JavaScript in our bundle. A
        payment link is a URL, which is what contract §7 promises and what keeps
        card data entirely outside this system.
        """
        body = await self._request(
            "POST",
            "/payment_links",
            json_body={
                "amount": _minor_to_paise(amount_minor),
                "currency": currency,
                "accept_partial": False,
                "description": f"{credits} ZipZop credits",
                "customer": {"email": email},
                "notify": {"email": True, "sms": False},
                "reminder_enable": False,
                "callback_url": return_url,
                "callback_method": "get",
                "expire_by": int(datetime.now(UTC).timestamp()) + CHECKOUT_TTL_SECONDS,
                "notes": {
                    "user_id": user_id,
                    "kind": "topup",
                    "pack": pack_code,
                    "credits": str(credits),
                    "idempotency_key": idempotency_key,
                },
            },
        )
        url = str(body.get("short_url") or "")
        reference = str(body.get("id") or "")
        if not url or not reference:
            raise ProviderError(
                "The payment provider did not return a checkout page.",
                detail=json.dumps(body)[:200],
            )
        return CheckoutSession(
            provider=self.provider,
            url=url,
            provider_reference=reference,
            expires_at=datetime.fromtimestamp(
                int(datetime.now(UTC).timestamp()) + CHECKOUT_TTL_SECONDS, tz=UTC
            ),
        )

    async def create_portal_session(
        self, *, provider_customer_id: str | None, return_url: str
    ) -> PortalSession:
        """**Razorpay has no hosted customer portal.** This is the honest answer.

        Stripe does, and contract §7 was written against it — *"a `portalUrl` to
        the provider's hosted management page — update card, view invoices,
        cancel. We do not rebuild any of that."* Razorpay's equivalent is the
        emails it sends the customer and the subscription's own management link,
        neither of which is an API we can hand out.

        Returning `url=None` with a reason means the client can say what *is*
        available — cancel here, invoices by email — instead of opening a page
        that does not exist. Recorded as an amendment in
        docs/05-api-contract.md §7.
        """
        return PortalSession(
            url=None,
            reason="Razorpay does not host a customer portal; manage the subscription here.",
        )

    async def cancel_subscription(
        self, *, provider_subscription_id: str, at_period_end: bool
    ) -> None:
        await self._request(
            "POST",
            f"/subscriptions/{provider_subscription_id}/cancel",
            json_body={"cancel_at_cycle_end": 1 if at_period_end else 0},
        )

    # -------------------------------------------------------------- webhook
    def verify_signature(self, *, raw_body: bytes, headers: dict[str, str]) -> None:
        """HMAC-SHA256 of the exact bytes, hex, compared in constant time.

        Three things here are load-bearing:

        * **the raw body.** Razorpay signs the octets it sent. A dict that has
          been parsed and re-serialised is a different byte string with the same
          meaning, and it will not verify.
        * **`compare_digest`.** A `==` on a signature leaks its prefix through
          timing. It is a small leak and this is the one endpoint where it is
          worth closing.
        * **a missing secret is a failure, never a pass.** An unconfigured
          verifier that returns quietly is a way to grant a plan for free, and
          it looks exactly like a working one.
        """
        secret = settings.razorpay_webhook_secret
        if not secret:
            raise SignatureError(
                "Webhooks are not configured.",
                detail="RAZORPAY_WEBHOOK_SECRET is empty; refusing to accept unverified events",
            )

        sent = _header(headers, "x-razorpay-signature")
        if not sent:
            raise SignatureError("Missing signature.", detail="no X-Razorpay-Signature header")

        expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sent.strip()):
            raise SignatureError("Signature did not verify.")

    def parse_webhook(self, *, raw_body: bytes, headers: dict[str, str]) -> WebhookEvent:
        try:
            payload: dict[str, Any] = json.loads(raw_body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderError("The webhook body was not JSON.", detail=str(exc)) from exc

        event_type = str(payload.get("event") or "unknown")
        kind = _EVENT_KINDS.get(event_type, BillingEventKind.IGNORED)

        entities = payload.get("payload") or {}
        subscription = _entity(entities, "subscription")
        payment = _entity(entities, "payment")
        link = _entity(entities, "payment_link")
        order = _entity(entities, "order")

        # Notes can ride on any of the four, and which one carries them depends
        # on the event. First match wins, most specific first.
        notes: dict[str, str] = {}
        for candidate in (subscription, link, order, payment):
            raw_notes = candidate.get("notes")
            if isinstance(raw_notes, dict) and raw_notes:
                notes = {str(k): str(v) for k, v in raw_notes.items()}
                break

        return WebhookEvent(
            provider=self.provider,
            event_id=_event_id(payload, headers, raw_body),
            event_type=event_type,
            kind=kind,
            payload=payload,
            user_id=notes.get("user_id"),
            subscription_reference=_text(subscription.get("id")),
            payment_reference=_text(payment.get("id")) or _text(link.get("id")),
            plan=_plan_from_notes(notes),
            amount_minor=_int(payment.get("amount")) or _int(link.get("amount")),
            currency=_text(payment.get("currency")) or _text(link.get("currency")),
            period_start=_moment(subscription.get("current_start")),
            period_end=_moment(subscription.get("current_end")),
            notes=notes,
        )


# --------------------------------------------------------------------------
# Reading a payload defensively.
#
# Every helper below returns `None` rather than raising on a shape it did not
# expect. A webhook that arrives with one field missing must still be stored and
# acknowledged: the alternative is a 500, which Razorpay reads as a failure and
# retries for days, and the delivery we could not parse the first time will not
# parse on the ninth.
# --------------------------------------------------------------------------


def _header(headers: dict[str, str], name: str) -> str | None:
    """Case-insensitive lookup. Header case is not guaranteed by anything."""
    lowered = {k.lower(): v for k, v in headers.items()}
    return lowered.get(name)


def _entity(entities: Any, name: str) -> dict[str, Any]:
    """Razorpay nests each entity one level deeper than you expect:
    `payload.subscription.entity`, not `payload.subscription`."""
    if not isinstance(entities, dict):
        return {}
    wrapper = entities.get(name)
    if not isinstance(wrapper, dict):
        return {}
    entity = wrapper.get("entity")
    return entity if isinstance(entity, dict) else {}


def _event_id(payload: dict[str, Any], headers: dict[str, str], raw_body: bytes) -> str:
    """What makes a duplicate a duplicate.

    Razorpay sends `X-Razorpay-Event-Id`. When it does not — an older account
    setting, a replay through a proxy that strips headers — a digest of the body
    is used instead. That is weaker (two genuinely distinct events with
    identical bodies would collide) and it is still far better than a random id,
    which would make every redelivery a fresh grant.
    """
    from_header = _header(headers, "x-razorpay-event-id")
    if from_header:
        return from_header
    digest = hashlib.sha256(raw_body).hexdigest()[:32]
    return f"body:{digest}"


def _text(value: Any) -> str | None:
    return str(value) if isinstance(value, str | int) and str(value) else None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _moment(value: Any) -> datetime | None:
    """Razorpay timestamps are unix seconds."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return datetime.fromtimestamp(value, tz=UTC)
    return None


def _plan_from_notes(notes: dict[str, str]) -> PlanCode | None:
    raw = notes.get("plan")
    if not raw:
        return None
    try:
        return PlanCode(raw)
    except ValueError:
        # A plan we have never heard of. Storing the event is still right; the
        # service will refuse to act on it, which is the safe direction.
        log.warning("razorpay_unknown_plan_in_notes", plan=raw)
        return None

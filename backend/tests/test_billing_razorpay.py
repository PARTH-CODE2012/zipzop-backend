"""The Razorpay adapter — signature verification and event normalisation.

**Everything here is on our side of the boundary**, and that is the point. The
account's webhook secret does not exist yet (docs/20-m6-readiness.md §3.1), so
nothing has seen a real delivery; a test asserting against a recorded response
body would only prove the recording is unchanged.

What *can* be tested is what actually protects the money: that a forged
signature is rejected, that a missing secret fails closed, that a body one byte
different does not verify, and that a payload we do understand is turned into
the right instruction. Those are the parts a live account would not have proved
anyway.
"""

import hashlib
import hmac
import json
from typing import Any

import pytest

from app.config import settings
from app.models import PaymentProvider, PlanCode
from app.services.billing.providers.base import BillingEventKind, SignatureError
from app.services.billing.providers.razorpay import RazorpayProvider

SECRET = "whsec_test_only_not_a_real_razorpay_secret"


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """A secret of our own choosing.

    The real one is issued by the dashboard when a webhook endpoint is created
    and does not exist yet. That blocks proving we agree with *Razorpay's*
    HMAC; it does not block proving the check runs, is constant-time, and fails
    closed — which is what this file is for.
    """
    monkeypatch.setattr(settings, "razorpay_webhook_secret", SECRET)


def _signed(body: dict[str, Any], *, secret: str = SECRET) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Razorpay-Signature": signature, "X-Razorpay-Event-Id": "evt_test_1"}


def _charged(**overrides: Any) -> dict[str, Any]:
    """A `subscription.charged` delivery, shaped as Razorpay documents it —
    each entity nested one level deeper than you expect."""
    body: dict[str, Any] = {
        "entity": "event",
        "account_id": "acc_test",
        "event": "subscription.charged",
        "contains": ["subscription", "payment"],
        "payload": {
            "subscription": {
                "entity": {
                    "id": "sub_test_1",
                    "status": "active",
                    "current_start": 1_756_000_000,
                    "current_end": 1_758_592_000,
                    "notes": {"user_id": "d3b07384-d9a0-4f4e-9c8a-000000000001", "plan": "beta"},
                }
            },
            "payment": {
                "entity": {
                    "id": "pay_test_1",
                    "amount": 19_900,
                    "currency": "INR",
                    "status": "captured",
                }
            },
        },
        "created_at": 1_756_000_001,
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------
# The signature — the whole defence on this path
# --------------------------------------------------------------------------


def test_a_correctly_signed_body_verifies() -> None:
    raw, headers = _signed(_charged())
    RazorpayProvider().verify_signature(raw_body=raw, headers=headers)


def test_a_forged_signature_is_rejected() -> None:
    raw, headers = _signed(_charged(), secret="not-the-secret")
    with pytest.raises(SignatureError):
        RazorpayProvider().verify_signature(raw_body=raw, headers=headers)


def test_a_body_changed_by_one_byte_does_not_verify() -> None:
    """The attack this stops: a genuine delivery replayed with the amount, the
    plan or the user id edited."""
    raw, headers = _signed(_charged())
    tampered = raw.replace(b'"amount": 19900', b'"amount": 1')
    assert tampered != raw
    with pytest.raises(SignatureError):
        RazorpayProvider().verify_signature(raw_body=tampered, headers=headers)


def test_a_missing_signature_header_is_rejected() -> None:
    raw, _ = _signed(_charged())
    with pytest.raises(SignatureError):
        RazorpayProvider().verify_signature(raw_body=raw, headers={})


def test_the_header_name_is_matched_case_insensitively() -> None:
    """Nothing guarantees header case — not the provider, not a proxy, not the
    ASGI server. Matching exactly would reject every genuine delivery through
    whichever hop lower-cased them."""
    raw, headers = _signed(_charged())
    lowered = {k.lower(): v for k, v in headers.items()}
    RazorpayProvider().verify_signature(raw_body=raw, headers=lowered)


def test_no_configured_secret_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 An unconfigured verifier must never be a passing one.

    A check that returns quietly when it has no secret is indistinguishable
    from a working one, and it is a way to grant yourself a plan for free.
    """
    monkeypatch.setattr(settings, "razorpay_webhook_secret", "")
    raw, headers = _signed(_charged())
    with pytest.raises(SignatureError):
        RazorpayProvider().verify_signature(raw_body=raw, headers=headers)


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------


def test_a_charge_is_read_as_a_grant() -> None:
    raw, headers = _signed(_charged())
    event = RazorpayProvider().parse_webhook(raw_body=raw, headers=headers)

    assert event.kind is BillingEventKind.SUBSCRIPTION_CHARGED
    assert event.provider is PaymentProvider.RAZORPAY
    assert event.user_id == "d3b07384-d9a0-4f4e-9c8a-000000000001"
    assert event.plan is PlanCode.BETA
    assert event.subscription_reference == "sub_test_1"
    assert event.payment_reference == "pay_test_1"
    assert (event.amount_minor, event.currency) == (19_900, "INR")


def test_the_period_comes_back_as_real_timestamps() -> None:
    """Razorpay counts in unix seconds. Passing those through as integers would
    make every period comparison silently false."""
    raw, headers = _signed(_charged())
    event = RazorpayProvider().parse_webhook(raw_body=raw, headers=headers)

    assert event.period_start is not None and event.period_end is not None
    assert event.period_start.year >= 2025
    assert event.period_end > event.period_start


def test_the_event_id_comes_from_the_header() -> None:
    """It is what makes a duplicate a duplicate — the primary key in
    `provider_events` together with the provider."""
    raw, headers = _signed(_charged())
    event = RazorpayProvider().parse_webhook(raw_body=raw, headers=headers)
    assert event.event_id == "evt_test_1"


def test_without_the_header_the_body_digest_is_the_id() -> None:
    """Weaker, and far better than a random id.

    A random id would make every redelivery a fresh grant. A digest collides
    only when two genuinely distinct events have byte-identical bodies, which
    for a payload carrying its own timestamp does not happen.
    """
    raw, _ = _signed(_charged())
    first = RazorpayProvider().parse_webhook(raw_body=raw, headers={})
    second = RazorpayProvider().parse_webhook(raw_body=raw, headers={})
    assert first.event_id == second.event_id
    assert first.event_id.startswith("body:")


def test_an_event_we_do_not_act_on_is_still_readable() -> None:
    """Neither provider lets you unsubscribe from most notifications. Anything
    unrecognised is stored and acknowledged rather than retried forever."""
    raw, headers = _signed(_charged(event="payment.downtime.started"))
    event = RazorpayProvider().parse_webhook(raw_body=raw, headers=headers)
    assert event.kind is BillingEventKind.IGNORED
    assert event.event_type == "payment.downtime.started"


def test_a_halted_subscription_is_dunning_and_not_a_cancellation() -> None:
    """§8.3: a first decline is usually an expired card, not an unwilling
    customer. Cutting service off is how a recoverable payment is lost."""
    raw, headers = _signed(_charged(event="subscription.halted"))
    event = RazorpayProvider().parse_webhook(raw_body=raw, headers=headers)
    assert event.kind is BillingEventKind.SUBSCRIPTION_PAYMENT_FAILED


def test_a_cancellation_is_read_as_one() -> None:
    raw, headers = _signed(_charged(event="subscription.cancelled"))
    event = RazorpayProvider().parse_webhook(raw_body=raw, headers=headers)
    assert event.kind is BillingEventKind.SUBSCRIPTION_CANCELLED


def test_a_payment_link_is_read_as_a_topup() -> None:
    body = {
        "entity": "event",
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": "plink_test_1",
                    "amount": 224_900,
                    "currency": "INR",
                    "notes": {
                        "user_id": "d3b07384-d9a0-4f4e-9c8a-000000000002",
                        "kind": "topup",
                        "pack": "credits_5000",
                    },
                }
            }
        },
    }
    raw, headers = _signed(body)
    event = RazorpayProvider().parse_webhook(raw_body=raw, headers=headers)

    assert event.kind is BillingEventKind.TOPUP_PAID
    assert event.notes["pack"] == "credits_5000"
    assert event.payment_reference == "plink_test_1"


def test_a_payload_missing_everything_still_parses() -> None:
    """A delivery we cannot read must not become a `500`.

    Both providers read a 500 as our outage and retry for days, and a body that
    did not parse the first time will not parse on the ninth. Storing it and
    acknowledging is what lets a person look at it.
    """
    raw, headers = _signed({"entity": "event", "event": "subscription.charged"})
    event = RazorpayProvider().parse_webhook(raw_body=raw, headers=headers)

    assert event.kind is BillingEventKind.SUBSCRIPTION_CHARGED
    assert event.user_id is None
    assert event.plan is None
    assert event.amount_minor is None


def test_a_plan_we_have_never_heard_of_is_dropped_not_guessed() -> None:
    """A note naming a plan that does not exist is a mistake somewhere. Guessing
    would grant an allowance nobody bought."""
    body = _charged()
    body["payload"]["subscription"]["entity"]["notes"]["plan"] = "platinum"
    raw, headers = _signed(body)
    event = RazorpayProvider().parse_webhook(raw_body=raw, headers=headers)
    assert event.plan is None


def test_is_configured_reflects_the_key_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    """Checked at call time, never at startup: the keys are empty in every
    developer environment, and an application that will not boot without a
    payment provider cannot be developed."""
    provider = RazorpayProvider()
    monkeypatch.setattr(settings, "razorpay_key_id", "")
    monkeypatch.setattr(settings, "razorpay_key_secret", "")
    assert provider.is_configured() is False

    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_x")
    monkeypatch.setattr(settings, "razorpay_key_secret", "s3cret")
    assert provider.is_configured() is True


async def test_razorpay_reports_that_it_hosts_no_portal() -> None:
    """Stripe has a customer portal. Razorpay does not, and the honest answer is
    `None` with a reason — not an invented URL, and not a rebuilt card manager
    that contract §7 explicitly declines to build."""
    result = await RazorpayProvider().create_portal_session(
        provider_customer_id="cust_1", return_url="http://localhost:3123/settings/billing"
    )
    assert result.url is None
    assert result.reason

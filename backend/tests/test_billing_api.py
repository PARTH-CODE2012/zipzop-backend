"""The billing endpoints — contract §7.

The pricing table, checkout, the portal, cancellation and the ledger, driven
over HTTP. The provider is replaced by a fake that records what it was asked
for: everything worth asserting here is on our side of that call, and the real
adapter's own behaviour is covered in `test_billing_razorpay.py`.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CreditBucket,
    CreditLedgerEntry,
    LedgerReason,
    PaymentProvider,
    Plan,
    PlanCode,
    Subscription,
    User,
)
from app.services.billing import service
from app.services.billing.providers.base import (
    BillingProvider,
    CheckoutSession,
    PortalSession,
    WebhookEvent,
)

pytestmark = pytest.mark.anyio

V1 = "/v1"
RETURN_URL = "http://localhost:3123/settings/billing"


class FakeProvider(BillingProvider):
    """Records what it was asked for, and answers as the real one would."""

    provider = PaymentProvider.RAZORPAY
    calls: ClassVar[list[tuple[str, dict[str, Any]]]] = []

    def is_configured(self) -> bool:
        return True

    async def ensure_plan(
        self, *, plan: PlanCode, display_name: str, currency: str, amount_minor: int
    ) -> str:
        FakeProvider.calls.append(
            ("ensure_plan", {"plan": plan, "currency": currency, "amount": amount_minor})
        )
        return f"plan_{plan.value}_{currency}"

    async def create_checkout(self, **kwargs: Any) -> CheckoutSession:
        FakeProvider.calls.append(("create_checkout", kwargs))
        return CheckoutSession(
            provider=self.provider,
            url="https://rzp.io/i/fake-subscription",
            provider_reference=f"sub_{uuid.uuid4().hex[:10]}",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )

    async def create_topup(self, **kwargs: Any) -> CheckoutSession:
        FakeProvider.calls.append(("create_topup", kwargs))
        return CheckoutSession(
            provider=self.provider,
            url="https://rzp.io/i/fake-topup",
            provider_reference=f"plink_{uuid.uuid4().hex[:10]}",
        )

    async def create_portal_session(self, **kwargs: Any) -> PortalSession:
        return PortalSession(url=None, reason="Razorpay does not host a customer portal.")

    async def cancel_subscription(self, **kwargs: Any) -> None:
        FakeProvider.calls.append(("cancel_subscription", kwargs))

    def verify_signature(self, **kwargs: Any) -> None:  # pragma: no cover - not used here
        return None

    def parse_webhook(self, **kwargs: Any) -> WebhookEvent:  # pragma: no cover
        raise NotImplementedError


@pytest.fixture(autouse=True)
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> type[FakeProvider]:
    FakeProvider.calls = []
    monkeypatch.setitem(service._ADAPTERS, PaymentProvider.RAZORPAY, FakeProvider)
    return FakeProvider


async def _account(client: AsyncClient) -> tuple[dict[str, str], uuid.UUID]:
    body = (
        await client.post(
            f"{V1}/auth/register",
            json={"email": f"{uuid.uuid4().hex[:12]}@example.com", "password": "hunter2hunter2"},
        )
    ).json()
    return (
        {"Authorization": f"Bearer {body['accessToken']}"},
        uuid.UUID(body["user"]["id"].removeprefix("usr_")),
    )


# --------------------------------------------------------------------------
# GET /plans
# --------------------------------------------------------------------------


async def test_the_pricing_table_needs_no_account(client: AsyncClient) -> None:
    """A pricing page that needs an account to show a price asks people to sign
    up before they know what it costs."""
    response = await client.get(f"{V1}/plans")

    assert response.status_code == 200, response.text
    body = response.json()
    assert {plan["code"] for plan in body["plans"]} == {
        "free",
        "beta",
        "pro",
        "business",
        "studio",
    }


async def test_the_plans_come_back_cheapest_first(client: AsyncClient) -> None:
    body = (await client.get(f"{V1}/plans")).json()
    prices = [plan["priceMinor"] for plan in body["plans"]]
    assert prices == sorted(prices)
    assert [plan["code"] for plan in body["plans"]][:2] == ["free", "beta"]


async def test_a_retired_plan_disappears_from_the_price_list(
    client: AsyncClient, db: AsyncSession
) -> None:
    """🔴 The whole mechanism for withdrawing `beta` when the campaign ends.

    One boolean: the row stays, everyone already subscribed keeps it, and the
    price list stops offering it. An endpoint ignoring `is_public` would make
    the retirement a no-op — discovered on the day it was needed.
    """
    beta = await db.get(Plan, PlanCode.BETA)
    assert beta is not None
    beta.is_public = False
    await db.commit()

    body = (await client.get(f"{V1}/plans")).json()

    assert "beta" not in {plan["code"] for plan in body["plans"]}
    assert "pro" in {plan["code"] for plan in body["plans"]}, "the others are still on sale"


async def test_a_retired_plan_can_no_longer_be_bought(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Hiding it from the list is not enough — the checkout has to refuse it
    too, or a bookmarked pricing page keeps selling it."""
    headers, _ = await _account(client)
    beta = await db.get(Plan, PlanCode.BETA)
    assert beta is not None
    beta.is_public = False
    await db.commit()

    response = await client.post(
        f"{V1}/billing/checkout",
        headers=headers,
        json={"plan": "beta", "currency": "INR", "returnUrl": RETURN_URL},
    )
    assert response.status_code == 404


async def test_prices_come_back_in_the_currency_asked_for(client: AsyncClient) -> None:
    inr = (await client.get(f"{V1}/plans", params={"currency": "INR"})).json()
    usd = (await client.get(f"{V1}/plans", params={"currency": "USD"})).json()

    beta_inr = next(p for p in inr["plans"] if p["code"] == "beta")
    beta_usd = next(p for p in usd["plans"] if p["code"] == "beta")

    assert (beta_inr["priceMinor"], beta_inr["currency"]) == (19_900, "INR")
    assert (beta_usd["priceMinor"], beta_usd["currency"]) == (399, "USD")


async def test_the_country_header_only_suggests(client: AsyncClient) -> None:
    """Contract §7: *a suggestion, not a decision*. VPNs, travellers and
    expatriates make IP unreliable, so the caller can always override it."""
    india = (await client.get(f"{V1}/plans", headers={"CF-IPCountry": "IN"})).json()
    assert india["suggestedCurrency"] == "INR"

    elsewhere = (await client.get(f"{V1}/plans", headers={"CF-IPCountry": "FR"})).json()
    assert elsewhere["suggestedCurrency"] == "USD"

    overridden = (
        await client.get(f"{V1}/plans", params={"currency": "USD"}, headers={"CF-IPCountry": "IN"})
    ).json()
    assert overridden["plans"][0]["currency"] == "USD"


async def test_an_unknown_country_falls_back_rather_than_guessing(
    client: AsyncClient,
) -> None:
    """Cloudflare uses `XX` for unknown and `T1` for Tor. Treating either as a
    country code would suggest a currency at random."""
    body = (await client.get(f"{V1}/plans", headers={"CF-IPCountry": "XX"})).json()
    assert body["suggestedCurrency"] == "USD"


async def test_a_currency_we_cannot_take_is_refused_by_name(client: AsyncClient) -> None:
    response = await client.get(f"{V1}/plans", params={"currency": "GBP"})
    assert response.status_code == 422
    assert response.json()["error"]["details"]["supported"] == ["INR", "USD"]


async def test_the_marketing_figure_tracks_what_a_job_costs(client: AsyncClient) -> None:
    """`approxVideosPerMonth` is derived from `pricing.py`, never written down.

    The moment it stops tracking what a job costs it becomes a promise the
    product does not keep — and it is rounded down, because a pricing page that
    rounds up over-promises in the first month.
    """
    from app.services.billing import catalogue

    body = (await client.get(f"{V1}/plans")).json()
    per_video = catalogue.reference_video_credits()

    for plan in body["plans"]:
        assert plan["approxVideosPerMonth"] == plan["monthlyCredits"] // per_video


async def test_the_queue_label_is_a_word_not_a_time(client: AsyncClient) -> None:
    """*"We do not publish an SLA we have not measured."*"""
    body = (await client.get(f"{V1}/plans")).json()
    labels = {plan["code"]: plan["queueLabel"] for plan in body["plans"]}
    assert labels["free"] == "Standard"
    assert labels["beta"] == "Standard", "beta buys resolution and no watermark, not a queue place"
    assert labels["pro"] == "Fast"


# --------------------------------------------------------------------------
# Checkout
# --------------------------------------------------------------------------


async def test_checkout_returns_a_hosted_page(client: AsyncClient) -> None:
    headers, _ = await _account(client)

    response = await client.post(
        f"{V1}/billing/checkout",
        headers=headers,
        json={"plan": "beta", "currency": "INR", "returnUrl": RETURN_URL},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "razorpay"
    assert body["checkoutUrl"].startswith("https://")


async def test_checkout_records_the_intent_before_the_redirect(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The pending `payments` row is what identifies a webhook whose `notes`
    come back empty — the difference between a paid customer and a support
    ticket nobody can resolve."""
    headers, user_id = await _account(client)

    await client.post(
        f"{V1}/billing/checkout",
        headers=headers,
        json={"plan": "beta", "currency": "INR", "returnUrl": RETURN_URL},
    )

    from app.models import Payment

    payment = (await db.execute(sa.select(Payment).where(Payment.user_id == user_id))).scalar_one()
    assert payment.status.value == "pending"
    assert payment.amount_minor == 19_900
    assert payment.provider_payment_id.startswith("sub_")


async def test_the_subscription_is_not_active_on_return(
    client: AsyncClient, db: AsyncSession
) -> None:
    """🔴 Contract §7: *never assume success from the redirect alone.*

    A user can land on `returnUrl` by pressing back. Nothing about checkout
    grants anything — only the webhook does.
    """
    headers, _ = await _account(client)

    await client.post(
        f"{V1}/billing/checkout",
        headers=headers,
        json={"plan": "pro", "currency": "USD", "returnUrl": RETURN_URL},
    )

    me = (await client.get(f"{V1}/me", headers=headers)).json()
    assert me["subscription"]["plan"] == "free"
    assert me["credits"]["plan"] == 300


async def test_a_return_url_pointing_elsewhere_is_refused(client: AsyncClient) -> None:
    """🔴 An open redirect on the billing path is a phishing page the customer
    reaches from a genuine payment, having just typed card details on a page
    that really was the provider's."""
    headers, _ = await _account(client)

    response = await client.post(
        f"{V1}/billing/checkout",
        headers=headers,
        json={"plan": "beta", "returnUrl": "https://evil.example/steal"},
    )
    assert response.status_code == 422


async def test_a_lookalike_origin_is_refused_too(client: AsyncClient) -> None:
    """`localhost:3123.evil.example` passes a naive `endswith`, and that is the
    attack. The check is origin-exact."""
    headers, _ = await _account(client)

    response = await client.post(
        f"{V1}/billing/checkout",
        headers=headers,
        json={"plan": "beta", "returnUrl": "https://localhost:3123.evil.example/x"},
    )
    assert response.status_code == 422


async def test_the_free_plan_cannot_be_bought(client: AsyncClient) -> None:
    headers, _ = await _account(client)
    response = await client.post(
        f"{V1}/billing/checkout",
        headers=headers,
        json={"plan": "free", "returnUrl": RETURN_URL},
    )
    assert response.status_code == 403


async def test_an_unknown_plan_is_a_404(client: AsyncClient) -> None:
    headers, _ = await _account(client)
    response = await client.post(
        f"{V1}/billing/checkout",
        headers=headers,
        json={"plan": "platinum", "returnUrl": RETURN_URL},
    )
    assert response.status_code == 404


async def test_a_downgrade_is_scheduled_rather_than_sold(
    client: AsyncClient, db: AsyncSession
) -> None:
    """§8.3: downgrades apply at the next boundary, so nobody loses credits they
    are half way through using. Sending the user to a payment page for it would
    be asking them to pay in order to receive less.

    ⚠️ **The row, not the status code.** This route first reported the downgrade
    by raising — and `get_session` rolls back on any exception, so the response
    announced a scheduled downgrade that was never written. A test asserting
    only the response would have passed against code that did nothing.
    """
    headers, user_id = await _account(client)
    subscription = (
        await db.execute(sa.select(Subscription).where(Subscription.user_id == user_id))
    ).scalar_one()
    subscription.plan = PlanCode.BUSINESS
    await db.commit()

    response = await client.post(
        f"{V1}/billing/checkout",
        headers=headers,
        json={"plan": "beta", "currency": "USD", "returnUrl": RETURN_URL},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scheduledPlan"] == "beta"
    assert body["checkoutUrl"] is None, "there is nothing to pay for a downgrade"
    assert body["effectiveAt"]

    await db.refresh(subscription)
    assert subscription.pending_plan is PlanCode.BETA
    assert subscription.plan is PlanCode.BUSINESS, "nothing changes until the boundary"


async def test_the_provider_is_derived_from_the_currency(client: AsyncClient) -> None:
    """§8.2: the provider is not a client parameter. Rupees go to Razorpay
    because that is the market it exists for."""
    headers, _ = await _account(client)
    response = await client.post(
        f"{V1}/billing/checkout",
        headers=headers,
        json={"plan": "beta", "currency": "INR", "returnUrl": RETURN_URL},
    )
    assert response.json()["provider"] == "razorpay"


async def test_the_provider_plan_id_is_created_once_and_remembered(
    client: AsyncClient, db: AsyncSession, fake_provider: type[FakeProvider]
) -> None:
    """Creating the same plan twice on the provider's side leaves two ids for
    one price, and subscriptions split across them."""
    first_headers, _ = await _account(client)
    second_headers, _ = await _account(client)

    for headers in (first_headers, second_headers):
        await client.post(
            f"{V1}/billing/checkout",
            headers=headers,
            json={"plan": "beta", "currency": "INR", "returnUrl": RETURN_URL},
        )

    created = [call for call in fake_provider.calls if call[0] == "ensure_plan"]
    assert len(created) == 1, "the provider plan was created twice for one price"

    from app.models import ProviderPlan

    rows = (await db.execute(sa.select(ProviderPlan))).scalars().all()
    assert len(rows) == 1


# --------------------------------------------------------------------------
# Top-ups
# --------------------------------------------------------------------------


async def test_the_pack_list_is_served_rather_than_hardcoded(client: AsyncClient) -> None:
    body = (await client.get(f"{V1}/billing/topup-packs", params={"currency": "USD"})).json()
    assert {pack["code"] for pack in body["packs"]} >= {"credits_5000"}
    assert all(pack["currency"] == "USD" for pack in body["packs"])


async def test_topup_returns_a_hosted_page(client: AsyncClient) -> None:
    headers, _ = await _account(client)
    response = await client.post(
        f"{V1}/billing/topup",
        headers=headers,
        json={"packCode": "credits_5000", "currency": "USD", "returnUrl": RETURN_URL},
    )
    assert response.status_code == 200, response.text
    assert response.json()["checkoutUrl"].startswith("https://")


async def test_an_unknown_pack_is_a_404(client: AsyncClient) -> None:
    headers, _ = await _account(client)
    response = await client.post(
        f"{V1}/billing/topup",
        headers=headers,
        json={"packCode": "credits_99999", "returnUrl": RETURN_URL},
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------
# The portal
# --------------------------------------------------------------------------


async def test_the_portal_reports_its_own_absence(client: AsyncClient) -> None:
    """Contract §7 amendment. Razorpay hosts no customer portal; inventing a URL
    that 404s would be worse than saying so, and rebuilding card management is
    what §7 explicitly declines to do."""
    headers, _ = await _account(client)
    response = await client.post(
        f"{V1}/billing/portal", headers=headers, json={"returnUrl": RETURN_URL}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["portalUrl"] is None
    assert body["reason"]


# --------------------------------------------------------------------------
# Cancellation
# --------------------------------------------------------------------------


async def test_cancelling_says_what_it_costs(client: AsyncClient, db: AsyncSession) -> None:
    """🔴 Contract §7: the response is written to be shown *before* the user
    confirms. Losing 1,840 credits is worth knowing about in advance, and saying
    so at the moment of cancelling is fairer — and better retention — than
    discovering it a week later."""
    headers, user_id = await _account(client)
    user = await db.get(User, user_id)
    assert user is not None
    user.plan_credits = 1_840
    user.topup_credits = 500
    user.facemap_seconds = 240
    subscription = (
        await db.execute(sa.select(Subscription).where(Subscription.user_id == user_id))
    ).scalar_one()
    subscription.plan = PlanCode.PRO
    subscription.provider = PaymentProvider.RAZORPAY
    subscription.provider_subscription_id = "sub_live_1"
    await db.commit()

    response = await client.post(
        f"{V1}/billing/cancel", headers=headers, json={"atPeriodEnd": True}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cancelAtPeriodEnd"] is True
    assert body["creditsLostAtPeriodEnd"] == {"plan": 1_840, "facemapSeconds": 240}
    assert body["creditsKept"] == {"topup": 500}
    assert body["accessUntil"]


async def test_cancelling_takes_nothing_away_today(client: AsyncClient, db: AsyncSession) -> None:
    """§8.3: access and credits continue to `current_period_end`. The month is
    paid for; taking it back early would be keeping the money and withdrawing
    the service."""
    headers, user_id = await _account(client)
    user = await db.get(User, user_id)
    assert user is not None
    user.plan_credits = 1_840
    await db.commit()

    await client.post(f"{V1}/billing/cancel", headers=headers, json={"atPeriodEnd": True})

    await db.refresh(user)
    assert user.plan_credits == 1_840


async def test_cancelling_survives_a_provider_that_will_not_answer(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ A provider that is briefly unreachable must not stop somebody
    cancelling.

    The alternative is a customer who tried to cancel, was told it failed, and
    is charged again — which is a chargeback and a review. A failed provider
    call costs one refund at worst; a cancel button that does not work costs
    more than that.
    """
    from app.services.billing.providers.base import ProviderError

    async def _refuse(_self: Any, **_: Any) -> None:
        raise ProviderError("provider is down")

    monkeypatch.setattr(FakeProvider, "cancel_subscription", _refuse)

    headers, user_id = await _account(client)
    subscription = (
        await db.execute(sa.select(Subscription).where(Subscription.user_id == user_id))
    ).scalar_one()
    subscription.plan = PlanCode.PRO
    subscription.provider = PaymentProvider.RAZORPAY
    subscription.provider_subscription_id = "sub_live_2"
    await db.commit()

    response = await client.post(
        f"{V1}/billing/cancel", headers=headers, json={"atPeriodEnd": True}
    )

    assert response.status_code == 200
    await db.refresh(subscription)
    assert subscription.cancel_at_period_end is True


# --------------------------------------------------------------------------
# The ledger
# --------------------------------------------------------------------------


async def test_the_ledger_shows_every_movement_with_its_bucket(client: AsyncClient) -> None:
    """A first-class endpoint, not an admin tool: this is what a support
    conversation about *"where did my credits go"* is answered from."""
    headers, _ = await _account(client)

    response = await client.get(f"{V1}/credits/ledger", headers=headers)

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["bucket"] == "plan"
    assert items[0]["reason"] == "signup_grant"
    assert items[0]["delta"] == 300
    assert items[0]["balanceAfter"] == 300


async def test_the_ledger_pages_newest_first(client: AsyncClient, db: AsyncSession) -> None:
    headers, user_id = await _account(client)
    user = await db.get(User, user_id)
    assert user is not None
    for index in range(5):
        db.add(
            CreditLedgerEntry(
                user_id=user_id,
                bucket=CreditBucket.TOPUP,
                delta=10,
                reason=LedgerReason.ADMIN_GRANT,
                balance_after=10 * (index + 1),
            )
        )
    await db.commit()

    first = (await client.get(f"{V1}/credits/ledger", headers=headers, params={"limit": 3})).json()
    assert len(first["items"]) == 3
    assert first["nextCursor"]
    assert first["items"][0]["id"] > first["items"][-1]["id"]

    second = (
        await client.get(
            f"{V1}/credits/ledger",
            headers=headers,
            params={"limit": 3, "cursor": first["nextCursor"]},
        )
    ).json()
    assert len(second["items"]) == 3
    assert second["nextCursor"] is None
    assert {item["id"] for item in first["items"]}.isdisjoint(
        {item["id"] for item in second["items"]}
    )


async def test_a_cursor_we_did_not_issue_starts_from_the_top(client: AsyncClient) -> None:
    """The harmless answer. A 400 on a link somebody pasted helps nobody."""
    headers, _ = await _account(client)
    response = await client.get(
        f"{V1}/credits/ledger", headers=headers, params={"cursor": "not-a-cursor"}
    )
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1


async def test_the_ledger_is_only_ever_your_own(client: AsyncClient, db: AsyncSession) -> None:
    mine, _ = await _account(client)
    _, other_id = await _account(client)

    items = (await client.get(f"{V1}/credits/ledger", headers=mine)).json()["items"]
    rows = (
        (
            await db.execute(
                sa.select(CreditLedgerEntry.id).where(CreditLedgerEntry.user_id == other_id)
            )
        )
        .scalars()
        .all()
    )

    assert {item["id"] for item in items}.isdisjoint(set(rows))


async def test_the_ledger_needs_an_account(client: AsyncClient) -> None:
    assert (await client.get(f"{V1}/credits/ledger")).status_code == 401

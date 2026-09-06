"""The webhook route and what a delivery does — §8.5, end to end.

Four steps, and each of the four is a separate way to lose money:

1. **verify** — an unverified billing callback is a way to grant a plan for free;
2. **store** — the row is what makes a redelivery a duplicate rather than a
   second month;
3. **200 immediately** — both providers read a slow answer as a failure and
   retry, so work done inline becomes a second charge attempt;
4. **process asynchronously**, dropping a period already granted.

The route tests below cover the first three. The event-application tests cover what step 4
then does to the ledger, driven through the same code the worker runs.
"""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    CreditBucket,
    CreditLedgerEntry,
    LedgerReason,
    Payment,
    PaymentKind,
    PaymentProvider,
    PaymentStatus,
    Plan,
    PlanCode,
    ProviderEvent,
    Subscription,
    SubStatus,
    User,
)
from app.services.billing import service
from app.services.billing.providers.razorpay import RazorpayProvider

pytestmark = pytest.mark.anyio

V1 = "/v1"
SECRET = "whsec_test_only_not_a_real_razorpay_secret"


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "razorpay_webhook_secret", SECRET)


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Capture the Celery send instead of making one.

    The route must hand the event to a worker, and it must do so **after** its
    own commit — a message for a row that is not yet visible is a task that
    finds nothing.
    """
    sent: list[tuple[Any, ...]] = []
    from app.workers.tasks import billing as billing_task

    monkeypatch.setattr(
        billing_task.process_provider_event,
        "apply_async",
        lambda *a, **k: sent.append((a, k)),
    )
    return sent


def _sign(body: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body).encode()
    return raw, {
        "X-Razorpay-Signature": hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest(),
        "X-Razorpay-Event-Id": f"evt_{uuid.uuid4().hex[:12]}",
        "Content-Type": "application/json",
    }


def _charged(user_id: uuid.UUID, *, plan: str = "beta", amount: int = 19_900) -> dict[str, Any]:
    now = int(datetime.now(UTC).timestamp())
    return {
        "entity": "event",
        "event": "subscription.charged",
        "payload": {
            "subscription": {
                "entity": {
                    "id": f"sub_{uuid.uuid4().hex[:10]}",
                    "current_start": now,
                    "current_end": now + 30 * 86_400,
                    "notes": {"user_id": str(user_id), "plan": plan},
                }
            },
            "payment": {
                "entity": {
                    "id": f"pay_{uuid.uuid4().hex[:10]}",
                    "amount": amount,
                    "currency": "INR",
                }
            },
        },
    }


async def _account(db: AsyncSession, **kwargs: Any) -> tuple[User, Subscription]:
    user = User(
        email=f"{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="not-a-real-hash",
        **kwargs,
    )
    db.add(user)
    await db.flush()
    started = datetime.now(UTC) - timedelta(days=31)
    subscription = Subscription(
        user_id=user.id,
        plan=PlanCode.FREE,
        status=SubStatus.ACTIVE,
        current_period_start=started,
        current_period_end=started + timedelta(days=30),
    )
    db.add(subscription)
    await db.flush()
    return user, subscription


# --------------------------------------------------------------------------
# 1 — verify
# --------------------------------------------------------------------------


async def test_an_unsigned_callback_is_refused(
    client: AsyncClient, db: AsyncSession, enqueued: list[Any]
) -> None:
    user, _ = await _account(db)
    raw, _ = _sign(_charged(user.id))

    response = await client.post(
        f"{V1}/webhooks/razorpay", content=raw, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "WEBHOOK_REJECTED"
    assert enqueued == [], "an unverified callback reached a worker"


async def test_a_forged_signature_stores_nothing(
    client: AsyncClient, db: AsyncSession, enqueued: list[Any]
) -> None:
    """🔴 The attack this endpoint exists to stop: granting yourself a plan.

    Nothing may be written before the signature verifies — not the event, not a
    grant. An attacker who can make us store rows can fill a table even if they
    cannot make us act on them.
    """
    user, _ = await _account(db)
    raw, headers = _sign(_charged(user.id))
    headers["X-Razorpay-Signature"] = "0" * 64

    response = await client.post(f"{V1}/webhooks/razorpay", content=raw, headers=headers)

    assert response.status_code == 400
    stored = (await db.execute(sa.select(sa.func.count()).select_from(ProviderEvent))).scalar()
    assert stored == 0


async def test_stripe_is_refused_rather_than_pretended(
    client: AsyncClient, db: AsyncSession, enqueued: list[Any]
) -> None:
    """Stripe is deferred, not dropped. The route is listed because the contract
    lists it, and it says so rather than half-working."""
    raw, headers = _sign({"entity": "event", "event": "invoice.paid"})
    response = await client.post(f"{V1}/webhooks/stripe", content=raw, headers=headers)
    assert response.status_code == 400


# --------------------------------------------------------------------------
# 2 and 3 — store, then acknowledge
# --------------------------------------------------------------------------


async def test_a_verified_callback_is_stored_and_acknowledged(
    client: AsyncClient, db: AsyncSession, enqueued: list[Any]
) -> None:
    user, _ = await _account(db)
    raw, headers = _sign(_charged(user.id))

    response = await client.post(f"{V1}/webhooks/razorpay", content=raw, headers=headers)

    assert response.status_code == 200
    stored = (await db.execute(sa.select(ProviderEvent))).scalars().all()
    assert len(stored) == 1
    assert stored[0].event_type == "subscription.charged"
    assert stored[0].processed_at is None, "the route must not do the work itself"
    assert len(enqueued) == 1, "the event was stored but never handed to a worker"


async def test_the_route_grants_nothing_itself(
    client: AsyncClient, db: AsyncSession, enqueued: list[Any]
) -> None:
    """Step 3, and the reason for it.

    Both providers treat a slow response as a failure and retry. A handler that
    granted the period inline would turn one slow query into a second charge
    attempt — and the retry would then be dropped as a duplicate, so the money
    moves and the allowance does not.
    """
    user, _ = await _account(db, plan_credits=0)
    raw, headers = _sign(_charged(user.id))

    await client.post(f"{V1}/webhooks/razorpay", content=raw, headers=headers)

    await db.refresh(user)
    assert user.plan_credits == 0
    movements = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(CreditLedgerEntry)
            .where(CreditLedgerEntry.user_id == user.id)
        )
    ).scalar()
    assert movements == 0


async def test_a_redelivery_is_acknowledged_and_dropped(
    client: AsyncClient, db: AsyncSession, enqueued: list[Any]
) -> None:
    """⚠️ Both providers retry, sometimes for days.

    The idempotency is the primary key on `(provider, event_id)` — the
    database's, not ours — which is why the insert comes before the processing.
    """
    user, _ = await _account(db)
    raw, headers = _sign(_charged(user.id))

    first = await client.post(f"{V1}/webhooks/razorpay", content=raw, headers=headers)
    second = await client.post(f"{V1}/webhooks/razorpay", content=raw, headers=headers)

    assert (first.status_code, second.status_code) == (200, 200)
    stored = (await db.execute(sa.select(sa.func.count()).select_from(ProviderEvent))).scalar()
    assert stored == 1
    assert len(enqueued) == 1, "a duplicate was handed to a worker a second time"


# --------------------------------------------------------------------------
# 4 — what the delivery then does
# --------------------------------------------------------------------------


async def _apply(db: AsyncSession, body: dict[str, Any]) -> Any:
    """Run a delivery through exactly the path the worker runs."""
    raw = json.dumps(body).encode()
    event = RazorpayProvider().parse_webhook(raw_body=raw, headers={})
    return await service.apply_event(db, event)


async def test_a_charge_grants_the_period(db: AsyncSession) -> None:
    user, _ = await _account(db, plan_credits=0)

    outcome = await _apply(db, _charged(user.id, plan="beta"))

    beta = await db.get(Plan, PlanCode.BETA)
    assert beta is not None
    assert outcome.action == "granted"
    assert user.plan_credits == beta.monthly_credits

    subscription = await service.live_subscription(db, user.id)
    assert subscription is not None
    assert subscription.plan is PlanCode.BETA
    assert subscription.provider is PaymentProvider.RAZORPAY
    assert subscription.currency == "INR"


async def test_the_same_charge_twice_grants_once(db: AsyncSession) -> None:
    """Belt and braces behind the primary key: even if the same event reached
    the service twice, the period check refuses the second."""
    user, _ = await _account(db, plan_credits=0)
    body = _charged(user.id)

    first = await _apply(db, body)
    second = await _apply(db, body)

    beta = await db.get(Plan, PlanCode.BETA)
    assert beta is not None
    assert (first.action, second.action) == ("granted", "already_granted")
    assert user.plan_credits == beta.monthly_credits


async def test_a_failed_renewal_is_past_due_and_keeps_the_plan(db: AsyncSession) -> None:
    """§8.3: the provider retries on its own schedule, and cutting service off on
    a first decline turns an expired card into a lost customer."""
    user, subscription = await _account(db)
    subscription.plan = PlanCode.PRO
    await db.flush()

    body = _charged(user.id, plan="pro")
    body["event"] = "subscription.halted"
    outcome = await _apply(db, body)

    assert outcome.action == "past_due"
    assert subscription.status is SubStatus.PAST_DUE
    assert subscription.plan is PlanCode.PRO, "a failed payment is not a downgrade"


async def test_a_cancellation_schedules_rather_than_strips(db: AsyncSession) -> None:
    user, subscription = await _account(db, plan_credits=1_500)
    subscription.plan = PlanCode.PRO
    await db.flush()

    body = _charged(user.id, plan="pro")
    body["event"] = "subscription.cancelled"
    outcome = await _apply(db, body)

    assert outcome.action == "cancel_scheduled"
    assert subscription.cancel_at_period_end is True
    assert user.plan_credits == 1_500, "credits were taken before the period ended"


async def test_an_event_naming_nobody_is_recorded_and_not_guessed(
    db: AsyncSession,
) -> None:
    """Money we have taken and cannot attribute needs a person, not a retry.

    Guessing — the newest account, the matching amount — would grant a plan to
    somebody who did not buy it and leave the person who did still waiting.
    """
    body = _charged(uuid.uuid4())
    outcome = await _apply(db, body)
    assert outcome.action == "unattributed"


async def test_a_payment_row_written_at_checkout_identifies_a_noteless_event(
    db: AsyncSession,
) -> None:
    """The fallback that makes the pending `payments` row worth writing.

    `notes` coming back empty is a known failure mode, and a paid customer we
    cannot identify is the worst outcome on this path.
    """
    user, _ = await _account(db, plan_credits=0)
    body = _charged(user.id)
    reference = body["payload"]["subscription"]["entity"]["id"]
    db.add(
        Payment(
            user_id=user.id,
            provider=PaymentProvider.RAZORPAY,
            provider_payment_id=reference,
            kind=PaymentKind.SUBSCRIPTION,
            status=PaymentStatus.PENDING,
            amount_minor=19_900,
            currency="INR",
        )
    )
    await db.flush()

    # The delivery arrives with no notes at all.
    body["payload"]["subscription"]["entity"]["notes"] = {}
    body["payload"]["payment"]["entity"].pop("id")
    outcome = await _apply(db, body)

    assert outcome.action == "granted"
    assert outcome.user_id == str(user.id)


# --------------------------------------------------------------------------
# Top-ups
# --------------------------------------------------------------------------


def _link_paid(user_id: uuid.UUID, pack: str = "credits_5000") -> dict[str, Any]:
    return {
        "entity": "event",
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": f"plink_{uuid.uuid4().hex[:10]}",
                    "amount": 224_900,
                    "currency": "INR",
                    "notes": {"user_id": str(user_id), "kind": "topup", "pack": pack},
                }
            }
        },
    }


async def test_a_topup_lands_in_the_bucket_that_never_expires(db: AsyncSession) -> None:
    user, _ = await _account(db, topup_credits=0)

    outcome = await _apply(db, _link_paid(user.id))

    assert outcome.action == "topup_granted"
    assert user.topup_credits == 5_000

    row = (
        await db.execute(sa.select(CreditLedgerEntry).where(CreditLedgerEntry.user_id == user.id))
    ).scalar_one()
    assert row.bucket is CreditBucket.TOPUP
    assert row.reason is LedgerReason.TOPUP_PURCHASE
    assert row.payment_id is not None, "the ledger row should name the payment that bought it"


async def test_a_redelivered_topup_grants_once(db: AsyncSession) -> None:
    user, _ = await _account(db, topup_credits=0)
    body = _link_paid(user.id)

    await _apply(db, body)
    second = await _apply(db, body)

    assert second.action == "already_granted"
    assert user.topup_credits == 5_000


async def test_the_pack_comes_from_the_notes_not_the_amount(db: AsyncSession) -> None:
    """An amount can be matched to the wrong pack by a currency conversion or a
    partial payment. Granting the wrong number of credits is worse than granting
    none, so an unknown pack grants nothing and is logged."""
    user, _ = await _account(db, topup_credits=0)
    body = _link_paid(user.id, pack="credits_99999")

    outcome = await _apply(db, body)

    assert outcome.action == "unknown_pack"
    assert user.topup_credits == 0


async def test_a_redelivery_survives_the_commit_production_makes(
    engine: Any, enqueued: list[Any]
) -> None:
    """⚠️ The test the duplicate case needed — and the reason it looks unusual.

    Answering `200` is only half of it: the request then has to *end*. The first
    version of this route added the `ProviderEvent` **before** opening the
    savepoint, so the rejected row stayed pending and the commit `get_session`
    performs at the end of the request retried the same doomed INSERT.

    That turned the correct answer into the worst one: `500` on a redelivery,
    which the provider reads as a failure and retries, every retry `500`ing
    again — a loop that ends only when somebody notices.

    🔴 **The rest of this suite structurally cannot see it.** `conftest`'s
    `get_session` override wraps every request in a savepoint so the test can be
    rolled back; production wraps it in nothing. The savepoint absorbs exactly
    the failure this test is about, so the assertion has to run against a
    session shaped the way production shapes one — committed for real, and
    cleaned up by hand.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db import get_session
    from app.main import create_app

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    app = create_app()

    async def _production_shaped_session() -> Any:
        async with maker() as session:
            yield session
            await session.commit()  # what the real dependency does, and what failed

    app.dependency_overrides[get_session] = _production_shaped_session

    created: list[Any] = []
    try:
        async with maker() as setup:
            user, _ = await _account(setup)
            await setup.commit()
            created.append(user.id)

        raw, headers = _sign(_charged(user.id))
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            first = await ac.post(f"{V1}/webhooks/razorpay", content=raw, headers=headers)
            second = await ac.post(f"{V1}/webhooks/razorpay", content=raw, headers=headers)

        assert first.status_code == 200
        assert second.status_code == 200, (
            "a redelivery 500s; the provider will now retry it forever"
        )
    finally:
        async with maker() as cleanup:
            await cleanup.execute(sa.delete(ProviderEvent))
            for user_id in created:
                await cleanup.execute(sa.delete(User).where(User.id == user_id))
            await cleanup.commit()

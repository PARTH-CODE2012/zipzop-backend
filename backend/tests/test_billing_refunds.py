"""Credits revoked on refund — the project lead's rule of 8 October 2026.

*"Fully revoke credits on refund — if a customer already used some, just claw
back whatever's left unused."* Every test runs a real Razorpay delivery through
the worker's own path, and pins one property of the money:

* what the payment granted is revoked, and nothing it did not grant;
* what was spent stays spent, and a balance never goes below zero;
* a month that has already been replaced by the next one is not taken out of
  the next one;
* a refund delivered twice revokes once.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CreditBucket,
    CreditLedgerEntry,
    LedgerReason,
    Payment,
    Plan,
    PlanCode,
    Subscription,
    SubStatus,
    User,
)
from app.services.billing import service
from app.services.billing.providers.razorpay import RazorpayProvider
from app.services.credits import CreditLedger


async def _account(db: AsyncSession, **kwargs: Any) -> User:
    user = User(
        email=f"{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="not-a-real-hash",
        **kwargs,
    )
    db.add(user)
    await db.flush()
    started = datetime.now(UTC) - timedelta(days=31)
    db.add(
        Subscription(
            user_id=user.id,
            plan=PlanCode.FREE,
            status=SubStatus.ACTIVE,
            current_period_start=started,
            current_period_end=started + timedelta(days=30),
        )
    )
    await db.flush()
    return user


def _charged(user_id: uuid.UUID, *, start: datetime | None = None) -> dict[str, Any]:
    begin = int((start or datetime.now(UTC)).timestamp())
    return {
        "entity": "event",
        "event": "subscription.charged",
        "payload": {
            "subscription": {
                "entity": {
                    "id": f"sub_{uuid.uuid4().hex[:10]}",
                    "current_start": begin,
                    "current_end": begin + 30 * 86_400,
                    "notes": {"user_id": str(user_id), "plan": "beta"},
                }
            },
            "payment": {
                "entity": {
                    "id": f"pay_{uuid.uuid4().hex[:10]}",
                    "amount": 19_900,
                    "currency": "INR",
                }
            },
        },
    }


def _topup(user_id: uuid.UUID) -> dict[str, Any]:
    return {
        "entity": "event",
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": f"plink_{uuid.uuid4().hex[:10]}",
                    "amount": 224_900,
                    "currency": "INR",
                    "notes": {"user_id": str(user_id), "kind": "topup", "pack": "credits_5000"},
                }
            }
        },
    }


def _refund(payment: Payment, event: str = "refund.processed") -> dict[str, Any]:
    entity = "refund" if event.startswith("refund") else "dispute"
    return {
        "entity": "event",
        "event": event,
        "payload": {
            entity: {
                "entity": {
                    "id": f"{entity[:4]}_{uuid.uuid4().hex[:10]}",
                    "payment_id": payment.provider_payment_id,
                    "amount": payment.amount_minor,
                    "currency": payment.currency,
                }
            }
        },
    }


async def _apply(db: AsyncSession, body: dict[str, Any]) -> Any:
    event = RazorpayProvider().parse_webhook(raw_body=json.dumps(body).encode(), headers={})
    return await service.apply_event(db, event)


async def _payments(db: AsyncSession, user: User) -> list[Payment]:
    rows = await db.execute(
        sa.select(Payment).where(Payment.user_id == user.id).order_by(Payment.settled_at)
    )
    return list(rows.scalars().all())


async def _spend(db: AsyncSession, user: User, bucket: CreditBucket, amount: int) -> None:
    """Credits used by a job, as the ledger records a reservation."""
    await CreditLedger(db)._write(
        user=user,
        bucket=bucket,
        delta=-amount,
        reason=LedgerReason.RESERVE,
        job_id=None,
        note="spent in a test",
    )
    await db.flush()


async def _beta_allowance(db: AsyncSession) -> int:
    beta = await db.get(Plan, PlanCode.BETA)
    assert beta is not None
    return beta.monthly_credits


# --------------------------------------------------------------------------
# Top-ups
# --------------------------------------------------------------------------


async def test_a_refunded_topup_takes_its_credits_back(db: AsyncSession) -> None:
    user = await _account(db, topup_credits=0)
    await _apply(db, _topup(user.id))
    (payment,) = await _payments(db, user)

    outcome = await _apply(db, _refund(payment))

    assert outcome.details["credits_revoked"] == {"topup": 5_000}
    assert user.topup_credits == 0
    row = await db.scalar(
        sa.select(CreditLedgerEntry).where(
            CreditLedgerEntry.user_id == user.id,
            CreditLedgerEntry.reason == LedgerReason.PAYMENT_REVERSAL,
        )
    )
    assert row is not None and row.payment_id == payment.id, "the row names the payment"


async def test_what_was_already_spent_stays_spent(db: AsyncSession) -> None:
    """Only the unused part comes back, and the balance never goes negative:
    spent credits are not turned into a debt."""
    user = await _account(db, topup_credits=0)
    await _apply(db, _topup(user.id))
    await _spend(db, user, CreditBucket.TOPUP, 3_200)
    (payment,) = await _payments(db, user)

    outcome = await _apply(db, _refund(payment))

    assert outcome.details["credits_revoked"] == {"topup": 1_800}
    assert user.topup_credits == 0


async def test_a_topup_refund_takes_nothing_it_did_not_grant(db: AsyncSession) -> None:
    """The promo bonus shares the `topup` bucket. A refund of the pack takes
    back the pack, not the bonus."""
    user = await _account(db, topup_credits=0)
    await CreditLedger(db).grant_promo_bonus(user=user, credits=300, code="srvtest")
    await _apply(db, _topup(user.id))
    (payment,) = await _payments(db, user)

    await _apply(db, _refund(payment))

    assert user.topup_credits == 300


async def test_a_refund_delivered_twice_revokes_once(db: AsyncSession) -> None:
    """A redelivery, then a dispute lost on the same payment."""
    user = await _account(db, topup_credits=0)
    await CreditLedger(db).grant_promo_bonus(user=user, credits=300, code="srvtest")
    await _apply(db, _topup(user.id))
    (payment,) = await _payments(db, user)

    first = await _apply(db, _refund(payment))
    again = await _apply(db, _refund(payment))
    dispute = await _apply(db, _refund(payment, event="payment.dispute.lost"))

    assert first.details["credits_revoked"] == {"topup": 5_000}
    assert again.details["credits_revoked"] == {}
    assert dispute.details["credits_revoked"] == {}
    assert user.topup_credits == 300


# --------------------------------------------------------------------------
# Subscriptions
# --------------------------------------------------------------------------


async def test_a_refunded_month_takes_its_allowance_back(db: AsyncSession) -> None:
    allowance = await _beta_allowance(db)
    user = await _account(db, plan_credits=0)
    await _apply(db, _charged(user.id))
    await _spend(db, user, CreditBucket.PLAN, 300)
    (payment,) = await _payments(db, user)

    outcome = await _apply(db, _refund(payment))

    assert outcome.details["credits_revoked"] == {"plan": allowance - 300}
    assert user.plan_credits == 0


async def test_a_refund_after_the_month_was_replaced_takes_nothing_from_the_next(
    db: AsyncSession,
) -> None:
    """The refunded month's credits expired at the boundary. Taking the same
    amount from the following month would charge the customer for a period they
    paid for separately."""
    allowance = await _beta_allowance(db)
    user = await _account(db, plan_credits=0)
    await _apply(db, _charged(user.id))
    await _apply(db, _charged(user.id, start=datetime.now(UTC) + timedelta(days=30)))
    first, second = await _payments(db, user)
    assert second.id != first.id

    outcome = await _apply(db, _refund(first))

    assert outcome.details["credits_revoked"] == {}
    assert user.plan_credits == allowance


async def test_the_month_grant_names_the_payment_that_bought_it(db: AsyncSession) -> None:
    user = await _account(db, plan_credits=0)
    await _apply(db, _charged(user.id))
    (payment,) = await _payments(db, user)

    grant = await db.scalar(
        sa.select(CreditLedgerEntry).where(
            CreditLedgerEntry.user_id == user.id,
            CreditLedgerEntry.reason == LedgerReason.PLAN_GRANT,
            CreditLedgerEntry.bucket == CreditBucket.PLAN,
        )
    )
    assert grant is not None and grant.payment_id == payment.id

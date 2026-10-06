"""The project lead's referral policy (6 October 2026) — docs/24-m7-closure.md §2.6.

Using your own code, or sharing it outside your Discord server, is a violation.
The first cancels the commission it earned and is a warning. The second removes
the code. What each test pins down is the money: what is cancelled, what is
never cancelled twice, and what a removed code stops earning.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CommissionLedgerEntry,
    CommissionReason,
    Payment,
    PaymentKind,
    PaymentProvider,
    PaymentStatus,
    PlanCode,
    PromoCode,
    PromoViolation,
    PromoViolationAction,
    PromoViolationKind,
    Subscription,
    SubStatus,
    User,
)
from app.services import promo, promo_policy
from app.services.billing import service

SELF_USE = PromoViolationKind.SELF_USE
SHARED = PromoViolationKind.SHARED_OUTSIDE_SERVER


async def _code(db: AsyncSession) -> PromoCode:
    row = PromoCode(
        code=f"srv{uuid.uuid4().hex[:8]}",
        owner_label="A Discord server owner",
        bonus_credits=300,
        commission_bps=1500,
    )
    db.add(row)
    await db.flush()
    return row


async def _payment(db: AsyncSession, user: User, amount: int = 399) -> Payment:
    payment = Payment(
        user_id=user.id,
        provider=PaymentProvider.RAZORPAY,
        provider_payment_id=f"pay_{uuid.uuid4().hex[:10]}",
        kind=PaymentKind.SUBSCRIPTION,
        status=PaymentStatus.SUCCEEDED,
        amount_minor=amount,
        currency="USD",
    )
    db.add(payment)
    await db.flush()
    return payment


async def _subscriber(db: AsyncSession, code: PromoCode) -> tuple[User, Payment]:
    """A paying account that came in through `code`, its first commission accrued."""
    user = User(
        email=f"{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="not-a-real-hash",
        promo_code=code.code,
        promo_code_applied_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()
    started = datetime.now(UTC) - timedelta(days=31)
    db.add(
        Subscription(
            user_id=user.id,
            plan=PlanCode.BETA,
            status=SubStatus.ACTIVE,
            current_period_start=started,
            current_period_end=started + timedelta(days=30),
        )
    )
    payment = await _payment(db, user)
    assert await service.accrue_commission(db, user=user, payment=payment) == 59
    return user, payment


async def _violate(
    db: AsyncSession, code: PromoCode, kind: PromoViolationKind, user: User | None = None
) -> promo_policy.ViolationOutcome:
    return await promo_policy.record_violation(db, code=code.code, kind=kind, referred_user=user)


async def _ledger(db: AsyncSession, code: PromoCode, reason: CommissionReason) -> list[Any]:
    return list(
        (
            await db.execute(
                sa.select(CommissionLedgerEntry).where(
                    CommissionLedgerEntry.code == code.code,
                    CommissionLedgerEntry.reason == reason,
                )
            )
        )
        .scalars()
        .all()
    )


async def test_a_first_violation_cancels_that_commission_and_is_a_warning(
    db: AsyncSession,
) -> None:
    code = await _code(db)
    alt, payment = await _subscriber(db, code)
    honest, _ = await _subscriber(db, code)

    outcome = await _violate(db, code, SELF_USE, alt)

    assert (outcome.number, outcome.action) == (1, PromoViolationAction.WARNING)
    assert outcome.reversed_minor == {"USD": 59}
    reversals = await _ledger(db, code, CommissionReason.REVERSAL)
    assert [(r.payment_id, r.amount_minor) for r in reversals] == [(payment.id, -59)]
    assert "referral violation #1: self_use" in (reversals[0].note or "")
    # Only the breach's commission: the honest subscriber's still stands.
    stats = await promo.owner_stats(db, code=code.code)
    assert stats is not None and stats.owed_minor == {"USD": 59}
    assert code.is_active, "a warning leaves the code working"
    assert honest.promo_code == code.code


async def test_the_breach_account_stops_earning_on_later_renewals(db: AsyncSession) -> None:
    """Without the detach, next month's renewal would accrue the same
    commission again, and the violation would keep paying."""
    code = await _code(db)
    alt, _ = await _subscriber(db, code)

    outcome = await _violate(db, code, SELF_USE, alt)

    assert outcome.attribution_removed
    assert alt.promo_code is None
    renewal = await _payment(db, alt)
    assert await service.accrue_commission(db, user=alt, payment=renewal) == 0


async def test_a_second_violation_removes_the_code(db: AsyncSession) -> None:
    code = await _code(db)
    first, _ = await _subscriber(db, code)
    second, _ = await _subscriber(db, code)
    bystander, _ = await _subscriber(db, code)

    await _violate(db, code, SELF_USE, first)
    outcome = await promo_policy.record_violation(
        db, code=code.code, kind=SHARED, referred_user=second, note="posted in a public group"
    )

    assert (outcome.number, outcome.action) == (2, PromoViolationAction.CODE_REMOVED)
    assert code.is_active is False
    # Removed means it grants nothing at sign-up and earns nothing on renewals,
    # even on customers who came in honestly before.
    assert await promo.find(db, code.code) is None
    renewal = await _payment(db, bystander)
    assert await service.accrue_commission(db, user=bystander, payment=renewal) == 0
    # What was legitimately earned before the removal is still owed.
    stats = await promo.owner_stats(db, code=code.code)
    assert stats is not None and stats.owed_minor == {"USD": 59}

    rows = (
        (await db.execute(sa.select(PromoViolation).where(PromoViolation.code == code.code)))
        .scalars()
        .all()
    )
    assert [(r.kind, r.action) for r in rows] == [
        ("self_use", "warning"),
        ("shared_outside_server", "code_removed"),
    ]


async def test_a_refunded_payment_is_not_reversed_twice(db: AsyncSession) -> None:
    """The refund already took the commission back. The violation must not take
    it a second time and leave the owner owing money they never received."""
    code = await _code(db)
    alt, payment = await _subscriber(db, code)
    assert await service.reverse_commission(db, payment=payment, why="refund") == 59

    outcome = await _violate(db, code, SELF_USE, alt)

    assert outcome.reversed_minor == {}
    assert len(await _ledger(db, code, CommissionReason.REVERSAL)) == 1


async def test_the_same_account_reported_twice_is_reversed_once(db: AsyncSession) -> None:
    code = await _code(db)
    alt, _ = await _subscriber(db, code)

    await _violate(db, code, SELF_USE, alt)
    again = await _violate(db, code, SELF_USE, alt)

    assert again.reversed_minor == {}
    assert len(await _ledger(db, code, CommissionReason.REVERSAL)) == 1
    assert again.action is PromoViolationAction.CODE_REMOVED


async def test_an_accrual_whose_payment_is_gone_is_still_cancelled_once(db: AsyncSession) -> None:
    """`payment_id` is `ON DELETE SET NULL`, and the unique index only guards
    rows that have one."""
    code = await _code(db)
    alt, payment = await _subscriber(db, code)
    await db.execute(
        sa.update(CommissionLedgerEntry)
        .where(CommissionLedgerEntry.payment_id == payment.id)
        .values(payment_id=None)
    )
    await db.flush()

    first = await _violate(db, code, SELF_USE, alt)
    second = await _violate(db, code, SELF_USE, alt)

    assert (first.reversed_minor, second.reversed_minor) == ({"USD": 59}, {})


async def test_a_violation_with_no_account_still_counts(db: AsyncSession) -> None:
    """A code seen posted in public before anyone used it cancels nothing, but
    it is still the owner's first violation."""
    code = await _code(db)

    first = await promo_policy.record_violation(db, code=code.code, kind=SHARED)
    second = await promo_policy.record_violation(db, code=code.code, kind=SHARED)

    assert (first.action, first.reversed_minor) == (PromoViolationAction.WARNING, {})
    assert second.action is PromoViolationAction.CODE_REMOVED


async def test_violations_are_counted_per_code(db: AsyncSession) -> None:
    one = await _code(db)
    other = await _code(db)

    await promo_policy.record_violation(db, code=one.code, kind=SHARED)
    outcome = await promo_policy.record_violation(db, code=other.code, kind=SHARED)

    assert (outcome.number, outcome.action) == (1, PromoViolationAction.WARNING)
    assert other.is_active


async def test_the_code_is_matched_as_people_type_it(db: AsyncSession) -> None:
    """CITEXT, like everywhere else a code is read: `SRVABC` is `srvabc`."""
    code = await _code(db)

    outcome = await promo_policy.record_violation(db, code=code.code.upper(), kind=SHARED)

    assert outcome.code == code.code


async def test_an_unknown_code_is_refused(db: AsyncSession) -> None:
    with pytest.raises(LookupError):
        await promo_policy.record_violation(db, code="no-such-code", kind=SHARED)

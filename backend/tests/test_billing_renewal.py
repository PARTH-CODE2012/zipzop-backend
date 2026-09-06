"""The renewal transaction — docs/03-backend-architecture.md §8.4.

**The first test in this file is the one whose failure is a refund and an
apology rather than a bug report.** `topup` credits are bought and never expire;
a renewal that swept them would be taking money already paid. Everything else
here is ordinary correctness.

The second theme is idempotency. Two triggers fire at a period boundary — the
provider's webhook and the hourly sweep — and both are meant to. A boundary
that granted twice would double an allowance every month for every paying
customer, and nothing in the product would look wrong until the compute bill
arrived.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CreditBucket,
    CreditLedgerEntry,
    LedgerReason,
    Plan,
    PlanCode,
    Subscription,
    SubStatus,
    User,
)
from app.services.billing import service

pytestmark = pytest.mark.anyio


async def _subscriber(
    db: AsyncSession,
    *,
    plan: PlanCode = PlanCode.PRO,
    plan_credits: int = 1_840,
    topup_credits: int = 500,
    facemap_seconds: int = 240,
    period_days_ago: int = 31,
) -> tuple[User, Subscription]:
    """An account mid-life: some allowance left, some bought credits, a period
    that ended yesterday."""
    user = User(
        email=f"{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="not-a-real-hash",
        plan_credits=plan_credits,
        topup_credits=topup_credits,
        facemap_seconds=facemap_seconds,
    )
    db.add(user)
    await db.flush()

    started = datetime.now(UTC) - timedelta(days=period_days_ago)
    subscription = Subscription(
        user_id=user.id,
        plan=plan,
        status=SubStatus.ACTIVE,
        current_period_start=started,
        current_period_end=started + timedelta(days=30),
    )
    db.add(subscription)
    await db.flush()
    return user, subscription


# --------------------------------------------------------------------------
# 🔴 The one that matters
# --------------------------------------------------------------------------


async def test_a_renewal_never_touches_topup(db: AsyncSession) -> None:
    """🔴 **Bought credits survive every period boundary.**

    §8.4's SQL sweeps `plan` and `facemap` and leaves `topup` alone. There is no
    branch in `roll_period` that names the bucket at all — this asserts it
    against a real balance anyway, because the guarantee is worth more than the
    argument that it holds, and because a future edit that "tidied up" the loop
    into all three buckets would look entirely reasonable in review.
    """
    user, subscription = await _subscriber(db, topup_credits=500)
    pro = await db.get(Plan, PlanCode.PRO)
    assert pro is not None

    granted = await service.grant_period(
        db,
        user=user,
        subscription=subscription,
        plan=pro,
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC) + timedelta(days=30),
        note="test",
    )

    assert granted is True
    assert user.topup_credits == 500, "topup was swept by a renewal — this is money already paid"

    # And nothing in the ledger claims to have moved it.
    touched_topup = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(CreditLedgerEntry)
            .where(
                CreditLedgerEntry.user_id == user.id,
                CreditLedgerEntry.bucket == CreditBucket.TOPUP,
            )
        )
    ).scalar()
    assert touched_topup == 0


async def test_the_sweep_leaves_topup_alone_too(db: AsyncSession) -> None:
    """The same guarantee through the other trigger.

    Two paths reach a period boundary and they must agree. This one is the only
    path a free user ever takes, and free users are the ones most likely to hold
    a top-up they bought before deciding not to subscribe.
    """
    user, subscription = await _subscriber(db, plan=PlanCode.FREE, topup_credits=1_200)

    outcome = await service.renew_one(db, subscription=subscription, now=datetime.now(UTC))

    free = await db.get(Plan, PlanCode.FREE)
    assert free is not None
    assert outcome == "renewed"
    assert user.plan_credits == free.monthly_credits, "the sweep did no work, so it proves nothing"
    assert user.topup_credits == 1_200


# --------------------------------------------------------------------------
# What the boundary actually does
# --------------------------------------------------------------------------


async def test_the_expired_allowance_is_swept_and_the_new_one_granted(
    db: AsyncSession,
) -> None:
    """Two movements, in that order, and both in the ledger.

    The sweep is written before the grant so the ledger reads as a period
    boundary rather than as an unexplained jump in a balance — which is what a
    support conversation about "where did my credits go" is answered from.
    """
    user, subscription = await _subscriber(db, plan_credits=1_840, facemap_seconds=240)
    pro = await db.get(Plan, PlanCode.PRO)
    assert pro is not None

    await service.grant_period(
        db,
        user=user,
        subscription=subscription,
        plan=pro,
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC) + timedelta(days=30),
        note="test",
    )

    assert user.plan_credits == pro.monthly_credits
    assert user.facemap_seconds == pro.facemap_seconds

    rows = (
        (
            await db.execute(
                sa.select(CreditLedgerEntry)
                .where(CreditLedgerEntry.user_id == user.id)
                .order_by(CreditLedgerEntry.id)
            )
        )
        .scalars()
        .all()
    )
    movements = [(row.reason, row.bucket, row.delta) for row in rows]
    assert movements == [
        (LedgerReason.PLAN_EXPIRY, CreditBucket.PLAN, -1_840),
        (LedgerReason.PLAN_EXPIRY, CreditBucket.FACEMAP, -240),
        (LedgerReason.PLAN_GRANT, CreditBucket.PLAN, pro.monthly_credits),
        (LedgerReason.PLAN_GRANT, CreditBucket.FACEMAP, pro.facemap_seconds),
    ]


async def test_a_zero_balance_writes_no_expiry_row(db: AsyncSession) -> None:
    """The ledger forbids a zero delta, and a row saying nothing expired would
    only make the history harder to read."""
    user, subscription = await _subscriber(db, plan_credits=0, facemap_seconds=0)
    free = await db.get(Plan, PlanCode.FREE)
    assert free is not None

    await service.grant_period(
        db,
        user=user,
        subscription=subscription,
        plan=free,
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC) + timedelta(days=30),
        note="test",
    )

    expiries = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(CreditLedgerEntry)
            .where(
                CreditLedgerEntry.user_id == user.id,
                CreditLedgerEntry.reason == LedgerReason.PLAN_EXPIRY,
            )
        )
    ).scalar()
    assert expiries == 0


# --------------------------------------------------------------------------
# Idempotency — two triggers, one boundary
# --------------------------------------------------------------------------


async def test_the_same_period_is_never_granted_twice(db: AsyncSession) -> None:
    """⚠️ The webhook and the sweep both fire at a boundary, deliberately.

    Without the period check the second one grants a second month. Nothing in
    the product would look wrong; the compute bill is where it would show up.
    """
    user, subscription = await _subscriber(db)
    pro = await db.get(Plan, PlanCode.PRO)
    assert pro is not None
    start = datetime.now(UTC)

    first = await service.grant_period(
        db,
        user=user,
        subscription=subscription,
        plan=pro,
        period_start=start,
        period_end=start + timedelta(days=30),
        note="webhook",
    )
    second = await service.grant_period(
        db,
        user=user,
        subscription=subscription,
        plan=pro,
        period_start=start,
        period_end=start + timedelta(days=30),
        note="sweep",
    )

    assert (first, second) == (True, False)
    assert user.plan_credits == pro.monthly_credits, "the allowance was granted twice"


async def test_a_grant_seconds_after_another_is_the_same_period(db: AsyncSession) -> None:
    """The tolerance, and why it is an hour rather than nothing.

    The provider's clock and ours are not the same clock. A webhook naming
    14:00:03 and a sweep naming 14:00:05 are one boundary, and treating them as
    two would double the month.
    """
    user, subscription = await _subscriber(db)
    pro = await db.get(Plan, PlanCode.PRO)
    assert pro is not None
    start = datetime.now(UTC)

    await service.grant_period(
        db,
        user=user,
        subscription=subscription,
        plan=pro,
        period_start=start,
        period_end=start + timedelta(days=30),
        note="webhook",
    )
    again = await service.grant_period(
        db,
        user=user,
        subscription=subscription,
        plan=pro,
        period_start=start + timedelta(seconds=2),
        period_end=start + timedelta(days=30),
        note="sweep",
    )
    assert again is False


async def test_the_next_month_is_granted(db: AsyncSession) -> None:
    """The tolerance must not swallow a real boundary."""
    user, subscription = await _subscriber(db)
    pro = await db.get(Plan, PlanCode.PRO)
    assert pro is not None
    start = datetime.now(UTC)

    await service.grant_period(
        db,
        user=user,
        subscription=subscription,
        plan=pro,
        period_start=start,
        period_end=start + timedelta(days=30),
        note="first",
    )
    user.plan_credits = 12  # a month of use
    next_month = start + timedelta(days=30)

    granted = await service.grant_period(
        db,
        user=user,
        subscription=subscription,
        plan=pro,
        period_start=next_month,
        period_end=next_month + timedelta(days=30),
        note="second",
    )
    assert granted is True
    assert user.plan_credits == pro.monthly_credits


# --------------------------------------------------------------------------
# The sweep's three outcomes
# --------------------------------------------------------------------------


async def test_a_cancelled_subscription_drops_to_free_at_the_boundary(
    db: AsyncSession,
) -> None:
    """§8.3: access and credits continue to `current_period_end`, then the
    account drops to free and the plan balances are swept. Purchased credits
    survive — they were paid for separately."""
    user, subscription = await _subscriber(db, plan=PlanCode.PRO, topup_credits=500)
    subscription.cancel_at_period_end = True
    await db.flush()

    outcome = await service.renew_one(db, subscription=subscription, now=datetime.now(UTC))

    free = await db.get(Plan, PlanCode.FREE)
    assert free is not None
    assert outcome == "cancelled"
    assert subscription.plan is PlanCode.FREE
    assert subscription.provider is None
    assert subscription.cancel_at_period_end is False, "a cancelled flag left set would sweep again"
    assert user.plan_credits == free.monthly_credits
    assert user.topup_credits == 500


async def test_a_scheduled_downgrade_applies_at_the_boundary(db: AsyncSession) -> None:
    """§8.3: downgrades apply at the next period boundary, so nobody loses
    credits they are half way through using."""
    user, subscription = await _subscriber(db, plan=PlanCode.BUSINESS)
    subscription.pending_plan = PlanCode.BETA
    await db.flush()

    outcome = await service.renew_one(db, subscription=subscription, now=datetime.now(UTC))

    beta = await db.get(Plan, PlanCode.BETA)
    assert beta is not None
    assert outcome == "downgraded"
    assert subscription.plan is PlanCode.BETA
    assert subscription.pending_plan is None, "a pending plan left set would downgrade again"
    assert user.plan_credits == beta.monthly_credits


async def test_only_subscriptions_whose_period_has_ended_are_due(db: AsyncSession) -> None:
    """The sweep must not renew somebody a fortnight early."""
    _, ended = await _subscriber(db, period_days_ago=31)
    _, mid_period = await _subscriber(db, period_days_ago=5)

    due = await service.due_for_renewal(db, now=datetime.now(UTC))
    ids_due = {s.id for s in due}

    assert ended.id in ids_due
    assert mid_period.id not in ids_due


# --------------------------------------------------------------------------
# Upgrades
# --------------------------------------------------------------------------


async def test_an_upgrade_grants_the_difference_not_the_allowance(db: AsyncSession) -> None:
    """§8.3, and the reason it is the difference.

    Handing over the full allowance on top of what is left would make upgrading
    twice in a month a way to print credits: Beta → Pro → Business would grant
    Pro's month and Business's month on top of Beta's.
    """
    user, subscription = await _subscriber(db, plan=PlanCode.BETA, plan_credits=600)
    pro = await db.get(Plan, PlanCode.PRO)
    assert pro is not None

    await service.apply_upgrade(db, user=user, subscription=subscription, plan=pro)

    assert user.plan_credits == pro.monthly_credits, "topped up to the new allowance, not stacked"
    assert subscription.plan is PlanCode.PRO


async def test_an_upgrade_never_takes_credits_away(db: AsyncSession) -> None:
    """Someone holding more than the new plan grants — from a top-up, or an
    earlier richer plan — keeps what they have. Taking it back would be
    charging someone to lose credits."""
    user, subscription = await _subscriber(db, plan=PlanCode.BETA, plan_credits=9_000)
    pro = await db.get(Plan, PlanCode.PRO)
    assert pro is not None

    await service.apply_upgrade(db, user=user, subscription=subscription, plan=pro)

    assert user.plan_credits == 9_000

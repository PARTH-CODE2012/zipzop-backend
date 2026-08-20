"""Credits: allocation, reservation and refund.

**The one part of the system where a bug costs money in both directions.** The
tests worth reading here are not the happy paths — they are the ordering
property (plan before topup, because the alternative quietly expires the
allowance a user is entitled to), the concurrency property (two jobs against a
balance that covers one), and the two ways a refund can go wrong: twice, or
into the wrong bucket.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CreditBucket,
    CreditLedgerEntry,
    Job,
    JobFamily,
    JobStatus,
    JobTool,
    LedgerReason,
    User,
)
from app.services.credits import Allocation, CreditLedger, allocate

pytestmark = pytest.mark.anyio


# --------------------------------------------------------------------------
# allocate() — pure, and the ordering is the whole point
# --------------------------------------------------------------------------


def test_plan_credits_are_spent_before_topup() -> None:
    """docs/03-backend-architecture.md §5.4's own example.

    Drawing from `topup` first would let a monthly allowance expire unused
    every month while the credits the user *paid extra for* drain away. It
    looks like sharp practice and it is trivially avoidable.
    """
    assert allocate(50, plan_credits=20, topup_credits=500) == Allocation(plan=20, topup=30)


def test_a_job_can_span_both_buckets() -> None:
    # The contract's own example: 22 credits from 8 plan and 400 topup.
    assert allocate(22, plan_credits=8, topup_credits=400) == Allocation(plan=8, topup=14)


def test_plan_alone_is_used_when_it_covers_the_cost() -> None:
    assert allocate(10, plan_credits=300, topup_credits=0) == Allocation(plan=10, topup=0)


def test_a_short_balance_allocates_nothing() -> None:
    """None, not a partial allocation. Half-funding a job would run work that
    cannot be paid for and leave the ledger describing a debt."""
    assert allocate(22, plan_credits=0, topup_credits=5) is None


def test_the_sum_is_always_exactly_the_cost() -> None:
    for cost in range(0, 60, 7):
        for plan in (0, 3, 25, 300):
            found = allocate(cost, plan_credits=plan, topup_credits=1000)
            assert found is not None
            assert found.credits == cost


# --------------------------------------------------------------------------
# The ledger
# --------------------------------------------------------------------------


async def _user(db: AsyncSession, *, plan: int = 100, topup: int = 0) -> User:
    user = User(
        email=f"{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="x",
        plan_credits=plan,
        topup_credits=topup,
    )
    db.add(user)
    await db.flush()
    return user


async def _job(
    db: AsyncSession, user: User, *, credits: int, created_at: datetime | None = None
) -> Job:
    job = Job(
        user_id=user.id,
        tool=JobTool.COLOR_ANALYSIS,
        family=JobFamily.ANALYSIS,
        status=JobStatus.QUEUED,
        input={"assetId": "ast_x"},
        credits_reserved=credits,
    )
    if created_at is not None:
        job.created_at = created_at
    db.add(job)
    await db.flush()
    return job


async def test_reserving_writes_one_row_per_bucket_and_moves_the_balance(db: AsyncSession) -> None:
    user = await _user(db, plan=8, topup=400)
    job = await _job(db, user, credits=22)

    ledger = CreditLedger(db)
    await ledger.reserve(user=user, job_id=job.id, allocation=Allocation(plan=8, topup=14))

    assert user.plan_credits == 0
    assert user.topup_credits == 386

    rows = (
        (await db.execute(sa.select(CreditLedgerEntry).where(CreditLedgerEntry.job_id == job.id)))
        .scalars()
        .all()
    )
    assert {(r.bucket, r.delta) for r in rows} == {
        (CreditBucket.PLAN, -8),
        (CreditBucket.TOPUP, -14),
    }
    # `balance_after` is what lets the ledger be read without replaying it.
    assert {r.balance_after for r in rows} == {0, 386}


async def test_a_bucket_that_paid_nothing_writes_no_row(db: AsyncSession) -> None:
    """A zero-delta row records nothing, and the table's CHECK forbids it."""
    user = await _user(db, plan=100)
    job = await _job(db, user, credits=10)
    await CreditLedger(db).reserve(
        user=user, job_id=job.id, allocation=Allocation(plan=10, topup=0)
    )

    count = await db.scalar(
        sa.select(sa.func.count())
        .select_from(CreditLedgerEntry)
        .where(CreditLedgerEntry.job_id == job.id)
    )
    assert count == 1


async def test_a_refund_returns_to_the_buckets_it_came_from(db: AsyncSession) -> None:
    user = await _user(db, plan=8, topup=400)
    job = await _job(db, user, credits=22)
    ledger = CreditLedger(db)
    await ledger.reserve(user=user, job_id=job.id, allocation=Allocation(plan=8, topup=14))

    returned = await ledger.refund(user=user, job_id=job.id)

    assert returned == {CreditBucket.PLAN: 8, CreditBucket.TOPUP: 14}
    assert user.plan_credits == 8
    assert user.topup_credits == 400


async def test_a_refund_is_read_back_not_recomputed(db: AsyncSession) -> None:
    """The balances move between the reservation and the refund.

    Recomputing the split at refund time would allocate against the *new*
    balance and return the credits to a different bucket than the one they left
    — drift that only shows up in the nightly reconciliation, weeks later.
    """
    user = await _user(db, plan=8, topup=400)
    job = await _job(db, user, credits=22)
    ledger = CreditLedger(db)
    await ledger.reserve(user=user, job_id=job.id, allocation=Allocation(plan=8, topup=14))

    # A monthly grant lands while the job runs.
    user.plan_credits += 300

    returned = await ledger.refund(user=user, job_id=job.id)
    assert returned == {CreditBucket.PLAN: 8, CreditBucket.TOPUP: 14}


async def test_refunding_twice_returns_nothing_the_second_time(db: AsyncSession) -> None:
    """A worker that retries its completion handler must not pay twice.

    The unique index on `(job_id, reason, bucket)` is the real guard; this
    proves the code in front of it does not need the database to say no.
    """
    user = await _user(db, plan=50)
    job = await _job(db, user, credits=20)
    ledger = CreditLedger(db)
    await ledger.reserve(user=user, job_id=job.id, allocation=Allocation(plan=20, topup=0))

    first = await ledger.refund(user=user, job_id=job.id)
    second = await ledger.refund(user=user, job_id=job.id)

    assert first == {CreditBucket.PLAN: 20}
    assert second == {}
    assert user.plan_credits == 50


async def test_a_double_refund_is_impossible_even_past_the_check(db: AsyncSession) -> None:
    """The index, not the code. Writing the row directly must fail."""
    user = await _user(db, plan=50)
    job = await _job(db, user, credits=20)
    ledger = CreditLedger(db)
    await ledger.reserve(user=user, job_id=job.id, allocation=Allocation(plan=20, topup=0))
    await ledger.refund(user=user, job_id=job.id)

    db.add(
        CreditLedgerEntry(
            user_id=user.id,
            bucket=CreditBucket.PLAN,
            delta=20,
            reason=LedgerReason.REFUND,
            job_id=job.id,
            balance_after=70,
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_a_refund_after_the_period_rolled_over_goes_to_topup(db: AsyncSession) -> None:
    """§5.4's explicit edge case.

    The `plan` bucket the job drew from has been swept and re-granted since, so
    crediting it now would put the credits into a bucket that no longer holds
    what it took. `topup` never expires, so the user is made whole — and it
    cannot be farmed, because starting the job cost the same either way.
    """
    user = await _user(db, plan=300, topup=0)
    started = datetime.now(UTC) - timedelta(days=40)
    job = await _job(db, user, credits=20, created_at=started)
    ledger = CreditLedger(db)
    await ledger.reserve(user=user, job_id=job.id, allocation=Allocation(plan=20, topup=0))

    returned = await ledger.refund(
        user=user,
        job_id=job.id,
        period_started_at=datetime.now(UTC) - timedelta(days=5),
        job_created_at=started,
    )

    assert returned == {CreditBucket.TOPUP: 20}
    assert user.topup_credits == 20
    assert user.plan_credits == 280  # untouched by the refund


async def test_a_job_created_alongside_its_period_is_not_a_rollover(db: AsyncSession) -> None:
    """Two clocks, and the gap between them is not evidence of anything.

    `jobs.created_at` is the database's `now()`; a subscription's
    `current_period_start` is written by whatever granted it. They differ by the
    time it takes to run a request, and without a tolerance a refund lands in
    `topup` for no reason — the user is still made whole, so nothing is lost,
    but the ledger then records something that did not happen.
    """
    user = await _user(db, plan=300)
    created = datetime.now(UTC)
    job = await _job(db, user, credits=20, created_at=created)
    ledger = CreditLedger(db)
    await ledger.reserve(user=user, job_id=job.id, allocation=Allocation(plan=20, topup=0))

    returned = await ledger.refund(
        user=user,
        job_id=job.id,
        # The period "started" a heartbeat after the job did.
        period_started_at=created + timedelta(milliseconds=400),
        job_created_at=created,
    )

    assert returned == {CreditBucket.PLAN: 20}

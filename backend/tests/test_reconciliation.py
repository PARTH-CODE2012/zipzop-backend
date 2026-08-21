"""The nightly check on the credit ledger.

**The alarm that says a transaction boundary is wrong.** The three balances on
`users` are caches of `credit_ledger`, and every write to one is meant to happen
in the same transaction as the row explaining it. Nothing else verifies that —
so if this test file is wrong, the system has no way of knowing it has been
losing or inventing credits.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CreditBucket, CreditLedgerEntry, LedgerReason, User
from app.services.reconciliation import reconcile

pytestmark = pytest.mark.anyio


async def _user(db: AsyncSession, *, plan: int = 0, topup: int = 0, facemap: int = 0) -> User:
    user = User(
        email=f"{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="x",
        plan_credits=plan,
        topup_credits=topup,
        facemap_seconds=facemap,
    )
    db.add(user)
    await db.flush()
    return user


async def _row(db: AsyncSession, user: User, bucket: CreditBucket, delta: int) -> None:
    db.add(
        CreditLedgerEntry(
            user_id=user.id,
            bucket=bucket,
            delta=delta,
            reason=LedgerReason.ADMIN_ADJUST,
            balance_after=0,  # not what reconciliation reads
        )
    )
    await db.flush()


async def test_a_balance_that_matches_its_ledger_reports_nothing(db: AsyncSession) -> None:
    user = await _user(db, plan=300)
    await _row(db, user, CreditBucket.PLAN, 300)

    result = await reconcile(db)

    assert result.ok
    assert result.users_checked >= 1


async def test_a_balance_written_without_its_row_is_caught(db: AsyncSession) -> None:
    """The exact failure mode: someone updates `users` and forgets the ledger."""
    user = await _user(db, plan=300)
    await _row(db, user, CreditBucket.PLAN, 300)
    user.plan_credits = 500  # a grant that wrote no row
    await db.flush()

    result = await reconcile(db)

    drift = next(d for d in result.drifts if d.user_id == user.id)
    assert drift.bucket is CreditBucket.PLAN
    assert drift.cached == 500
    assert drift.from_ledger == 300
    assert drift.difference == 200


async def test_a_row_written_without_its_balance_is_caught(db: AsyncSession) -> None:
    """And the other direction, which loses the user credits rather than
    inventing them — the one that generates support tickets."""
    user = await _user(db, plan=0)
    await _row(db, user, CreditBucket.PLAN, 250)

    result = await reconcile(db)

    drift = next(d for d in result.drifts if d.user_id == user.id)
    assert drift.difference == -250


async def test_a_bucket_with_no_rows_must_hold_nothing(db: AsyncSession) -> None:
    """Absence of evidence is not agreement.

    Defaulting an empty bucket to whatever the cache says would make exactly
    this case — a balance nobody ever wrote a row for — invisible.
    """
    user = await _user(db, topup=99)

    result = await reconcile(db)

    drift = next(
        d for d in result.drifts if d.user_id == user.id and d.bucket is CreditBucket.TOPUP
    )
    assert drift.from_ledger == 0
    assert drift.cached == 99


async def test_the_buckets_are_checked_separately(db: AsyncSession) -> None:
    """A user can be right about one bucket and wrong about another, and
    summing them would let two mistakes cancel out."""
    user = await _user(db, plan=100, topup=100)
    await _row(db, user, CreditBucket.PLAN, 150)
    await _row(db, user, CreditBucket.TOPUP, 50)

    result = await reconcile(db)

    mine = {d.bucket: d.difference for d in result.drifts if d.user_id == user.id}
    assert mine == {CreditBucket.PLAN: -50, CreditBucket.TOPUP: 50}


async def test_a_real_job_leaves_no_drift(client: object, db: AsyncSession) -> None:
    """The property that matters: the code paths that move credits keep the
    ledger and the balances in step, all by themselves."""
    from app.models import Job, JobFamily, JobStatus, JobTool
    from app.services.credits import Allocation, CreditLedger

    user = await _user(db, plan=300)
    await _row(db, user, CreditBucket.PLAN, 300)

    job = Job(
        user_id=user.id,
        tool=JobTool.COLOR_ANALYSIS,
        family=JobFamily.ANALYSIS,
        status=JobStatus.QUEUED,
        input={},
        credits_reserved=40,
    )
    db.add(job)
    await db.flush()

    ledger = CreditLedger(db)
    await ledger.reserve(user=user, job_id=job.id, allocation=Allocation(plan=40, topup=0))
    assert not (await reconcile(db)).drifts

    await ledger.refund(user=user, job_id=job.id)
    assert not (await reconcile(db)).drifts

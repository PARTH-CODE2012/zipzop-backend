"""Credits: who may spend, from which bucket, and how it comes back.

**This is the module where money moves.** Two properties hold throughout
(docs/03-backend-architecture.md §5.4), and everything here exists to keep them:

* *a user cannot start work they cannot pay for* — the reservation is written in
  the same transaction that queues the job, so there is no window in which one
  exists without the other;
* *a failure on our side never costs the user anything* — a refund is automatic,
  goes back to the buckets it came from, and cannot be applied twice.

The second property is enforced by the database, not by care: `credit_ledger`
carries a unique index on `(job_id, reason, bucket)`, so a worker that retries
its completion handler writes a duplicate refund exactly once and gets an
integrity error the second time. Code that relies on remembering to check is
code that will one day forget.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CreditBucket, CreditLedgerEntry, LedgerReason, User


@dataclass(frozen=True, slots=True)
class Allocation:
    """Which buckets a cost is drawn from. Never negative, always sums to the cost."""

    plan: int
    topup: int
    #: Face mapping and lip sync only, and neither ships in phase 1. Carried so
    #: `reservedFrom` in the contract's response has the field it promises
    #: rather than the client learning it exists in phase 2.
    facemap_seconds: int = 0

    @property
    def credits(self) -> int:
        return self.plan + self.topup

    def as_dict(self) -> dict[str, int]:
        return {"plan": self.plan, "topup": self.topup, "facemapSeconds": self.facemap_seconds}


def allocate(cost: int, *, plan_credits: int, topup_credits: int) -> Allocation | None:
    """Draw from the bucket that expires soonest. `None` when the balance is short.

    **Plan credits first, and it is not a detail.** Drawing from `topup` first
    would mean a user's monthly allowance quietly expires unused every month
    while the credits they paid extra for drain away — it looks like sharp
    practice, it generates refund requests, and it is trivially avoidable.

    Returns `None` rather than raising, because `POST /jobs/estimate` asks the
    same question and must answer it without an exception: the estimate exists
    precisely so the client can grey out a button instead of catching a 402.
    """
    if cost <= 0:
        return Allocation(plan=0, topup=0)
    take_plan = min(max(0, plan_credits), cost)
    take_topup = cost - take_plan
    if take_topup > max(0, topup_credits):
        return None
    return Allocation(plan=take_plan, topup=take_topup)


#: How far a job may predate the current period and still be treated as part of
#: it.
#:
#: **Because two clocks are involved.** `jobs.created_at` is the database's
#: `now()` — the timestamp of the transaction that inserted it — while a
#: subscription's `current_period_start` is written by whatever granted it. They
#: agree to within the time it takes to run a request, and a job created in the
#: same second as a renewal is not evidence that the period turned over
#: underneath it. Without the tolerance a refund lands in `topup` for no reason:
#: the user is still made whole, so it is not a loss, but the ledger then says
#: something happened that did not.
ROLLOVER_TOLERANCE = timedelta(seconds=5)


def _period_rolled_over(
    job_created_at: datetime | None, period_started_at: datetime | None
) -> bool:
    """Was the `plan` bucket swept and re-granted while this job was alive?"""
    if job_created_at is None or period_started_at is None:
        return False
    return job_created_at < period_started_at - ROLLOVER_TOLERANCE


#: Which cached column on `users` each bucket keeps its balance in.
_BALANCE_COLUMN: Final[dict[CreditBucket, str]] = {
    CreditBucket.PLAN: "plan_credits",
    CreditBucket.TOPUP: "topup_credits",
    CreditBucket.FACEMAP: "facemap_seconds",
}


class CreditLedger:
    """Every write to a balance, with its ledger row, in one place.

    The three balances on `users` are **caches** of this table. They are only
    ever written here, beside the row that explains them, which is what makes
    the nightly reconciliation's alarm meaningful: if the sum of the ledger and
    the cached balance disagree, a transaction boundary is wrong, and there is
    exactly one file to look in.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_user(self, user_id: uuid.UUID) -> User | None:
        """`SELECT … FOR UPDATE` — step 3 of job creation.

        Two requests arriving together against a balance that only covers one
        must not both read it, both decide they can afford it, and both write.
        The row lock is what serialises them; without it the check and the
        deduction are two statements with a gap in between, and the gap is
        where a user spends credits they do not have.
        """
        result = await self._session.execute(
            sa.select(User).where(User.id == user_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def _write(
        self,
        *,
        user: User,
        bucket: CreditBucket,
        delta: int,
        reason: LedgerReason,
        job_id: uuid.UUID | None,
        note: str | None = None,
    ) -> None:
        """One ledger row and the cached balance it explains, together."""
        if delta == 0:
            # The table's own CHECK forbids it, and a zero row records nothing
            # anyway. Silently skipping keeps callers free of the special case.
            return

        column = _BALANCE_COLUMN[bucket]
        balance_after = getattr(user, column) + delta
        setattr(user, column, balance_after)

        self._session.add(
            CreditLedgerEntry(
                user_id=user.id,
                bucket=bucket,
                delta=delta,
                reason=reason,
                job_id=job_id,
                balance_after=balance_after,
                note=note,
            )
        )

    async def reserve(self, *, user: User, job_id: uuid.UUID, allocation: Allocation) -> None:
        """Take the credits. One row per bucket drawn — a job spanning two
        buckets writes two, which is why `bucket` is part of the ledger's key."""
        await self._write(
            user=user,
            bucket=CreditBucket.PLAN,
            delta=-allocation.plan,
            reason=LedgerReason.RESERVE,
            job_id=job_id,
        )
        await self._write(
            user=user,
            bucket=CreditBucket.TOPUP,
            delta=-allocation.topup,
            reason=LedgerReason.RESERVE,
            job_id=job_id,
        )
        await self._write(
            user=user,
            bucket=CreditBucket.FACEMAP,
            delta=-allocation.facemap_seconds,
            reason=LedgerReason.RESERVE,
            job_id=job_id,
        )
        await self._session.flush()

    async def reserved_for(self, job_id: uuid.UUID) -> dict[CreditBucket, int]:
        """What a job actually took, read back from its own rows.

        **Read back, never recomputed.** Recomputing the allocation at refund
        time would run `allocate()` against balances that have moved since, and
        could return the credits to a different bucket than the one they left —
        which is a bug that only shows up as drift in the nightly
        reconciliation, weeks later.
        """
        rows = await self._session.execute(
            sa.select(CreditLedgerEntry.bucket, CreditLedgerEntry.delta).where(
                CreditLedgerEntry.job_id == job_id,
                CreditLedgerEntry.reason == LedgerReason.RESERVE,
            )
        )
        return {bucket: -delta for bucket, delta in rows.all()}

    async def reserved_for_many(
        self, job_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, dict[CreditBucket, int]]:
        """The same, for a page of jobs — one query rather than one per row."""
        if not job_ids:
            return {}
        rows = await self._session.execute(
            sa.select(
                CreditLedgerEntry.job_id, CreditLedgerEntry.bucket, CreditLedgerEntry.delta
            ).where(
                CreditLedgerEntry.job_id.in_(job_ids),
                CreditLedgerEntry.reason == LedgerReason.RESERVE,
            )
        )
        found: dict[uuid.UUID, dict[CreditBucket, int]] = {}
        for job_id, bucket, delta in rows.all():
            found.setdefault(job_id, {})[bucket] = -delta
        return found

    async def already_refunded(self, job_id: uuid.UUID) -> bool:
        found = await self._session.scalar(
            sa.select(sa.literal(1)).where(
                sa.exists().where(
                    CreditLedgerEntry.job_id == job_id,
                    CreditLedgerEntry.reason == LedgerReason.REFUND,
                )
            )
        )
        return found is not None

    async def refund(
        self,
        *,
        user: User,
        job_id: uuid.UUID,
        period_started_at: datetime | None = None,
        job_created_at: datetime | None = None,
    ) -> dict[CreditBucket, int]:
        """Give it back, to the buckets it came from. Idempotent.

        **The period-rollover case is handled explicitly.** If the billing
        period turned over while the job was running, the `plan` bucket it drew
        from has already been swept and re-granted — crediting it now would put
        credits into a bucket that no longer contains what it took. So that part
        of the refund goes to `topup`, which never expires. The user is made
        whole, and it cannot be farmed: starting the job cost the same either
        way.

        Returns what was returned, per bucket, or an empty mapping when there
        was nothing to return.
        """
        if await self.already_refunded(job_id):
            return {}

        reserved = await self.reserved_for(job_id)
        if not reserved:
            return {}

        rolled_over = _period_rolled_over(job_created_at, period_started_at)

        returned: dict[CreditBucket, int] = {}
        for bucket, amount in reserved.items():
            if amount <= 0:
                continue
            target = bucket
            note = None
            if rolled_over and bucket is CreditBucket.PLAN:
                target = CreditBucket.TOPUP
                note = "plan bucket swept while the job ran; refunded to topup"
            await self._write(
                user=user,
                bucket=target,
                delta=amount,
                reason=LedgerReason.REFUND,
                job_id=job_id,
                note=note,
            )
            returned[target] = returned.get(target, 0) + amount

        await self._session.flush()
        return returned

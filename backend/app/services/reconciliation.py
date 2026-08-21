"""Does the ledger still agree with the balances it explains?

The three balances on `users` are **caches** of `credit_ledger`. Every write to
one happens in the same transaction as the row that explains it — that is the
invariant the whole credit system rests on, and this is the only thing that
checks it (docs/03-backend-architecture.md §5.4: *"a nightly job re-sums the
ledger per user per bucket and alerts on any drift — if that alarm fires, there
is a bug in a transaction boundary"*).

**It reports; it does not repair.** Silently correcting a balance would hide the
bug that caused the drift and destroy the evidence of how much was lost and to
whom. Drift is a page for a person, not a self-healing behaviour: the ledger is
the record of account, and if it disagrees with the cache the right response is
to find out why before touching either.
"""

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import CreditBucket, CreditLedgerEntry, User

log = get_logger(__name__)

#: Which cached column each bucket is meant to match.
BALANCE_COLUMN = {
    CreditBucket.PLAN: User.plan_credits,
    CreditBucket.TOPUP: User.topup_credits,
    CreditBucket.FACEMAP: User.facemap_seconds,
}


@dataclass(frozen=True, slots=True)
class Drift:
    user_id: uuid.UUID
    bucket: CreditBucket
    cached: int
    from_ledger: int

    @property
    def difference(self) -> int:
        return self.cached - self.from_ledger


@dataclass(frozen=True, slots=True)
class Reconciliation:
    users_checked: int
    drifts: list[Drift]

    @property
    def ok(self) -> bool:
        return not self.drifts


async def reconcile(session: AsyncSession) -> Reconciliation:
    """Re-sum the ledger for every user and compare, in **one** query per bucket.

    Per user would be three queries times the whole user table, which on a
    nightly job over a real account list is an hour of round trips to answer a
    question that is one `GROUP BY`.
    """
    totals: dict[tuple[uuid.UUID, CreditBucket], int] = {}
    rows = await session.execute(
        sa.select(
            CreditLedgerEntry.user_id,
            CreditLedgerEntry.bucket,
            sa.func.coalesce(sa.func.sum(CreditLedgerEntry.delta), 0),
        ).group_by(CreditLedgerEntry.user_id, CreditLedgerEntry.bucket)
    )
    for user_id, bucket, total in rows.all():
        totals[(user_id, bucket)] = int(total or 0)

    drifts: list[Drift] = []
    checked = 0

    users = await session.execute(
        sa.select(User.id, User.plan_credits, User.topup_credits, User.facemap_seconds)
    )
    for user_id, plan, topup, facemap in users.all():
        checked += 1
        cached = {
            CreditBucket.PLAN: plan,
            CreditBucket.TOPUP: topup,
            CreditBucket.FACEMAP: facemap,
        }
        for bucket, value in cached.items():
            # A user with no rows in a bucket should have a zero balance there.
            # Defaulting to the cached value would make the absence of evidence
            # look like agreement, which is exactly the bug this hunts.
            from_ledger = totals.get((user_id, bucket), 0)
            if value != from_ledger:
                drifts.append(
                    Drift(user_id=user_id, bucket=bucket, cached=value, from_ledger=from_ledger)
                )

    return Reconciliation(users_checked=checked, drifts=drifts)

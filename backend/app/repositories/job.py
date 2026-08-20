"""Jobs — the row every unit of server work lives on.

Two halves, deliberately separate.

`JobRepository` is what the **API** uses: scoped to one user by construction,
like every other repository here (`base.py` explains why that is structural
rather than a habit).

The module-level functions below are what a **worker** uses. A worker is not a
request: it has a job id and no user, it opens its own session, and the row it
is about to touch may belong to anyone. Giving it a scoped repository would mean
inventing a user context it does not have, so the claim and the finish are
plain functions that take the id they were handed — and every one of them is
written so that running it twice cannot corrupt anything.
"""

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobFamily, JobStatus, JobTool
from app.repositories.base import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ScopedRepository,
    decode_cursor,
    encode_cursor,
)

#: A worker claims a job only if the user is under their plan's cap for that
#: family. The check is a correlated subquery inside the claiming UPDATE, so it
#: sees the same snapshot as the claim — but two workers claiming two *different*
#: jobs for the same user at the same instant can still both pass it. That is a
#: deliberate soft limit: the cap exists so one account cannot monopolise a pool
#: (docs/03-backend-architecture.md §5.3), and being one over it for a few
#: seconds costs nothing. A hard limit would need a lock per user per family on
#: the hot path of every worker, which is a real cost for an imaginary problem.
LIVE_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)


class JobRepository(ScopedRepository[Job]):
    model = Job

    async def create(
        self,
        *,
        tool: JobTool,
        family: JobFamily,
        priority: int,
        job_input: dict[str, Any],
        credits_reserved: int,
        project_id: uuid.UUID | None,
        idempotency_key: str | None,
    ) -> Job:
        job = Job(
            user_id=self.user_id,
            project_id=project_id,
            tool=tool,
            family=family,
            status=JobStatus.QUEUED,
            priority=priority,
            input=job_input,
            credits_reserved=credits_reserved,
            idempotency_key=idempotency_key,
        )
        self._session.add(job)
        # Flushed, not committed: the caller is inside the one transaction that
        # also writes the reservation, and the job needs an id for the ledger
        # rows to point at. A job can never exist without its reservation.
        await self._session.flush()
        return job

    async def by_idempotency_key(self, key: str) -> Job | None:
        """Replay returns the original job rather than charging twice."""
        result = await self._session.execute(self._select().where(Job.idempotency_key == key))
        return result.scalar_one_or_none()

    async def page(
        self,
        *,
        project_id: uuid.UUID | None = None,
        statuses: tuple[JobStatus, ...] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> tuple[list[Job], str | None]:
        """Newest first. `status=running` is what a client calls on reconnect to
        catch up on anything it missed while the socket was down (contract §6)."""
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        query = self._select()
        if project_id is not None:
            query = query.where(Job.project_id == project_id)
        if statuses:
            query = query.where(Job.status.in_(statuses))
        if cursor:
            moment, row_id = decode_cursor(cursor)
            query = query.where(
                sa.tuple_(Job.created_at, Job.id)
                < sa.tuple_(sa.literal(moment), sa.literal(row_id))
            )
        query = query.order_by(Job.created_at.desc(), Job.id.desc()).limit(limit + 1)
        rows = list((await self._session.execute(query)).scalars().all())

        if len(rows) > limit:
            rows = rows[:limit]
            last = rows[-1]
            return rows, encode_cursor(last.created_at, last.id)
        return rows, None

    async def live_count(self, family: JobFamily) -> int:
        """Queued *and* running. Used only to report how full a user's lane is —
        the cap itself is applied when a worker claims (see `claim` below)."""
        count = await self._session.scalar(
            sa.select(sa.func.count())
            .select_from(Job)
            .where(
                Job.user_id == self.user_id,
                Job.family == family,
                Job.status.in_(LIVE_STATUSES),
            )
        )
        return int(count or 0)

    async def cancel(self, job: Job) -> bool:
        """Cancel if it has not finished. Returns False when it is too late.

        Compare-and-set on the status, for the same reason the timeline save is:
        a worker may claim the job between the read that decided it was
        cancellable and the write that cancels it, and a `cancelled` row whose
        worker is still running is a job that refunds *and* completes.
        """
        result = await self._session.execute(
            sa.update(Job)
            .where(
                Job.id == job.id,
                Job.user_id == self.user_id,
                Job.status.in_(LIVE_STATUSES),
            )
            .values(status=JobStatus.CANCELLED, finished_at=sa.func.now())
            .returning(Job.id)
        )
        cancelled = result.one_or_none() is not None
        if cancelled:
            await self._session.refresh(job)
        return cancelled


# --------------------------------------------------------------------------
# Worker side. No user context, and every function is safe to run twice.
# --------------------------------------------------------------------------


async def claim(
    session: AsyncSession, job_id: uuid.UUID, *, concurrency_limit: int, worker_id: str
) -> Job | None:
    """Take the job, or return None because somebody else already has.

    `WHERE status='queued'` is the whole mechanism: exactly one worker's UPDATE
    can match, and the losers see zero rows and stop. Nothing is locked, nothing
    is polled, and a redelivered message cannot start a second run of work that
    is already under way.

    A user at their plan's concurrency cap matches nothing either — the job
    stays `queued` and the caller retries it later, which is what the contract
    promises: *"beyond the limit, jobs stay queued and start as slots free up"*,
    never an error the client has to handle.
    """
    # An alias of the same table, correlated to the row being updated: "how
    # many jobs is this user already running in this family". Written as an
    # alias rather than a second query so it is evaluated in the same statement
    # as the claim, against the same snapshot.
    peer = sa.alias(Job.__table__, "peer")
    others = (
        sa.select(sa.func.count())
        .select_from(peer)
        .where(
            peer.c.user_id == Job.user_id,
            peer.c.family == Job.family,
            peer.c.status == JobStatus.RUNNING,
        )
        .scalar_subquery()
    )

    result = await session.execute(
        sa.update(Job)
        .where(
            Job.id == job_id,
            Job.status == JobStatus.QUEUED,
            others < concurrency_limit,
        )
        .values(
            status=JobStatus.RUNNING,
            started_at=sa.func.now(),
            attempts=Job.attempts + 1,
            worker_id=worker_id,
        )
        .returning(Job.id)
    )
    if result.one_or_none() is None:
        return None
    return await session.get(Job, job_id)


async def set_progress(session: AsyncSession, job_id: uuid.UUID, progress: int) -> None:
    """Progress only ever moves forward, and only while running.

    A late checkpoint from a retried attempt must not drag the bar backwards,
    and a finished job must not come back to life as `running` because a message
    arrived out of order.
    """
    await session.execute(
        sa.update(Job)
        .where(
            Job.id == job_id,
            Job.status == JobStatus.RUNNING,
            Job.progress < progress,
        )
        .values(progress=max(0, min(100, progress)))
    )


async def succeed(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    result: dict[str, Any] | None = None,
    result_key: str | None = None,
    output_asset_id: uuid.UUID | None = None,
    model_version: str | None = None,
) -> bool:
    """Finish. The reservation *is* the charge, so nothing moves in the ledger.

    `WHERE status='running'` again: a job cancelled while it ran must stay
    cancelled — its credits have already gone back, and marking it succeeded
    now would hand the user the result for free and leave the ledger disagreeing
    with the row.
    """
    outcome = await session.execute(
        sa.update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.RUNNING)
        .values(
            status=JobStatus.SUCCEEDED,
            progress=100,
            result=result,
            result_key=result_key,
            output_asset_id=output_asset_id,
            model_version=model_version,
            credits_settled=Job.credits_reserved,
            finished_at=sa.func.now(),
        )
        .returning(Job.id)
    )
    return outcome.one_or_none() is not None


async def fail(
    session: AsyncSession, job_id: uuid.UUID, *, error_code: str, error_message: str
) -> bool:
    """Permanent failure. The refund is the caller's next call, not this one's —
    it needs the user row locked, and locking it here would put a write lock
    inside every progress update's transaction."""
    outcome = await session.execute(
        sa.update(Job)
        .where(Job.id == job_id, Job.status.in_(LIVE_STATUSES))
        .values(
            status=JobStatus.FAILED,
            error_code=error_code,
            error_message=error_message[:2000],
            finished_at=sa.func.now(),
        )
        .returning(Job.id)
    )
    return outcome.one_or_none() is not None


async def requeue(session: AsyncSession, job_id: uuid.UUID) -> None:
    """A transient failure goes back in the queue rather than to `failed`.

    The credits stay reserved: the work is still going to happen, and refunding
    now to re-reserve on the retry would write four ledger rows for one job and
    could fail the second reservation against a balance that moved in between.
    """
    await session.execute(
        sa.update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.RUNNING)
        .values(status=JobStatus.QUEUED, started_at=None, progress=0, worker_id=None)
    )


async def cancellation_requested(session: AsyncSession, job_id: uuid.UUID) -> bool:
    """What a worker checks between stages, so a long job stops promptly."""
    status = await session.scalar(sa.select(Job.status).where(Job.id == job_id))
    return status == JobStatus.CANCELLED


async def period_start_for(session: AsyncSession, user_id: uuid.UUID) -> datetime | None:
    """When the live subscription's current period began — the one fact a refund
    needs to know whether the `plan` bucket it is about to credit is the same
    one the job drew from."""
    from app.models import Subscription, SubStatus

    started: datetime | None = await session.scalar(
        sa.select(Subscription.current_period_start).where(
            Subscription.user_id == user_id,
            Subscription.status.in_([SubStatus.ACTIVE, SubStatus.PAST_DUE]),
        )
    )
    return started

"""Jobs — contract §6. One endpoint creates every kind of server work.

The whole milestone turns on what `POST /jobs` does in a single transaction
(docs/03-backend-architecture.md §5.2): resolve the asset, price the work, lock
the user row, reserve the credits, insert the job. **A job can never exist
without its reservation, and a reservation can never exist without its job** —
which is why none of those steps is allowed to be a separate request, and why
the enqueue is the only thing that happens after the commit.

That last part is deliberate and was not in the original sequence. Enqueueing
inside the transaction hands a worker an id that is not yet visible to anyone
else's connection: the task starts, reads nothing, and fails a job the user was
never told about. The Celery send goes after the response body is built, in
`_enqueue`, and the task retries a job it cannot see yet in case this ever runs
somewhere the ordering is less obvious.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, status

from app.api import ids
from app.api.deps import CurrentUser, Session, general_rate_limit
from app.api.errors import (
    APIError,
    FairUseExceededError,
    InsufficientCreditsError,
    JobNotCancellableError,
    NotFoundError,
    PlanLimitExceededError,
    UnsupportedMediaError,
)
from app.api.schemas.common import Page
from app.api.schemas.job import (
    CreateJobRequest,
    EstimateResponse,
    JobError,
    JobResponse,
    ReservedFrom,
)
from app.logging import get_logger
from app.models import CreditBucket, Job, JobStatus
from app.repositories.job import JobRepository
from app.services import pricing, storage
from app.services.credits import CreditLedger
from app.services.jobs import QuoteRejectionError, quote

log = get_logger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(general_rate_limit)])

#: Server-written key on `jobs.input`. The client's own fields are left exactly
#: as they were sent; this is the one thing the server adds, and it is prefixed
#: so nobody mistakes it for something the caller can set.
ANALYSED_MS = "analyzedDurationMs"

_REJECTION_ERRORS: dict[str, type[APIError]] = {
    "NOT_FOUND": NotFoundError,
    "UNSUPPORTED_MEDIA": UnsupportedMediaError,
    "INSUFFICIENT_CREDITS": InsufficientCreditsError,
    "FAIR_USE_EXCEEDED": FairUseExceededError,
    "PLAN_LIMIT_EXCEEDED": PlanLimitExceededError,
}


def _raise(rejection: QuoteRejectionError) -> None:
    error = _REJECTION_ERRORS.get(rejection.code, UnsupportedMediaError)
    raise error(rejection.message, details=rejection.details)


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------


def _serialise(job: Job, reserved: dict[CreditBucket, int]) -> JobResponse:
    """One job, with where its credits came from and where its result is.

    `reservedFrom` is read from the ledger rather than stored on the row. The
    ledger is what the nightly reconciliation checks and what a refund reads
    back, so a second copy on the job could only ever be the one that disagrees.
    """
    duration_ms = int(job.input.get(ANALYSED_MS) or 0)
    error = (
        JobError(code=job.error_code, message=job.error_message or "") if job.error_code else None
    )
    return JobResponse(
        id=ids.encode(ids.JOB, job.id),
        tool=job.tool,
        family=job.family,
        status=job.status,
        progress=job.progress,
        priority=job.priority,
        credits_reserved=job.credits_reserved,
        reserved_from=ReservedFrom(
            plan=reserved.get(CreditBucket.PLAN, 0),
            topup=reserved.get(CreditBucket.TOPUP, 0),
            facemap_seconds=reserved.get(CreditBucket.FACEMAP, 0),
        ),
        estimated_seconds=pricing.estimated_seconds(job.tool, duration_ms),
        project_id=ids.encode(ids.PROJECT, job.project_id) if job.project_id else None,
        clip_id=job.input.get("clipId"),
        result=job.result,
        # A result over 256 KB lives in S3 and the link is minted per request,
        # like every other signed URL here — one written into the row would be
        # dead an hour later (contract §6.3).
        result_url=storage.presign_get(job.result_key) if job.result_key else None,
        output_asset_id=(
            ids.encode(ids.ASSET, job.output_asset_id) if job.output_asset_id else None
        ),
        error=error,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


async def _with_reservations(session: Session, jobs: list[Job]) -> list[JobResponse]:
    """One query for a page of jobs, not one per job."""
    if not jobs:
        return []
    ledger = CreditLedger(session)
    reserved = await ledger.reserved_for_many({j.id for j in jobs})
    return [_serialise(job, reserved.get(job.id, {})) for job in jobs]


def _enqueue(job: Job) -> None:
    """Hand the job to a worker. **After** the transaction, never inside it."""
    from app.workers.tasks.analysis import run_analysis

    run_analysis.apply_async(args=[str(job.id)], priority=job.priority)
    log.info("job_enqueued", job_id=str(job.id), tool=job.tool.value, priority=job.priority)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.post(
    "/estimate",
    response_model=EstimateResponse,
    summary="What a job would cost, without creating it",
)
async def estimate_job(
    body: CreateJobRequest, user: CurrentUser, session: Session
) -> EstimateResponse:
    """Contract §6.1. Same body as `POST /jobs`, nothing created.

    A block is **reported, not raised**: the point of this endpoint is to put
    "Not enough credits" on the button instead of after a failed click, so the
    only failures that raise here are the ones that mean the request itself is
    wrong — an asset that does not exist, or one that is not ready.
    """
    try:
        assessment = await quote(
            session,
            user_id=user.id,
            plan_credits=user.plan_credits,
            topup_credits=user.topup_credits,
            tool=body.tool,
            job_input=body.input,
        )
    except QuoteRejectionError as rejection:
        _raise(rejection)
        raise  # unreachable; keeps the type checker honest

    allocation = assessment.allocation
    return EstimateResponse(
        credits=assessment.credits,
        would_reserve_from=ReservedFrom(
            plan=allocation.plan if allocation else 0,
            topup=allocation.topup if allocation else 0,
            facemap_seconds=allocation.facemap_seconds if allocation else 0,
        ),
        estimated_seconds=assessment.estimated_seconds,
        sufficient_balance=allocation is not None,
        blocked_by=assessment.rejection.code if assessment.rejection else None,
    )


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobResponse,
    summary="Start a job",
)
async def create_job(
    body: CreateJobRequest,
    user: CurrentUser,
    session: Session,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobResponse:
    jobs = JobRepository(session, user.id)

    # Replay first, and before anything is priced: a retry after a network
    # timeout is indistinguishable from a second request, and the difference
    # between the two is whether the user is charged twice (contract §1).
    if idempotency_key:
        existing = await jobs.by_idempotency_key(idempotency_key)
        if existing is not None:
            ledger = CreditLedger(session)
            return _serialise(existing, await ledger.reserved_for(existing.id))

    try:
        assessment = await quote(
            session,
            user_id=user.id,
            plan_credits=user.plan_credits,
            topup_credits=user.topup_credits,
            tool=body.tool,
            job_input=body.input,
        )
    except QuoteRejectionError as rejection:
        _raise(rejection)
        raise

    if assessment.rejection is not None:
        _raise(assessment.rejection)

    ledger = CreditLedger(session)
    # The lock, and everything after it, is the whole of §5.2's steps 3-5. Two
    # requests against a balance that covers one of them serialise here; without
    # it they both read the same balance and both spend it.
    locked = await ledger.lock_user(user.id)
    if locked is None:
        raise NotFoundError("That account no longer exists.")

    # Re-price against the locked row. The balance read for the quote came from
    # the request's own user object, which was loaded before the lock — between
    # the two, another request may have spent it, and the quote's answer is
    # then a decision made on a number that is no longer true.
    from app.services.credits import allocate as allocate_credits

    allocation = allocate_credits(
        assessment.credits,
        plan_credits=locked.plan_credits,
        topup_credits=locked.topup_credits,
    )
    if allocation is None:
        raise InsufficientCreditsError(
            "This job needs more credits than the account holds.",
            details={
                "required": assessment.credits,
                "available": locked.plan_credits + locked.topup_credits,
            },
        )

    job_input: dict[str, Any] = body.input.model_dump(mode="json", by_alias=True, exclude_none=True)
    job_input[ANALYSED_MS] = assessment.duration_ms

    project_uuid: uuid.UUID | None = None
    if body.project_id:
        project_uuid = ids.decode(ids.PROJECT, body.project_id)

    job = await jobs.create(
        tool=assessment.tool,
        family=assessment.family,
        priority=assessment.priority,
        job_input=job_input,
        credits_reserved=assessment.credits,
        project_id=project_uuid,
        idempotency_key=idempotency_key,
    )
    await ledger.reserve(user=locked, job_id=job.id, allocation=allocation)

    response = _serialise(
        job,
        {
            CreditBucket.PLAN: allocation.plan,
            CreditBucket.TOPUP: allocation.topup,
            CreditBucket.FACEMAP: allocation.facemap_seconds,
        },
    )

    # Committed here rather than by the dependency, so the row is visible to
    # every connection before a worker is told it exists.
    await session.commit()
    _enqueue(job)

    log.info(
        "job_created",
        job_id=str(job.id),
        tool=job.tool.value,
        credits=job.credits_reserved,
        plan=allocation.plan,
        topup=allocation.topup,
    )
    return response


@router.get("", response_model=Page[JobResponse], summary="The caller's jobs")
async def list_jobs(
    user: CurrentUser,
    session: Session,
    project_id: Annotated[str | None, Query(alias="projectId")] = None,
    job_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[JobResponse]:
    """*"On reconnect the client calls this to catch up on anything it missed
    while disconnected."* — which is why `status=running` is the query that
    matters, and why the socket is never the source of truth."""
    statuses: tuple[JobStatus, ...] | None = None
    if job_status:
        try:
            statuses = tuple(JobStatus(s) for s in job_status.split(","))
        except ValueError:
            raise UnsupportedMediaError(f"There is no job status called {job_status!r}.") from None

    rows, next_cursor = await JobRepository(session, user.id).page(
        project_id=ids.decode(ids.PROJECT, project_id) if project_id else None,
        statuses=statuses,
        limit=limit,
        cursor=cursor,
    )
    return Page[JobResponse](items=await _with_reservations(session, rows), next_cursor=next_cursor)


@router.get("/{job_id}", response_model=JobResponse, summary="One job")
async def get_job(job_id: str, user: CurrentUser, session: Session) -> JobResponse:
    job = await JobRepository(session, user.id).get(ids.decode(ids.JOB, job_id))
    if job is None:
        raise NotFoundError("We have no job with that id.")
    return _serialise(job, await CreditLedger(session).reserved_for(job.id))


@router.post("/{job_id}/cancel", response_model=JobResponse, summary="Cancel a job")
async def cancel_job(job_id: str, user: CurrentUser, session: Session) -> JobResponse:
    """`409` if it already finished. Cancelling refunds the reservation in full.

    The refund happens here rather than in the worker: a `queued` job has no
    worker to notice, and one that is running checks the status between stages
    and stops without touching credits that have already gone back.
    """
    jobs = JobRepository(session, user.id)
    job = await jobs.get(ids.decode(ids.JOB, job_id))
    if job is None:
        raise NotFoundError("We have no job with that id.")

    if not await jobs.cancel(job):
        raise JobNotCancellableError(
            "That job has already finished.", details={"status": job.status.value}
        )

    ledger = CreditLedger(session)
    locked = await ledger.lock_user(user.id)
    if locked is not None:
        from app.repositories.job import period_start_for

        await ledger.refund(
            user=locked,
            job_id=job.id,
            period_started_at=await period_start_for(session, user.id),
            job_created_at=job.created_at,
        )
    log.info("job_cancelled", job_id=str(job.id))
    return _serialise(job, await ledger.reserved_for(job.id))

"""Measured cost per job — docs/20-m6-readiness.md §7.

🟠 The report that says whether the `beta` plan loses money. Every tier's
allowance derives from `SECONDS_PER_MINUTE_OF_MEDIA`, which is a heuristic; this
measures what the fleet actually did.

Three properties worth defending, and none of them is the arithmetic:

* the **median**, not the mean — one job that paid for a cold model download
  would drag a mean past every other job in the sample combined;
* **too few samples is not a measurement**, and must not be reported as one;
* jobs that ran before the column existed are **not backfilled with a guess**,
  because a guess is indistinguishable from a measurement in this report.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobFamily, JobStatus, JobTool, User
from app.services.job_costs import MIN_SAMPLES, measure, worst_case_seconds

pytestmark = pytest.mark.anyio


async def _user(db: AsyncSession) -> User:
    user = User(email=f"{uuid.uuid4().hex[:12]}@example.com", hashed_password="x")
    db.add(user)
    await db.flush()
    return user


async def _job(
    db: AsyncSession,
    user: User,
    *,
    tool: JobTool = JobTool.CAPTIONS,
    media_minutes: float = 1.0,
    wall_seconds: float = 20.0,
    status: JobStatus = JobStatus.SUCCEEDED,
    #: `False` means "leave the column NULL", which is what a job from before
    #: M6 looks like. `None` would collide with the computed default.
    media_duration_ms: int | bool | None = False,
    credits: int = 2,
) -> Job:
    started = datetime.now(UTC) - timedelta(minutes=5)
    job = Job(
        user_id=user.id,
        tool=tool,
        family=JobFamily.ANALYSIS,
        status=status,
        input={},
        credits_reserved=credits,
        media_duration_ms=(
            int(media_minutes * 60_000)
            if media_duration_ms is False
            else (media_duration_ms or None)
        ),
        started_at=started,
        finished_at=started + timedelta(seconds=wall_seconds),
    )
    db.add(job)
    await db.flush()
    return job


async def test_the_measured_ratio_is_seconds_per_minute_of_media(db: AsyncSession) -> None:
    user = await _user(db)
    for _ in range(MIN_SAMPLES):
        await _job(db, user, media_minutes=2.0, wall_seconds=40.0)

    [captions] = [cost for cost in await measure(db) if cost.tool is JobTool.CAPTIONS]

    assert captions.samples == MIN_SAMPLES
    assert captions.measured_seconds_per_minute == pytest.approx(20.0, abs=0.01)


async def test_one_slow_job_does_not_move_the_number(db: AsyncSession) -> None:
    """⚠️ The median, not the mean, and this is why.

    A first job that paid for a cold model download takes minutes rather than
    seconds. Averaged in, it would rewrite the tool's whole cost profile from a
    one-off that never happens again on a warm worker.
    """
    user = await _user(db)
    for _ in range(MIN_SAMPLES):
        await _job(db, user, media_minutes=1.0, wall_seconds=20.0)
    await _job(db, user, media_minutes=1.0, wall_seconds=1_200.0)  # a cold start

    [captions] = [cost for cost in await measure(db) if cost.tool is JobTool.CAPTIONS]

    assert captions.measured_seconds_per_minute == pytest.approx(20.0, abs=1.0)


async def test_too_few_samples_is_not_reported_as_a_measurement(db: AsyncSession) -> None:
    """Two jobs on a developer's laptop are not a fleet measurement. Reporting a
    ratio from them would be worse than reporting nothing — it would look like
    evidence."""
    user = await _user(db)
    await _job(db, user, media_minutes=1.0, wall_seconds=600.0)

    [captions] = [cost for cost in await measure(db) if cost.tool is JobTool.CAPTIONS]

    assert captions.samples < MIN_SAMPLES
    assert captions.is_underpriced is False, "a single sample cannot condemn a price"


async def test_drift_is_flagged_when_the_fleet_disagrees_with_the_price(
    db: AsyncSession,
) -> None:
    """The whole point of the report.

    Captions are priced assuming 20 s per minute of media. A fleet consistently
    taking 40 means the allowance was sized against half the real cost — which
    at $3.99 is the difference between a plan and a loss.
    """
    user = await _user(db)
    for _ in range(MIN_SAMPLES):
        await _job(db, user, media_minutes=1.0, wall_seconds=40.0)

    [captions] = [cost for cost in await measure(db) if cost.tool is JobTool.CAPTIONS]

    assert captions.assumed_seconds_per_minute == 20.0
    assert captions.drift == pytest.approx(2.0, abs=0.05)
    assert captions.is_underpriced is True


async def test_a_price_the_fleet_beats_is_not_flagged(db: AsyncSession) -> None:
    user = await _user(db)
    for _ in range(MIN_SAMPLES):
        await _job(db, user, media_minutes=1.0, wall_seconds=10.0)

    [captions] = [cost for cost in await measure(db) if cost.tool is JobTool.CAPTIONS]
    assert captions.is_underpriced is False


async def test_jobs_from_before_the_column_existed_are_left_out(db: AsyncSession) -> None:
    """🔴 Not backfilled, and that is the point.

    A reconstructed duration would sit in this report looking exactly like a
    measurement, and this report exists to be trusted about one number.
    """
    user = await _user(db)
    for _ in range(MIN_SAMPLES):
        await _job(db, user, media_duration_ms=None)  # the column left NULL

    assert [cost for cost in await measure(db) if cost.tool is JobTool.CAPTIONS] == []


async def test_only_jobs_that_finished_are_counted(db: AsyncSession) -> None:
    """A failed job's wall clock says how long it took to fail, which is a
    different question. A cancelled one says nothing at all."""
    user = await _user(db)
    for _ in range(MIN_SAMPLES):
        await _job(db, user, status=JobStatus.FAILED, wall_seconds=600.0)

    assert [cost for cost in await measure(db) if cost.tool is JobTool.CAPTIONS] == []


async def test_the_worst_case_uses_the_costliest_tool_per_credit(db: AsyncSession) -> None:
    """A heavy user finds the expensive mix without trying — captioning long
    recordings is both the headline feature and the slowest thing phase 1 runs.
    Sizing a plan on the average tool is sizing it for the users who do not
    stress it."""
    user = await _user(db)
    for _ in range(MIN_SAMPLES):
        # 20 s per minute at 2 credits per minute → 10 s per credit.
        await _job(db, user, tool=JobTool.CAPTIONS, media_minutes=1.0, wall_seconds=20.0)
        # 3 s per minute at 1 credit per minute → 3 s per credit.
        await _job(db, user, tool=JobTool.COLOR_ANALYSIS, media_minutes=1.0, wall_seconds=3.0)

    costs = await measure(db)
    seconds = worst_case_seconds(800, costs)

    assert seconds == pytest.approx(8_000.0, rel=0.05), "the costliest tool per credit"


async def test_no_measurements_means_no_worst_case_rather_than_zero_risk(
    db: AsyncSession,
) -> None:
    """`0.0` here means "unknown", and the script says so in words rather than
    printing a reassuring number."""
    assert worst_case_seconds(800, []) == 0.0

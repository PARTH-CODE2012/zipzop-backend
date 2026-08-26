"""The pipeline sweep — proving each kind of stuck state gets exactly the
recovery `app/services/pipeline_reconciliation.py` claims for it, and nothing
else.

Every "acted on" test has a matching "left alone" test at the same status with
a fresher timestamp — the risk this module carries is a threshold that is too
eager and nudges a job or an upload that was never actually stuck, so the
negative case is as important as the positive one.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AssetKind,
    AssetStatus,
    Job,
    JobFamily,
    JobStatus,
    JobTool,
    MediaAsset,
    User,
)
from app.services.pipeline_reconciliation import (
    ABANDONED_UPLOAD_AFTER,
    STUCK_PROBING_AFTER,
    STUCK_QUEUED_AFTER,
    STUCK_RUNNING_AFTER,
    report_stuck_probing_assets,
    sweep,
    sweep_abandoned_uploads,
    sweep_stuck_queued_jobs,
    sweep_stuck_running_jobs,
)


@pytest.fixture(autouse=True)
def _capture_celery(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Same shape as `test_jobs.py`'s `_silence_celery`, but recording what was
    sent rather than discarding it — the whole point here is proving the sweep
    sent the right job ids, not merely that it did not crash."""
    from app.workers.tasks import analysis as task_module

    sent: list[str] = []
    monkeypatch.setattr(
        task_module.run_analysis,
        "apply_async",
        lambda args, **_kwargs: sent.append(args[0]),
    )
    return sent


async def _user(db: AsyncSession) -> User:
    user = User(email=f"{uuid.uuid4().hex[:12]}@example.com", hashed_password="x")
    db.add(user)
    await db.flush()
    return user


async def _job(
    db: AsyncSession, user: User, *, status: JobStatus, created_at: datetime, started_at: datetime | None
) -> Job:
    job = Job(
        user_id=user.id,
        tool=JobTool.COLOR_ANALYSIS,
        family=JobFamily.ANALYSIS,
        status=status,
        input={"assetId": "ast_0", "clipId": "clp_a"},
        credits_reserved=1,
        created_at=created_at,
        started_at=started_at,
    )
    db.add(job)
    await db.flush()
    return job


async def _asset(
    db: AsyncSession, user: User, *, status: AssetStatus, created_at: datetime
) -> MediaAsset:
    asset = MediaAsset(
        user_id=user.id,
        kind=AssetKind.VIDEO,
        status=status,
        storage_key=f"originals/{user.id}/source.mp4",
        original_filename="clip.mp4",
        created_at=created_at,
    )
    db.add(asset)
    await db.flush()
    return asset


NOW = datetime.now(UTC)


# --------------------------------------------------------------------------
# Stuck queued jobs — the Celery send that apparently never happened
# --------------------------------------------------------------------------


async def test_a_job_stuck_in_queued_gets_resent(
    db: AsyncSession, _capture_celery: list[str]
) -> None:
    user = await _user(db)
    job = await _job(
        db,
        user,
        status=JobStatus.QUEUED,
        created_at=NOW - STUCK_QUEUED_AFTER - timedelta(minutes=1),
        started_at=None,
    )

    found = await sweep_stuck_queued_jobs(db, now=NOW)

    assert found == [job.id]
    assert _capture_celery == [str(job.id)]


async def test_a_freshly_queued_job_is_left_alone(
    db: AsyncSession, _capture_celery: list[str]
) -> None:
    """The case a too-eager threshold would get wrong: a job legitimately
    waiting its turn must not be re-nudged."""
    user = await _user(db)
    await _job(
        db,
        user,
        status=JobStatus.QUEUED,
        created_at=NOW - timedelta(minutes=1),
        started_at=None,
    )

    found = await sweep_stuck_queued_jobs(db, now=NOW)

    assert found == []
    assert _capture_celery == []


async def test_a_running_job_is_not_touched_by_the_queued_sweep(
    db: AsyncSession, _capture_celery: list[str]
) -> None:
    """A job that has `started_at` set is somebody's business, not this one's
    — `sweep_stuck_running_jobs` covers it, on a different, longer threshold."""
    user = await _user(db)
    await _job(
        db,
        user,
        status=JobStatus.RUNNING,
        created_at=NOW - STUCK_QUEUED_AFTER - timedelta(minutes=1),
        started_at=NOW - timedelta(minutes=1),
    )

    assert await sweep_stuck_queued_jobs(db, now=NOW) == []
    assert _capture_celery == []


# --------------------------------------------------------------------------
# Stuck running jobs — the worker that disappeared without raising anything
# --------------------------------------------------------------------------


async def test_a_job_stuck_in_running_is_requeued_and_resent(
    db: AsyncSession, _capture_celery: list[str]
) -> None:
    user = await _user(db)
    job = await _job(
        db,
        user,
        status=JobStatus.RUNNING,
        created_at=NOW - timedelta(hours=1),
        started_at=NOW - STUCK_RUNNING_AFTER - timedelta(minutes=1),
    )

    found = await sweep_stuck_running_jobs(db, now=NOW)

    assert found == [job.id]
    assert _capture_celery == [str(job.id)]

    await db.refresh(job)
    # Back to `queued`, and cleared exactly the way `JobRepository.requeue`
    # clears a job for retry — a claim can only succeed against this shape.
    assert job.status is JobStatus.QUEUED
    assert job.started_at is None


async def test_a_job_that_only_just_started_is_left_running(
    db: AsyncSession, _capture_celery: list[str]
) -> None:
    user = await _user(db)
    job = await _job(
        db,
        user,
        status=JobStatus.RUNNING,
        created_at=NOW - timedelta(hours=1),
        started_at=NOW - timedelta(minutes=2),
    )

    assert await sweep_stuck_running_jobs(db, now=NOW) == []
    assert _capture_celery == []

    await db.refresh(job)
    assert job.status is JobStatus.RUNNING


# --------------------------------------------------------------------------
# Abandoned uploads — nobody is ever going to call /complete
# --------------------------------------------------------------------------


async def test_an_abandoned_upload_is_marked_failed(db: AsyncSession) -> None:
    user = await _user(db)
    asset = await _asset(
        db,
        user,
        status=AssetStatus.PENDING_UPLOAD,
        created_at=NOW - ABANDONED_UPLOAD_AFTER - timedelta(minutes=1),
    )

    found = await sweep_abandoned_uploads(db, now=NOW)

    assert found == [asset.id]
    await db.refresh(asset)
    assert asset.status is AssetStatus.FAILED
    assert asset.failure_reason


async def test_a_recent_pending_upload_is_left_alone(db: AsyncSession) -> None:
    """A slow upload on a bad connection must have room to actually finish."""
    user = await _user(db)
    asset = await _asset(
        db, user, status=AssetStatus.PENDING_UPLOAD, created_at=NOW - timedelta(minutes=5)
    )

    assert await sweep_abandoned_uploads(db, now=NOW) == []
    await db.refresh(asset)
    assert asset.status is AssetStatus.PENDING_UPLOAD


async def test_a_probing_asset_is_not_touched_by_the_upload_sweep(db: AsyncSession) -> None:
    """`sweep_abandoned_uploads` only ever matches `pending_upload` — a stuck
    `probing` asset is a different problem with a deliberately different,
    weaker response. See `report_stuck_probing_assets`."""
    user = await _user(db)
    asset = await _asset(
        db,
        user,
        status=AssetStatus.PROBING,
        created_at=NOW - ABANDONED_UPLOAD_AFTER - timedelta(minutes=1),
    )

    assert await sweep_abandoned_uploads(db, now=NOW) == []
    await db.refresh(asset)
    assert asset.status is AssetStatus.PROBING


# --------------------------------------------------------------------------
# Stuck probing assets — reported, never acted on
# --------------------------------------------------------------------------


async def test_a_stuck_probing_asset_is_reported_but_left_exactly_as_it_was(
    db: AsyncSession,
) -> None:
    """The safety property this module exists to prove: `MediaAsset` has no
    atomic claim the way `Job` does, so unlike every other sweep here, this one
    must never write to the row it flags — only name it."""
    user = await _user(db)
    asset = await _asset(
        db,
        user,
        status=AssetStatus.PROBING,
        created_at=NOW - STUCK_PROBING_AFTER - timedelta(minutes=1),
    )

    found = await report_stuck_probing_assets(db, now=NOW)

    assert found == [asset.id]
    await db.refresh(asset)
    assert asset.status is AssetStatus.PROBING  # untouched, not merely unchanged by accident


async def test_a_recently_probing_asset_is_not_reported(db: AsyncSession) -> None:
    user = await _user(db)
    await _asset(db, user, status=AssetStatus.PROBING, created_at=NOW - timedelta(minutes=2))

    assert await report_stuck_probing_assets(db, now=NOW) == []


# --------------------------------------------------------------------------
# The orchestrator
# --------------------------------------------------------------------------


async def test_sweep_runs_every_check_and_reports_each_kind(
    db: AsyncSession, _capture_celery: list[str]
) -> None:
    user = await _user(db)
    stuck_job = await _job(
        db,
        user,
        status=JobStatus.QUEUED,
        created_at=NOW - STUCK_QUEUED_AFTER - timedelta(hours=1),
        started_at=None,
    )
    stuck_upload = await _asset(
        db,
        user,
        status=AssetStatus.PENDING_UPLOAD,
        created_at=NOW - ABANDONED_UPLOAD_AFTER - timedelta(hours=1),
    )
    stuck_probing = await _asset(
        db,
        user,
        status=AssetStatus.PROBING,
        created_at=NOW - STUCK_PROBING_AFTER - timedelta(hours=1),
    )

    result = await sweep(db)

    assert stuck_job.id in result.requeued_jobs
    assert stuck_upload.id in result.failed_uploads
    assert stuck_probing.id in result.stuck_probing
    assert result.touched == 2  # the job and the upload; probing is report-only


async def test_one_failing_check_does_not_stop_the_others(
    db: AsyncSession, _capture_celery: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recovery job that gives up the first time anything goes wrong is the
    failure mode it exists to prevent, one level up.

    The first draft of `sweep()` claimed each check was isolated and simply ran
    them in sequence, so a lock timeout in the first would have silently
    skipped the other three.
    """
    import app.services.pipeline_reconciliation as module

    user = await _user(db)
    stuck_upload = await _asset(
        db,
        user,
        status=AssetStatus.PENDING_UPLOAD,
        created_at=NOW - ABANDONED_UPLOAD_AFTER - timedelta(hours=1),
    )

    async def _explode(*_args: object, **_kwargs: object) -> list[uuid.UUID]:
        raise RuntimeError("lock timeout")

    monkeypatch.setattr(module, "sweep_stuck_queued_jobs", _explode)

    result = await module.sweep(db)

    # The broken check contributed nothing, and the rest still ran.
    assert result.requeued_jobs == []
    assert stuck_upload.id in result.failed_uploads

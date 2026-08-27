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
    MAX_INGEST_ATTEMPTS,
    STUCK_PROBING_AFTER,
    STUCK_QUEUED_AFTER,
    STUCK_RUNNING_AFTER,
    UNCLAIMED_PROBING_AFTER,
    sweep,
    sweep_abandoned_uploads,
    sweep_stuck_probing_assets,
    sweep_stuck_queued_jobs,
    sweep_stuck_running_jobs,
    sweep_unclaimed_probing_assets,
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


@pytest.fixture(autouse=True)
def _capture_ingest(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The same, for the ingest queue.

    Kept as its own list rather than merged into `_capture_celery`: the two
    sweeps send to different queues, and a test that asserted "one message went
    somewhere" would pass if the asset recovery sent its message to the
    analysis worker.
    """
    from app.workers.tasks import ingest as task_module

    sent: list[str] = []
    monkeypatch.setattr(
        task_module.process_asset,
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
    db: AsyncSession,
    user: User,
    *,
    status: JobStatus,
    created_at: datetime,
    started_at: datetime | None,
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
    db: AsyncSession,
    user: User,
    *,
    status: AssetStatus,
    created_at: datetime,
    worker_id: str | None = None,
    ingest_started_at: datetime | None = None,
    ingest_attempts: int = 0,
) -> MediaAsset:
    asset = MediaAsset(
        user_id=user.id,
        kind=AssetKind.VIDEO,
        status=status,
        storage_key=f"originals/{user.id}/source.mp4",
        original_filename="clip.mp4",
        created_at=created_at,
        worker_id=worker_id,
        ingest_started_at=ingest_started_at,
        ingest_attempts=ingest_attempts,
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
    `probing` asset is a different problem with a different response, and
    failing one that a worker is actively transcoding would be the worse of the
    two mistakes. See `sweep_stuck_probing_assets`."""
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
# Stuck probing assets
#
# Reported and never acted on until 27 August, because `media_assets` had no
# atomic claim and re-sending could have put two workers on the same file.
# Migration 0003 gave it one, so these now assert a repair. The two shapes
# mirror the two job checks exactly: a message that never arrived, and a worker
# that died holding the claim.
# --------------------------------------------------------------------------


async def test_an_asset_whose_ingest_message_never_arrived_is_resent(
    db: AsyncSession, _capture_ingest: list[str]
) -> None:
    """`probing` with no claim on it at all: `complete_upload` committed and the
    Celery send did not happen."""
    user = await _user(db)
    asset = await _asset(
        db,
        user,
        status=AssetStatus.PROBING,
        created_at=NOW - UNCLAIMED_PROBING_AFTER - timedelta(minutes=1),
    )

    found = await sweep_unclaimed_probing_assets(db, now=NOW)

    assert found == [asset.id]
    assert _capture_ingest == [str(asset.id)]
    # Nothing written: there is no claim to release on a row nobody claimed,
    # and the message is the whole recovery.
    await db.refresh(asset)
    assert asset.status is AssetStatus.PROBING
    assert asset.worker_id is None


async def test_a_recently_uploaded_probing_asset_is_left_alone(
    db: AsyncSession, _capture_ingest: list[str]
) -> None:
    user = await _user(db)
    await _asset(db, user, status=AssetStatus.PROBING, created_at=NOW - timedelta(minutes=2))

    assert await sweep_unclaimed_probing_assets(db, now=NOW) == []
    assert _capture_ingest == []


async def test_an_asset_a_worker_is_holding_is_not_treated_as_unclaimed(
    db: AsyncSession, _capture_ingest: list[str]
) -> None:
    """The distinction migration 0003 exists to make.

    Old enough on `created_at` to look stuck to the pre-claim version of this
    check, but a worker took it thirty seconds ago and is transcoding it right
    now. Measuring from `created_at` conflated the two; `worker_id` and
    `ingest_started_at` separate them.
    """
    user = await _user(db)
    await _asset(
        db,
        user,
        status=AssetStatus.PROBING,
        created_at=NOW - UNCLAIMED_PROBING_AFTER - timedelta(hours=3),
        worker_id="worker-1",
        ingest_started_at=NOW - timedelta(seconds=30),
    )

    assert await sweep_unclaimed_probing_assets(db, now=NOW) == []
    assert await sweep_stuck_probing_assets(db, now=NOW) == ([], [])
    assert _capture_ingest == []


async def test_an_asset_whose_worker_died_is_released_and_resent(
    db: AsyncSession, _capture_ingest: list[str]
) -> None:
    user = await _user(db)
    asset = await _asset(
        db,
        user,
        status=AssetStatus.PROBING,
        created_at=NOW - timedelta(hours=2),
        worker_id="worker-that-died",
        ingest_started_at=NOW - STUCK_PROBING_AFTER - timedelta(minutes=1),
        ingest_attempts=1,
    )

    requeued, failed = await sweep_stuck_probing_assets(db, now=NOW)

    assert requeued == [asset.id]
    assert failed == []
    assert _capture_ingest == [str(asset.id)]

    await db.refresh(asset)
    assert asset.status is AssetStatus.PROBING
    # Released, so the next worker's `WHERE worker_id IS NULL` can match.
    assert asset.worker_id is None
    assert asset.ingest_started_at is None
    # And *not* reset — this is the count that stops the retrying eventually.
    assert asset.ingest_attempts == 1


async def test_an_asset_past_the_attempt_ceiling_is_failed_not_resent(
    db: AsyncSession, _capture_ingest: list[str]
) -> None:
    """A file that kills whatever picks it up costs a full download and
    transcode every five minutes for ever otherwise."""
    user = await _user(db)
    asset = await _asset(
        db,
        user,
        status=AssetStatus.PROBING,
        created_at=NOW - timedelta(hours=2),
        worker_id="worker-5",
        ingest_started_at=NOW - STUCK_PROBING_AFTER - timedelta(minutes=1),
        ingest_attempts=MAX_INGEST_ATTEMPTS,
    )

    requeued, failed = await sweep_stuck_probing_assets(db, now=NOW)

    assert requeued == []
    assert failed == [asset.id]
    assert _capture_ingest == []

    await db.refresh(asset)
    assert asset.status is AssetStatus.FAILED
    assert asset.failure_reason is not None


async def test_the_ceiling_also_stops_the_unclaimed_check_resending_for_ever(
    db: AsyncSession, _capture_ingest: list[str]
) -> None:
    """Otherwise an asset that is released, re-sent and dies again would bounce
    between the two checks and never reach the ceiling."""
    user = await _user(db)
    await _asset(
        db,
        user,
        status=AssetStatus.PROBING,
        created_at=NOW - UNCLAIMED_PROBING_AFTER - timedelta(hours=1),
        ingest_attempts=MAX_INGEST_ATTEMPTS,
    )

    assert await sweep_unclaimed_probing_assets(db, now=NOW) == []
    assert _capture_ingest == []


async def test_a_deleted_asset_is_never_resent(
    db: AsyncSession, _capture_ingest: list[str]
) -> None:
    """Spending a download and a transcode on a row the user has deleted is
    waste at best."""
    user = await _user(db)
    asset = await _asset(
        db,
        user,
        status=AssetStatus.PROBING,
        created_at=NOW - UNCLAIMED_PROBING_AFTER - timedelta(hours=1),
    )
    asset.deleted_at = NOW - timedelta(minutes=5)
    await db.flush()

    assert await sweep_unclaimed_probing_assets(db, now=NOW) == []
    assert _capture_ingest == []


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
    never_sent = await _asset(
        db,
        user,
        status=AssetStatus.PROBING,
        created_at=NOW - UNCLAIMED_PROBING_AFTER - timedelta(hours=1),
    )
    dead_worker = await _asset(
        db,
        user,
        status=AssetStatus.PROBING,
        created_at=NOW - timedelta(hours=2),
        worker_id="worker-that-died",
        ingest_started_at=NOW - STUCK_PROBING_AFTER - timedelta(hours=1),
    )
    exhausted = await _asset(
        db,
        user,
        status=AssetStatus.PROBING,
        created_at=NOW - timedelta(hours=2),
        worker_id="worker-5",
        ingest_started_at=NOW - STUCK_PROBING_AFTER - timedelta(hours=1),
        ingest_attempts=MAX_INGEST_ATTEMPTS,
    )

    result = await sweep(db)

    assert stuck_job.id in result.requeued_jobs
    assert stuck_upload.id in result.failed_uploads
    assert never_sent.id in result.requeued_assets
    assert dead_worker.id in result.requeued_assets
    assert exhausted.id in result.failed_assets
    # Every kind acted on now — `stuck_probing` was report-only until the claim
    # landed, and `touched` counted three of the five checks.
    assert result.touched == 5


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

    # Committed on purpose, and this is the whole subtlety of the test. The
    # guard rolls the session back when a check raises — which is correct, a
    # half-failed check leaves the session dirty for the next one. But the
    # other fixtures in this file only `flush()`, and a rollback would take an
    # unflushed row with it, so the sweep would find nothing and the test would
    # blame `_guarded` for the fixture's state. In production the rows a sweep
    # acts on were committed by other processes minutes or hours earlier;
    # `worker_session()` hands `sweep()` a session with no pending writes of
    # its own. Committing here is what makes the test model that.
    await db.commit()

    async def _explode(*_args: object, **_kwargs: object) -> list[uuid.UUID]:
        raise RuntimeError("lock timeout")

    monkeypatch.setattr(module, "sweep_stuck_queued_jobs", _explode)

    result = await module.sweep(db)

    # The broken check contributed nothing, and the rest still ran.
    assert result.requeued_jobs == []
    assert stuck_upload.id in result.failed_uploads

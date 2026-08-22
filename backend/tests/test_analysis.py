"""Colour analysis — the tool, and the pipeline around it.

**The tool M4 proves the pipeline with, because it depends on nothing.** No
transcription engine, no model download: sampled frames through ffmpeg and a
look chosen from what the numbers say. That makes it the one analysis tool
whose correctness can be asserted against a file this suite generates itself.

The pipeline's own properties — claim, progress, settle, refund on failure —
are exercised here against real media and a real database, because the failure
that matters is a job that charges the user and produces nothing.
"""

import pathlib
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AssetKind,
    AssetStatus,
    CreditBucket,
    CreditLedgerEntry,
    Job,
    JobFamily,
    JobStatus,
    JobTool,
    LedgerReason,
    MediaAsset,
    User,
)
from app.services import color_analysis
from app.services.analysis_pipeline import JobUnavailableError, run_analysis
from app.services.credits import Allocation, CreditLedger

pytestmark = pytest.mark.anyio


# --------------------------------------------------------------------------
# The tool
# --------------------------------------------------------------------------


def test_it_reads_a_real_file_and_returns_normalised_numbers(
    sample_video: pathlib.Path,
) -> None:
    stats = color_analysis.sample_frames(sample_video, 4_000)

    for value in (stats.luma, stats.red, stats.green, stats.blue, stats.spread):
        assert 0.0 <= value <= 1.0
    # testsrc2 is a bright, high-contrast pattern; anything near black would
    # mean the frames never decoded.
    assert stats.luma > 0.15


def test_a_file_with_no_video_track_is_a_permanent_failure(tmp_path: pathlib.Path) -> None:
    """Not an exception for the queue to retry: the same file analysed three
    times gives the same answer three times and only delays the message."""
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not media" * 100)

    with pytest.raises(color_analysis.AnalysisFailedError):
        color_analysis.sample_frames(broken, 4_000)


def test_a_zero_length_file_is_refused_before_ffmpeg_runs(
    sample_video: pathlib.Path,
) -> None:
    with pytest.raises(color_analysis.AnalysisFailedError):
        color_analysis.sample_frames(sample_video, 0)


def test_the_recommendation_names_a_look_we_actually_ship() -> None:
    """A LUT the browser cannot render is worse than no recommendation."""
    for luma in (0.1, 0.5, 0.9):
        for spread in (0.05, 0.3, 0.8):
            found = color_analysis.recommend(
                color_analysis.FrameStats(luma=luma, red=0.5, green=0.5, blue=0.5, spread=spread)
            )
            assert found["lut"] in color_analysis.LOOKS
            assert 0.25 <= found["strength"] <= 0.95
            assert all(alt["lut"] in color_analysis.LOOKS for alt in found["alternatives"])
            assert found["lut"] not in [alt["lut"] for alt in found["alternatives"]]


def test_flat_footage_takes_more_of_the_grade_than_punchy_footage() -> None:
    """A fixed strength would blow out picture that needed nothing, which is
    the failure that makes people turn the feature off."""
    flat = color_analysis.recommend(
        color_analysis.FrameStats(luma=0.5, red=0.4, green=0.5, blue=0.6, spread=0.05)
    )
    punchy = color_analysis.recommend(
        color_analysis.FrameStats(luma=0.5, red=0.4, green=0.5, blue=0.6, spread=0.9)
    )
    assert flat["strength"] > punchy["strength"]


def test_a_preferred_look_wins_outright() -> None:
    """A user who has chosen a look is not asking for an opinion."""
    found = color_analysis.recommend(
        color_analysis.FrameStats(luma=0.2, red=0.2, green=0.2, blue=0.4, spread=0.1),
        preferred_look="sun_kissed",
    )
    assert found["lut"] == "sun_kissed"


def test_an_unknown_preferred_look_falls_back_rather_than_failing() -> None:
    found = color_analysis.recommend(
        color_analysis.FrameStats(luma=0.5, red=0.5, green=0.5, blue=0.5, spread=0.3),
        preferred_look="not_a_real_lut",
    )
    assert found["lut"] in color_analysis.LOOKS


# --------------------------------------------------------------------------
# The pipeline
# --------------------------------------------------------------------------


async def _user_with_job(
    db: AsyncSession, *, credits: int = 10, key: str = "proxies/x/y/proxy.mp4"
) -> tuple[User, Job]:
    user = User(
        email=f"{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="x",
        plan_credits=300,
    )
    db.add(user)
    await db.flush()

    asset = MediaAsset(
        user_id=user.id,
        kind=AssetKind.VIDEO,
        status=AssetStatus.READY,
        storage_key=f"originals/{user.id}/source.mp4",
        proxy_key=key,
        original_filename="clip.mp4",
        mime_type="video/mp4",
        size_bytes=2048,
        duration_ms=4_000,
    )
    db.add(asset)
    await db.flush()

    job = Job(
        user_id=user.id,
        tool=JobTool.COLOR_ANALYSIS,
        family=JobFamily.ANALYSIS,
        status=JobStatus.QUEUED,
        input={"assetId": f"ast_{asset.id}", "clipId": "clp_a", "analyzedDurationMs": 4_000},
        credits_reserved=credits,
    )
    db.add(job)
    await db.flush()
    await CreditLedger(db).reserve(
        user=user, job_id=job.id, allocation=Allocation(plan=credits, topup=0)
    )
    return user, job


async def test_a_job_runs_end_to_end_and_settles(
    db: AsyncSession, s3: object, s3_prefix: str, sample_video: pathlib.Path
) -> None:
    from app.services import storage

    key = f"{s3_prefix}/proxy.mp4"
    storage.upload(str(sample_video), key, "video/mp4")
    user, job = await _user_with_job(db, key=key)

    status = await run_analysis(db, job.id, worker_id="test")

    assert status == JobStatus.SUCCEEDED.value
    await db.refresh(job)
    assert job.status is JobStatus.SUCCEEDED
    assert job.progress == 100
    # The reservation *is* the charge — nothing moves on success.
    assert job.credits_settled == job.credits_reserved
    assert user.plan_credits == 290
    assert job.result is not None
    assert job.result["lut"] in color_analysis.LOOKS


async def test_unreadable_media_fails_the_job_and_refunds_it(
    db: AsyncSession, s3: object, s3_prefix: str, not_a_video: pathlib.Path
) -> None:
    """*"A failure on our side never costs the user anything, automatically,
    without anyone contacting support."*"""
    from app.services import storage

    key = f"{s3_prefix}/broken.mp4"
    storage.upload(str(not_a_video), key, "video/mp4")
    user, job = await _user_with_job(db, key=key)
    assert user.plan_credits == 290

    status = await run_analysis(db, job.id, worker_id="test")

    assert status == JobStatus.FAILED.value
    await db.refresh(job)
    assert job.status is JobStatus.FAILED
    assert job.error_code == "UNSUPPORTED_MEDIA"
    assert job.error_message  # a sentence the user can read
    assert user.plan_credits == 300

    refunds = (
        (
            await db.execute(
                sa.select(CreditLedgerEntry).where(
                    CreditLedgerEntry.job_id == job.id,
                    CreditLedgerEntry.reason == LedgerReason.REFUND,
                )
            )
        )
        .scalars()
        .all()
    )
    assert [(r.bucket, r.delta) for r in refunds] == [(CreditBucket.PLAN, 10)]


async def test_a_job_already_claimed_is_left_alone(
    db: AsyncSession, s3: object, s3_prefix: str, sample_video: pathlib.Path
) -> None:
    """A redelivered message must not start a second run of work already
    under way."""
    from app.services import storage

    key = f"{s3_prefix}/proxy.mp4"
    storage.upload(str(sample_video), key, "video/mp4")
    _, job = await _user_with_job(db, key=key)
    await run_analysis(db, job.id, worker_id="first")

    with pytest.raises(JobUnavailableError):
        await run_analysis(db, job.id, worker_id="second")

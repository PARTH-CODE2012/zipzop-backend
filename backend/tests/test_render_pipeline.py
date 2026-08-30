"""An export, end to end: a real timeline, real media, a real bucket.

**This is the milestone's closing condition, minus the frame comparison.** The
graph tests prove FFmpeg accepts the command; these prove the job pipeline
around it does the right things — claims, renders, uploads, creates the output
asset, settles the credits, and refunds when it cannot.

The pipeline is driven directly rather than through Celery, for the same reason
`test_analysis.py` does: what is under test is the work and the settlement, and
a broker in the way only makes that harder to state.
"""

import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import ids
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
    Project,
    User,
)
from app.services import storage
from app.services.credits import Allocation, CreditLedger
from app.services.render_pipeline import RenderFailedError, run_export

pytestmark = [pytest.mark.anyio, pytest.mark.ffmpeg, pytest.mark.storage]


async def _fixture(
    db: AsyncSession,
    s3: Any,
    s3_prefix: str,
    sample_video: Path,
    *,
    timeline: dict[str, Any] | None = None,
    credits: int = 4,
    resolution: str = "720p",
) -> tuple[User, Project, Job, MediaAsset]:
    user = User(email=f"{uuid.uuid4().hex[:12]}@example.com", hashed_password="x", plan_credits=300)
    db.add(user)
    await db.flush()

    key = f"{s3_prefix}/original.mp4"
    storage.upload(str(sample_video), key, "video/mp4")
    asset = MediaAsset(
        user_id=user.id,
        kind=AssetKind.VIDEO,
        status=AssetStatus.READY,
        storage_key=key,
        original_filename="clip.mp4",
        mime_type="video/mp4",
        size_bytes=sample_video.stat().st_size,
        duration_ms=4000,
    )
    db.add(asset)
    await db.flush()

    public_asset_id = ids.encode(ids.ASSET, asset.id)
    document = timeline or {
        "schemaVersion": 1,
        "tracks": [
            {
                "id": "trk_v",
                "kind": "video",
                "index": 0,
                "clips": [
                    {
                        "id": "clp_1",
                        "assetId": public_asset_id,
                        "startMs": 0,
                        "durationMs": 1000,
                        "sourceInMs": 0,
                    }
                ],
            }
        ],
    }

    project = Project(
        user_id=user.id,
        title="Export",
        version=1,
        duration_ms=1000,
        fps=24,
        timeline=document,
    )
    db.add(project)
    await db.flush()

    job = Job(
        user_id=user.id,
        project_id=project.id,
        tool=JobTool.EXPORT,
        family=JobFamily.RENDER,
        status=JobStatus.QUEUED,
        input={
            "timelineVersion": 1,
            "preset": {
                "resolution": resolution,
                "aspectRatio": "9:16",
                "quality": "draft",
                "format": "mp4",
            },
            "analysedDurationMs": 1000,
        },
        credits_reserved=credits,
    )
    db.add(job)
    await db.flush()
    await CreditLedger(db).reserve(
        user=user, job_id=job.id, allocation=Allocation(plan=credits, topup=0)
    )
    await db.commit()
    return user, project, job, asset


async def test_an_export_runs_end_to_end_and_produces_a_downloadable_file(
    db: AsyncSession, s3: Any, s3_prefix: str, sample_video: Path
) -> None:
    """The milestone, in one test: a timeline goes in and an MP4 comes out of
    the bucket, with a row pointing at it."""
    _, _, job, _ = await _fixture(db, s3, s3_prefix, sample_video)

    status = await run_export(db, job.id, worker_id="test")

    assert status == JobStatus.SUCCEEDED.value
    await db.refresh(job)
    assert job.status is JobStatus.SUCCEEDED
    assert job.progress == 100
    assert job.output_asset_id is not None

    output = await db.get(MediaAsset, job.output_asset_id)
    assert output is not None
    # Keyed by job, under `exports/` — the prefix the 30-day lifecycle rule
    # will be attached to.
    assert output.storage_key == f"exports/{job.user_id}/{job.id}/final.mp4"
    assert output.status is AssetStatus.READY
    # 720 * 9/16 is 405, and H.264 needs an even width, so 404.
    assert (output.width, output.height) == (404, 720)
    # In the bucket, not merely on the row. A key written while the upload
    # failed is the failure `finish_ingest` was built to avoid.
    stored = storage.head(output.storage_key)
    assert stored is not None and stored.size_bytes > 0

    assert job.result is not None
    assert job.result["watermarked"] is True  # no subscription means free terms


async def test_the_export_becomes_an_asset_derived_from_its_job(
    db: AsyncSession, s3: Any, s3_prefix: str, sample_video: Path
) -> None:
    """So it shows up in the media bin, counts against storage and can be
    re-edited — the same treatment every other derived file gets."""
    _, _, job, _ = await _fixture(db, s3, s3_prefix, sample_video)

    await run_export(db, job.id, worker_id="test")

    await db.refresh(job)
    output = await db.get(MediaAsset, job.output_asset_id)
    assert output is not None
    assert output.derived_by_job_id == job.id
    assert output.user_id == job.user_id


async def test_a_successful_export_settles_rather_than_refunding(
    db: AsyncSession, s3: Any, s3_prefix: str, sample_video: Path
) -> None:
    """The reservation *is* the charge — nothing should move in the ledger on
    success, and a refund row would mean the render was free."""
    _, _, job, _ = await _fixture(db, s3, s3_prefix, sample_video)

    await run_export(db, job.id, worker_id="test")

    refunds = await db.scalar(
        sa.select(sa.func.count())
        .select_from(CreditLedgerEntry)
        .where(CreditLedgerEntry.job_id == job.id, CreditLedgerEntry.reason == LedgerReason.REFUND)
    )
    assert refunds == 0


async def test_a_second_worker_cannot_claim_a_running_export(
    db: AsyncSession, s3: Any, s3_prefix: str, sample_video: Path
) -> None:
    """The same atomic claim every other job uses. Two workers rendering one
    timeline would charge once and upload twice, racing the same key."""
    from app.services.analysis_pipeline import JobUnavailableError

    _, _, job, _ = await _fixture(db, s3, s3_prefix, sample_video)
    await run_export(db, job.id, worker_id="first")

    with pytest.raises(JobUnavailableError):
        await run_export(db, job.id, worker_id="second")


async def test_a_timeline_referencing_missing_media_fails_and_refunds(
    db: AsyncSession, s3: Any, s3_prefix: str, sample_video: Path
) -> None:
    """A failure on our side never costs the user anything — §5.4."""
    timeline = {
        "schemaVersion": 1,
        "tracks": [
            {
                "id": "trk_v",
                "kind": "video",
                "index": 0,
                "clips": [
                    {
                        "id": "clp_1",
                        "assetId": ids.encode(ids.ASSET, uuid.uuid4()),
                        "startMs": 0,
                        "durationMs": 1000,
                        "sourceInMs": 0,
                    }
                ],
            }
        ],
    }
    user, _, job, _ = await _fixture(db, s3, s3_prefix, sample_video, timeline=timeline)

    status = await run_export(db, job.id, worker_id="test")

    assert status == JobStatus.FAILED.value
    await db.refresh(job)
    assert job.error_code == "RENDER_FAILED"

    refunded = await db.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(CreditLedgerEntry.delta), 0)).where(
            CreditLedgerEntry.job_id == job.id,
            CreditLedgerEntry.reason == LedgerReason.REFUND,
        )
    )
    assert refunded == 4
    await db.refresh(user)


async def test_an_empty_timeline_fails_rather_than_rendering_black(
    db: AsyncSession, s3: Any, s3_prefix: str, sample_video: Path
) -> None:
    timeline = {"schemaVersion": 1, "tracks": []}
    _, _, job, _ = await _fixture(db, s3, s3_prefix, sample_video, timeline=timeline)

    assert await run_export(db, job.id, worker_id="test") == JobStatus.FAILED.value


async def test_a_paid_plan_gets_no_watermark(
    db: AsyncSession, s3: Any, s3_prefix: str, sample_video: Path
) -> None:
    """Decided from the plan and never from the input — contract §6.2 is
    explicit that the client cannot ask for it to be left off, so an input
    field it could set would be exactly that."""
    from datetime import UTC, datetime, timedelta

    from app.models import PlanCode, Subscription, SubStatus

    user, _, job, _ = await _fixture(db, s3, s3_prefix, sample_video)
    now = datetime.now(UTC)
    db.add(
        Subscription(
            user_id=user.id,
            plan=PlanCode.PRO,
            status=SubStatus.ACTIVE,
            current_period_start=now - timedelta(days=1),
            current_period_end=now + timedelta(days=29),
        )
    )
    await db.commit()

    await run_export(db, job.id, worker_id="test")

    await db.refresh(job)
    assert job.result is not None
    assert job.result["watermarked"] is False


async def test_captions_reach_the_rendered_file(
    db: AsyncSession, s3: Any, s3_prefix: str, sample_video: Path
) -> None:
    """A text track in the document becomes an ASS script and a `subtitles`
    filter. This asserts the render survives it — the shaping itself is proved
    in `test_render_text.py`, where it can be measured."""
    timeline: dict[str, Any] = {
        "schemaVersion": 1,
        "tracks": [
            {
                "id": "trk_v",
                "kind": "video",
                "index": 0,
                "clips": [
                    {
                        "id": "clp_1",
                        "assetId": "PLACEHOLDER",
                        "startMs": 0,
                        "durationMs": 1000,
                        "sourceInMs": 0,
                    }
                ],
            },
            {
                "id": "trk_t",
                "kind": "text",
                "index": 0,
                "clips": [
                    {
                        "id": "clp_cap",
                        "kind": "caption",
                        "startMs": 0,
                        "durationMs": 900,
                        # Hindi, on purpose: the product's stated edge, and the
                        # path that would silently draw boxes.
                        "text": "हिन्दी",
                        "styleId": "kinetic_bold",
                        "position": {"x": 0.5, "y": 0.8, "anchor": "center"},
                    }
                ],
            },
        ],
    }
    _, _, job, asset = await _fixture(db, s3, s3_prefix, sample_video, timeline=timeline)
    # The fixture cannot know the asset id before it creates it.
    project = await db.get(Project, job.project_id)
    assert project is not None
    document = dict(project.timeline)
    document["tracks"][0]["clips"][0]["assetId"] = ids.encode(ids.ASSET, asset.id)
    project.timeline = document
    await db.commit()

    assert await run_export(db, job.id, worker_id="test") == JobStatus.SUCCEEDED.value


async def test_a_render_failure_is_permanent_and_not_retried_forever(
    db: AsyncSession, s3: Any, s3_prefix: str, sample_video: Path
) -> None:
    """`RenderFailedError` is deliberately not `TransientFailureError`: a
    retry costs a full transcode of the whole timeline, and a filtergraph
    FFmpeg refuses will be refused three times."""
    from app.services.analysis_pipeline import TransientFailureError

    assert not issubclass(RenderFailedError, TransientFailureError)


async def test_the_reserved_credits_are_recorded_against_the_job(
    db: AsyncSession, s3: Any, s3_prefix: str, sample_video: Path
) -> None:
    _, _, job, _ = await _fixture(db, s3, s3_prefix, sample_video, credits=6)

    await run_export(db, job.id, worker_id="test")

    reserved = await db.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(-CreditLedgerEntry.delta), 0)).where(
            CreditLedgerEntry.job_id == job.id,
            CreditLedgerEntry.reason == LedgerReason.RESERVE,
            CreditLedgerEntry.bucket == CreditBucket.PLAN,
        )
    )
    assert reserved == 6

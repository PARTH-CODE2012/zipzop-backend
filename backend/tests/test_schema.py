"""The schema's promises, tested against the migrated database.

Every assertion here corresponds to a sentence in
docs/03-backend-architecture.md §4.2 or §5.5. They are worth testing because
each one is a constraint the *database* enforces: if a migration is edited and
one of them quietly stops existing, no application test would notice — the
application would simply start being able to do something the design forbids.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CreditBucket,
    CreditLedgerEntry,
    JobFamily,
    JobStatus,
    JobTool,
    LedgerReason,
    MediaAsset,
    Plan,
    PlanCode,
    Project,
    Subscription,
    SubStatus,
    User,
)
from app.models.enums import AssetKind, AssetStatus
from app.models.job import Job
from app.models.project import ProjectAsset


async def _user(db: AsyncSession, email: str | None = None) -> User:
    user = User(
        email=email or f"{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="not-a-real-hash",
        display_name="Test",
    )
    db.add(user)
    await db.flush()
    return user


# --------------------------------------------------------------------------
# The plan catalogue
# --------------------------------------------------------------------------


async def test_the_four_plans_are_seeded_with_the_documented_values(db: AsyncSession) -> None:
    """docs/03-backend-architecture.md §5.5, exactly.

    Hard-coded rather than read from the migration, so that editing the
    migration's numbers by accident fails here instead of silently repricing
    every account.
    """
    rows = (await db.execute(sa.select(Plan).order_by(Plan.queue_priority))).scalars().all()
    got = {
        p.code: (
            p.monthly_credits,
            p.facemap_seconds,
            p.fair_use_credits,
            p.max_export_height,
            p.watermark.value,
            p.queue_priority,
            p.price_usd_cents,
            p.price_inr_paise,
        )
        for p in rows
    }
    assert got == {
        PlanCode.FREE: (300, 0, None, 720, "forced", 0, None, None),
        PlanCode.PRO: (2500, 300, None, 1080, "none", 10, 1999, 99900),
        PlanCode.BUSINESS: (8000, 1200, None, 2160, "none", 20, 4999, 199900),
        PlanCode.STUDIO: (30000, 3600, 30000, 2160, "custom", 30, 9999, 299900),
    }


async def test_money_is_stored_in_minor_units(db: AsyncSession) -> None:
    """$19.99 is 1999, not 19.99. A float here is a rounding bug in the making."""
    pro = await db.get(Plan, PlanCode.PRO)
    assert pro is not None
    assert pro.price_usd_cents == 1999
    assert isinstance(pro.price_usd_cents, int)


# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------


async def test_email_is_case_insensitively_unique(db: AsyncSession) -> None:
    """CITEXT: Sam@example.com and sam@example.com are one account, not two."""
    await _user(db, "Sam@Example.com")
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await _user(db, "sam@example.com")


@pytest.mark.parametrize("column", ["plan_credits", "topup_credits", "facemap_seconds"])
async def test_a_credit_balance_cannot_go_negative(db: AsyncSession, column: str) -> None:
    """The CHECK is the last line of defence behind the allocation logic.

    A negative balance means credits were spent that were never granted, which
    is the one accounting error nobody would notice from the interface.
    """
    user = await _user(db)
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(sa.update(User).where(User.id == user.id).values(**{column: -1}))


async def test_total_credits_is_plan_plus_topup_and_excludes_facemap(db: AsyncSession) -> None:
    """facemap is a separate meter — only face mapping and lip sync spend it,
    so it must not appear in the number the interface shows as spendable."""
    user = await _user(db)
    user.plan_credits = 300
    user.topup_credits = 50
    user.facemap_seconds = 240
    assert user.total_credits == 350


# --------------------------------------------------------------------------
# credit_ledger
# --------------------------------------------------------------------------


async def _job(db: AsyncSession, user: User) -> Job:
    job = Job(
        user_id=user.id,
        tool=JobTool.CAPTIONS,
        family=JobFamily.ANALYSIS,
        status=JobStatus.QUEUED,
        input={},
    )
    db.add(job)
    await db.flush()
    return job


async def test_one_refund_per_job_per_bucket(db: AsyncSession) -> None:
    """The guard that makes a double refund impossible even if a worker retries
    its completion handler (docs/03 §4.2)."""
    user = await _user(db)
    job = await _job(db, user)

    db.add(
        CreditLedgerEntry(
            user_id=user.id,
            bucket=CreditBucket.PLAN,
            delta=10,
            reason=LedgerReason.REFUND,
            job_id=job.id,
            balance_after=10,
        )
    )
    await db.flush()

    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(
                CreditLedgerEntry(
                    user_id=user.id,
                    bucket=CreditBucket.PLAN,
                    delta=10,
                    reason=LedgerReason.REFUND,
                    job_id=job.id,
                    balance_after=20,
                )
            )
            await db.flush()


async def test_a_job_may_refund_two_buckets(db: AsyncSession) -> None:
    """The bucket is part of the key precisely so a job that drew from plan and
    topup can return to both. Without the bucket in the index this is what
    would be wrongly rejected."""
    user = await _user(db)
    job = await _job(db, user)

    for bucket in (CreditBucket.PLAN, CreditBucket.TOPUP):
        db.add(
            CreditLedgerEntry(
                user_id=user.id,
                bucket=bucket,
                delta=5,
                reason=LedgerReason.REFUND,
                job_id=job.id,
                balance_after=5,
            )
        )
    await db.flush()

    count = await db.scalar(
        sa.select(sa.func.count())
        .select_from(CreditLedgerEntry)
        .where(CreditLedgerEntry.job_id == job.id)
    )
    assert count == 2


async def test_a_ledger_row_never_records_nothing(db: AsyncSession) -> None:
    user = await _user(db)
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(
                CreditLedgerEntry(
                    user_id=user.id,
                    bucket=CreditBucket.PLAN,
                    delta=0,
                    reason=LedgerReason.ADMIN_ADJUST,
                    balance_after=0,
                )
            )
            await db.flush()


async def test_signup_grants_without_a_job_are_not_constrained(db: AsyncSession) -> None:
    """The unique index is partial on `job_id IS NOT NULL`. Two signup grants
    with no job must both be insertable — otherwise the second registration on
    a machine fails for no reason."""
    a = await _user(db)
    b = await _user(db)
    for user in (a, b):
        db.add(
            CreditLedgerEntry(
                user_id=user.id,
                bucket=CreditBucket.PLAN,
                delta=300,
                reason=LedgerReason.SIGNUP_GRANT,
                balance_after=300,
            )
        )
    await db.flush()


# --------------------------------------------------------------------------
# subscriptions
# --------------------------------------------------------------------------


async def _subscription(db: AsyncSession, user: User, status: SubStatus) -> Subscription:
    now = datetime.now(UTC)
    sub = Subscription(
        user_id=user.id,
        plan=PlanCode.FREE,
        status=status,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
    )
    db.add(sub)
    await db.flush()
    return sub


async def test_only_one_live_subscription_per_user(db: AsyncSession) -> None:
    """What stops someone holding a Stripe and a Razorpay plan at once."""
    user = await _user(db)
    await _subscription(db, user, SubStatus.ACTIVE)
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await _subscription(db, user, SubStatus.PAST_DUE)


async def test_a_cancelled_subscription_does_not_block_a_new_one(db: AsyncSession) -> None:
    """The index is partial on active/past_due. Someone who cancels and
    resubscribes must not be locked out by their own history."""
    user = await _user(db)
    await _subscription(db, user, SubStatus.CANCELLED)
    await _subscription(db, user, SubStatus.EXPIRED)
    await _subscription(db, user, SubStatus.ACTIVE)


# --------------------------------------------------------------------------
# project_assets
# --------------------------------------------------------------------------


async def test_an_asset_in_use_by_a_project_cannot_be_deleted(db: AsyncSession) -> None:
    """ON DELETE RESTRICT. The API turns this into 409 ASSET_IN_USE rather than
    letting a timeline end up pointing at nothing."""
    user = await _user(db)
    asset = MediaAsset(
        user_id=user.id,
        kind=AssetKind.VIDEO,
        status=AssetStatus.READY,
        storage_key="originals/x/y/source.mp4",
    )
    project = Project(user_id=user.id, title="P")
    db.add_all([asset, project])
    await db.flush()
    db.add(ProjectAsset(project_id=project.id, asset_id=asset.id))
    await db.flush()

    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(sa.delete(MediaAsset).where(MediaAsset.id == asset.id))


async def test_deleting_a_project_releases_its_assets(db: AsyncSession) -> None:
    """CASCADE on the project side: removing a project must not leave rows
    behind that keep an asset undeletable forever."""
    user = await _user(db)
    asset = MediaAsset(
        user_id=user.id,
        kind=AssetKind.VIDEO,
        status=AssetStatus.READY,
        storage_key="originals/x/y/source.mp4",
    )
    project = Project(user_id=user.id, title="P")
    db.add_all([asset, project])
    await db.flush()
    db.add(ProjectAsset(project_id=project.id, asset_id=asset.id))
    await db.flush()

    await db.execute(sa.delete(Project).where(Project.id == project.id))
    await db.flush()

    remaining = await db.scalar(
        sa.select(sa.func.count())
        .select_from(ProjectAsset)
        .where(ProjectAsset.asset_id == asset.id)
    )
    assert remaining == 0


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------


async def test_progress_is_a_percentage(db: AsyncSession) -> None:
    user = await _user(db)
    job = await _job(db, user)
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(sa.update(Job).where(Job.id == job.id).values(progress=101))


async def test_an_idempotency_key_cannot_be_replayed_into_a_second_job(
    db: AsyncSession,
) -> None:
    """Replaying a key must return the original job rather than charge twice
    (contract §1). The database refuses the second insert; the API turns that
    into a replay."""
    user = await _user(db)
    for _ in range(2):
        db.add(
            Job(
                user_id=user.id,
                tool=JobTool.CAPTIONS,
                family=JobFamily.ANALYSIS,
                input={},
                idempotency_key="the-same-key",
            )
        )
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_two_jobs_without_an_idempotency_key_are_allowed(db: AsyncSession) -> None:
    """Partial index again: NULL keys must not collide with each other."""
    user = await _user(db)
    await _job(db, user)
    await _job(db, user)


# --------------------------------------------------------------------------
# media_assets
# --------------------------------------------------------------------------


async def test_ready_requires_all_four_derivatives_for_video(db: AsyncSession) -> None:
    """docs/03 §6.2: the asset becomes ready only when probe, proxy, thumbnail
    and peaks all exist."""
    user = await _user(db)
    asset = MediaAsset(
        user_id=user.id,
        kind=AssetKind.VIDEO,
        status=AssetStatus.PROBING,
        storage_key="originals/x/y/source.mp4",
        duration_ms=4000,
    )
    db.add(asset)
    await db.flush()

    assert asset.has_all_derivatives is False
    asset.proxy_key = "proxies/x/y/proxy.mp4"
    assert asset.has_all_derivatives is False
    asset.thumbnail_key = "thumbs/x/y/thumb.jpg"
    assert asset.has_all_derivatives is False
    asset.peaks_key = "peaks/x/y/peaks.json"
    assert asset.has_all_derivatives is True


async def test_audio_is_ready_without_a_proxy_or_thumbnail(db: AsyncSession) -> None:
    """An audio asset has no picture. Demanding four outputs would leave every
    music upload stuck in `probing` forever."""
    user = await _user(db)
    asset = MediaAsset(
        user_id=user.id,
        kind=AssetKind.AUDIO,
        status=AssetStatus.PROBING,
        storage_key="originals/x/y/source.mp3",
        duration_ms=4000,
        peaks_key="peaks/x/y/peaks.json",
    )
    db.add(asset)
    await db.flush()
    assert asset.has_all_derivatives is True


async def test_fps_survives_the_round_trip_as_a_decimal(db: AsyncSession) -> None:
    """29.97 is NUMERIC(7,3), not a float. Stored as a float it comes back as
    29.969999999999999 and the export renderer inherits the drift."""
    from decimal import Decimal

    user = await _user(db)
    asset = MediaAsset(
        user_id=user.id,
        kind=AssetKind.VIDEO,
        status=AssetStatus.READY,
        storage_key="originals/x/y/source.mp4",
        fps=Decimal("29.970"),
    )
    db.add(asset)
    await db.flush()
    await db.refresh(asset)
    assert asset.fps == Decimal("29.970")
    assert str(asset.fps) == "29.970"

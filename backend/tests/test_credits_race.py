"""The ledger under real concurrency — docs/07-security.md §6.6.

*"Fire 20 concurrent `POST /jobs` against a balance of one. The ledger must
never go negative, and exactly one job may be created."*

**Why this file exists at all.** `test_credits.py` has always described "the
concurrency property (two jobs against a balance that covers one)", and nothing
in it exercised two transactions at once: the `client` fixture runs every
request inside one savepoint on one connection, so requests cannot race. That
is the structural blind spot `docs/21-m6-notes.md` §2.2 names. The tests here
use the same way out as the webhook test that found it — an app whose sessions
are shaped the way production shapes them, one real connection each, committed
for real and cleaned up by hand.
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import ids
from app.db import get_session
from app.main import create_app
from app.models import (
    AssetKind,
    AssetStatus,
    CreditBucket,
    CreditLedgerEntry,
    Job,
    JobTool,
    LedgerReason,
    MediaAsset,
    User,
)
from app.services import pricing
from app.services.credits import CreditLedger

V1 = "/v1"
CONCURRENT = 20
ASSET_DURATION_MS = 60_000


@pytest.fixture
async def production_app(
    engine: Any, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[tuple[AsyncClient, Any], None]:
    """An app whose every request gets its own connection and a real commit.

    The Celery send is stubbed — what is under test is the transaction that
    reserves the credits, not the broker — and the rate limiter is left alone:
    twenty requests is well inside it.
    """
    from app.api.routes import jobs as jobs_route

    monkeypatch.setattr(jobs_route, "_enqueue", lambda job: None)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    app = create_app()

    async def _production_shaped_session() -> AsyncGenerator[AsyncSession, None]:
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _production_shaped_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, maker


async def _funded_account(ac: AsyncClient, maker: Any, *, jobs_affordable: int) -> Any:
    """A registered account holding exactly `jobs_affordable` captions jobs'
    worth of credits, and one ready asset to run them on.

    The balance is set through the ledger, not by writing the column, so the
    reconciliation at the end of each test is a real check rather than a
    comparison of two numbers this helper wrote.
    """
    registered = (
        await ac.post(
            f"{V1}/auth/register",
            json={"email": f"{uuid.uuid4().hex[:12]}@example.com", "password": "hunter2hunter2"},
        )
    ).json()
    user_id = uuid.UUID(registered["user"]["id"].removeprefix("usr_"))
    headers = {"Authorization": f"Bearer {registered['accessToken']}"}

    cost = pricing.cost_credits(JobTool.CAPTIONS, ASSET_DURATION_MS)
    async with maker() as setup:
        ledger = CreditLedger(setup)
        user = await ledger.lock_user(user_id)
        assert user is not None
        target = cost * jobs_affordable
        await ledger._write(
            user=user,
            bucket=CreditBucket.PLAN,
            delta=target - user.plan_credits,
            reason=LedgerReason.ADMIN_GRANT,
            job_id=None,
            note="M7 race test: fund exactly N jobs",
        )
        asset = MediaAsset(
            user_id=user_id,
            kind=AssetKind.VIDEO,
            status=AssetStatus.READY,
            storage_key=f"originals/{user_id}/{uuid.uuid4()}/source.mp4",
            proxy_key=f"proxies/{user_id}/{uuid.uuid4()}/proxy.mp4",
            thumbnail_key=f"thumbs/{user_id}/{uuid.uuid4()}/thumb.jpg",
            peaks_key=f"peaks/{user_id}/{uuid.uuid4()}/peaks.json",
            original_filename="clip.mp4",
            mime_type="video/mp4",
            size_bytes=1024,
            duration_ms=ASSET_DURATION_MS,
        )
        setup.add(asset)
        await setup.commit()
        asset_id = ids.encode(ids.ASSET, asset.id)
    return user_id, headers, asset_id, cost


async def _cleanup(maker: Any, user_id: uuid.UUID) -> None:
    """Remove what the test committed, retrying a lost deadlock.

    A request that failed can still be tearing its transaction down when the
    test reaches this point, and a DELETE racing that teardown is chosen as the
    deadlock victim. Without the retry, that error replaces the assertion that
    actually failed in the report — which is how the first run of this file
    pointed at the cleanup instead of at the 500s before it.
    """
    for attempt in range(5):
        try:
            async with maker() as cleanup:
                await cleanup.execute(
                    sa.delete(CreditLedgerEntry).where(CreditLedgerEntry.user_id == user_id)
                )
                await cleanup.execute(sa.delete(Job).where(Job.user_id == user_id))
                await cleanup.execute(sa.delete(MediaAsset).where(MediaAsset.user_id == user_id))
                await cleanup.execute(sa.delete(User).where(User.id == user_id))
                await cleanup.commit()
            return
        except sa.exc.DBAPIError:
            if attempt == 4:
                raise
            await asyncio.sleep(0.5)


async def _reconcile(maker: Any, user_id: uuid.UUID) -> tuple[int, int]:
    """The cached balance, and what the ledger says it should be."""
    async with maker() as check:
        user = await check.get(User, user_id)
        assert user is not None
        ledger_sum = await check.scalar(
            sa.select(sa.func.coalesce(sa.func.sum(CreditLedgerEntry.delta), 0)).where(
                CreditLedgerEntry.user_id == user_id,
                CreditLedgerEntry.bucket == CreditBucket.PLAN,
            )
        )
        return user.plan_credits, int(ledger_sum or 0)


async def test_twenty_concurrent_jobs_against_a_balance_of_one(
    production_app: tuple[AsyncClient, Any],
) -> None:
    """Exactly one job, and a ledger that never goes below zero.

    Without the `SELECT … FOR UPDATE` in `create_job` every one of these reads
    the same balance, decides it can pay, and spends it.
    """
    ac, maker = production_app
    user_id, headers, asset_id, cost = await _funded_account(ac, maker, jobs_affordable=1)
    try:
        body = {"tool": "captions", "input": {"assetId": asset_id}}
        responses = await asyncio.gather(
            *(ac.post(f"{V1}/jobs", headers=headers, json=body) for _ in range(CONCURRENT))
        )
        statuses = sorted(r.status_code for r in responses)
        balance, ledger_sum = await _reconcile(maker, user_id)

        assert cost > 0
        assert statuses.count(202) == 1, (statuses, {"cost": cost, "balance": balance})
        assert statuses.count(402) == CONCURRENT - 1, statuses

        assert balance == 0
        assert balance == ledger_sum, "the cached balance and the ledger disagree"
        async with maker() as check:
            created = await check.scalar(
                sa.select(sa.func.count()).select_from(Job).where(Job.user_id == user_id)
            )
        assert created == 1
    finally:
        await _cleanup(maker, user_id)


async def test_one_idempotency_key_sent_twenty_times_at_once_is_one_job(
    production_app: tuple[AsyncClient, Any],
) -> None:
    """A retry storm is what idempotency keys are *for* — a client that timed
    out and resent, concurrently, the same request.

    The balance covers many jobs here, so the credit check cannot be what
    stops the duplicates: only the key can. Every response must describe the
    same single job, and exactly one reservation may exist.
    """
    ac, maker = production_app
    user_id, headers, asset_id, cost = await _funded_account(ac, maker, jobs_affordable=50)
    try:
        body = {"tool": "captions", "input": {"assetId": asset_id}}
        keyed = {**headers, "Idempotency-Key": uuid.uuid4().hex}
        responses = await asyncio.gather(
            *(ac.post(f"{V1}/jobs", headers=keyed, json=body) for _ in range(CONCURRENT))
        )

        assert all(r.status_code == 202 for r in responses), sorted(
            (r.status_code, r.text[:120]) for r in responses
        )
        assert len({r.json()["id"] for r in responses}) == 1

        balance, ledger_sum = await _reconcile(maker, user_id)
        assert balance == cost * 49, "charged more than once for one job"
        assert balance == ledger_sum
    finally:
        await _cleanup(maker, user_id)
